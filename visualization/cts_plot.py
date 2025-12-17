"""
visualization/cts_plot.py

Clean, minimal CTS tree visualization.

Requirement from Phase 3:
  "CTS Tree Plot: Generate a plot of the DFFs (leaves) and chosen buffers (nodes),
   with lines connecting them to show the synthesized tree structure overlaid on 
   the chip layout."

This module:
  1. Parses DEF file for ALL cell positions (fabric layout)
  2. Parses clock_tree.json for CTS structure
  3. Resolves DFF positions from DEF (since CTS tree only has names)
  4. Draws: fabric background → tree edges → DFFs → buffers
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches


def parse_def_components(def_path: str) -> Dict[str, Tuple[float, float, str]]:
    """
    Parse DEF file COMPONENTS section.
    
    Returns:
        Dict mapping instance_name -> (x_um, y_um, cell_type)
        Coordinates are converted from DEF units (nm) to microns.
    """
    components = {}
    in_components = False
    
    with open(def_path, 'r') as f:
        for line in f:
            line = line.strip()
            
            if line.startswith('COMPONENTS'):
                in_components = True
                continue
            
            if in_components and line.startswith('END COMPONENTS'):
                break
            
            if in_components and line.startswith('-'):
                # Format: - inst_name cell_type + FIXED ( x y ) orient ;
                # Example: - $flatten\CPU.$auto$ff... sky130_fd_sc_hd__dfbbp_1 + FIXED ( 489440 76160 ) N ;
                match = re.match(r'-\s+(\S+)\s+(\S+)\s+\+\s+FIXED\s+\(\s*(\d+)\s+(\d+)\s*\)', line)
                if match:
                    inst = match.group(1)
                    cell_type = match.group(2)
                    x_nm = int(match.group(3))
                    y_nm = int(match.group(4))
                    # DEF uses UNITS DISTANCE MICRONS 1000, so divide by 1000
                    x_um = x_nm / 1000.0
                    y_um = y_nm / 1000.0
                    components[inst] = (x_um, y_um, cell_type)
    
    return components


def parse_def_diearea(def_path: str) -> Tuple[float, float, float, float]:
    """Parse DIE AREA from DEF file. Returns (x0, y0, x1, y1) in microns."""
    with open(def_path, 'r') as f:
        for line in f:
            if 'DIEAREA' in line:
                # DIEAREA ( 0 0 ) ( 1003600 989200 ) ;
                match = re.search(r'DIEAREA\s+\(\s*(\d+)\s+(\d+)\s*\)\s+\(\s*(\d+)\s+(\d+)\s*\)', line)
                if match:
                    return (
                        int(match.group(1)) / 1000.0,
                        int(match.group(2)) / 1000.0,
                        int(match.group(3)) / 1000.0,
                        int(match.group(4)) / 1000.0
                    )
    return (0, 0, 1000, 1000)  # Default


def extract_tree_data(clock_tree: Dict) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """
    Extract buffers and edges from CTS tree.
    
    Returns:
        (buffers, edges) where:
        - buffers: list of {name, x, y, depth}
        - edges: list of (parent_buffer, child_name) - child can be buffer or DFF
    """
    buffers = []
    edges = []  # (parent_buffer_name, child_name)
    
    def traverse(node, parent_buffer=None, depth=0):
        buffer_name = node.get('buffer')
        buffer_pos = node.get('buffer_pos')
        
        if buffer_name and buffer_pos:
            buffers.append({
                'name': buffer_name,
                'x': float(buffer_pos[0]),
                'y': float(buffer_pos[1]),
                'depth': depth
            })
            
            # Edge from parent to this buffer
            if parent_buffer:
                edges.append((parent_buffer, buffer_name))
            
            # Edges to sink DFFs
            for sink in node.get('sink_logical_names', []):
                edges.append((buffer_name, sink))
            
            # Recurse to children
            for child in node.get('children', []):
                traverse(child, buffer_name, depth + 1)
        else:
            # No buffer at this level, pass through
            for child in node.get('children', []):
                traverse(child, parent_buffer, depth)
    
    traverse(clock_tree)
    return buffers, edges


def plot_cts_tree(
    def_path: str,
    clock_tree_path: str,
    out_png: str = "cts_tree.png",
    figsize: Tuple[int, int] = (16, 16),
    show_fabric: bool = True,
    show_unused_dffs: bool = False,
    dpi: int = 150
) -> plt.Figure:
    """
    Generate CTS tree visualization overlaid on chip layout.
    
    Args:
        def_path: Path to fixed DEF file (contains all cell placements)
        clock_tree_path: Path to clock_tree.json
        out_png: Output PNG path
        figsize: Figure size
        show_fabric: Whether to show all fabric cells as background
        show_unused_dffs: Whether to show DFFs not in CTS tree
        dpi: Output DPI
    
    Returns:
        matplotlib Figure
    """
    # 1. Parse DEF for cell positions
    print(f"Parsing DEF: {def_path}")
    components = parse_def_components(def_path)
    die_bbox = parse_def_diearea(def_path)
    print(f"  Found {len(components)} components")
    print(f"  Die area: ({die_bbox[0]:.1f}, {die_bbox[1]:.1f}) to ({die_bbox[2]:.1f}, {die_bbox[3]:.1f}) µm")
    
    # 2. Parse CTS tree
    print(f"Parsing CTS tree: {clock_tree_path}")
    with open(clock_tree_path, 'r') as f:
        clock_tree = json.load(f)
    
    buffers, edges = extract_tree_data(clock_tree)
    print(f"  Found {len(buffers)} CTS buffers")
    print(f"  Found {len(edges)} tree edges")
    
    # 3. Resolve positions for all nodes
    # Buffers already have positions from CTS tree
    buffer_positions = {b['name']: (b['x'], b['y']) for b in buffers}
    
    # For DFFs (sink nodes), look up in DEF components
    sink_names = set()
    for parent, child in edges:
        if child not in buffer_positions:
            sink_names.add(child)
    
    sink_positions = {}
    missing_sinks = []
    for sink in sink_names:
        if sink in components:
            sink_positions[sink] = (components[sink][0], components[sink][1])
        else:
            missing_sinks.append(sink)
    
    print(f"  Resolved {len(sink_positions)} DFF positions from DEF")
    if missing_sinks:
        print(f"  Warning: {len(missing_sinks)} DFFs not found in DEF")
    
    # 4. Create figure
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')
    
    # 5. Draw die boundary
    dx0, dy0, dx1, dy1 = die_bbox
    ax.add_patch(patches.Rectangle(
        (dx0, dy0), dx1 - dx0, dy1 - dy0,
        fill=False, lw=2, edgecolor='navy', label='Die'
    ))
    
    # 6. Draw fabric cells as background (optional)
    if show_fabric:
        # Group by cell type for coloring
        type_colors = {}
        cmap = matplotlib.colormaps.get_cmap('tab20')
        color_idx = 0
        
        for inst, (x, y, cell_type) in components.items():
            # Skip cells in CTS tree (we'll draw them specially)
            if inst in buffer_positions or inst in sink_positions:
                continue
            
            # Get color for cell type
            base_type = cell_type.split('__')[-1] if '__' in cell_type else cell_type
            base_type = re.sub(r'_\d+$', '', base_type)  # Remove size suffix
            
            if base_type not in type_colors:
                type_colors[base_type] = cmap(color_idx % 20)
                color_idx += 1
            
            # Draw small rectangle (approximate cell size)
            # Most sky130 cells are ~0.46-2.3 µm wide, 2.72 µm tall
            w, h = 1.0, 2.72
            ax.add_patch(patches.Rectangle(
                (x, y), w, h,
                facecolor=type_colors[base_type],
                edgecolor='none',
                alpha=0.15,
                zorder=0
            ))
    
    # 7. Draw tree edges
    for parent, child in edges:
        if parent not in buffer_positions:
            continue
        
        px, py = buffer_positions[parent]
        
        if child in buffer_positions:
            # Buffer to buffer edge
            cx, cy = buffer_positions[child]
            ax.plot([px, cx], [py, cy], 
                   color='purple', linewidth=1.5, alpha=0.8, zorder=2)
        elif child in sink_positions:
            # Buffer to DFF edge
            cx, cy = sink_positions[child]
            ax.plot([px, cx], [py, cy],
                   color='orange', linewidth=0.8, alpha=0.6, 
                   linestyle=':', zorder=1)
    
    # 8. Draw DFFs (sinks) as red circles
    if sink_positions:
        dff_xs = [p[0] for p in sink_positions.values()]
        dff_ys = [p[1] for p in sink_positions.values()]
        ax.scatter(dff_xs, dff_ys, 
                  s=60, c='red', marker='o',
                  edgecolors='darkred', linewidths=0.5,
                  label=f'DFFs ({len(sink_positions)})',
                  zorder=5)
    
    # 9. Draw buffers as cyan squares (larger, on top)
    if buffers:
        buf_xs = [b['x'] for b in buffers]
        buf_ys = [b['y'] for b in buffers]
        ax.scatter(buf_xs, buf_ys,
                  s=120, c='cyan', marker='s',
                  edgecolors='blue', linewidths=1,
                  label=f'CTS Buffers ({len(buffers)})',
                  zorder=6)
        
        # Mark root buffer specially
        root_buf = buffers[0] if buffers else None
        if root_buf:
            ax.scatter([root_buf['x']], [root_buf['y']],
                      s=200, c='yellow', marker='*',
                      edgecolors='black', linewidths=1,
                      label='Root Buffer',
                      zorder=7)
    
    # 10. Configure plot
    ax.set_xlim(dx0 - 10, dx1 + 10)
    ax.set_ylim(dy0 - 10, dy1 + 10)
    ax.set_xlabel('X (µm)')
    ax.set_ylabel('Y (µm)')
    ax.set_title(f'CTS Tree Overlay\n{len(buffers)} buffers, {len(sink_positions)} DFFs')
    ax.legend(loc='upper right', fontsize=9)
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    # 11. Save
    fig.savefig(out_png, dpi=dpi, bbox_inches='tight')
    print(f"Saved: {out_png}")
    
    return fig


def main():
    """CLI entry point."""
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python cts_plot.py <def_file> <clock_tree.json> [output.png]")
        print("Example: python cts_plot.py build/6502/6502_fixed.def build/6502/6502_clock_tree.json")
        sys.exit(1)
    
    def_path = sys.argv[1]
    tree_path = sys.argv[2]
    out_png = sys.argv[3] if len(sys.argv) > 3 else "cts_tree.png"
    
    plot_cts_tree(def_path, tree_path, out_png)


if __name__ == '__main__':
    main()

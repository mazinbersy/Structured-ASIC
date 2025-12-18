"""
visualization/cts_plot.py

Clean CTS tree visualization - overlays CTS tree on the same layout as plot_layout.

Requirement from Phase 3:
  "CTS Tree Plot: Generate a plot of the DFFs (leaves) and chosen buffers (nodes),
   with lines connecting them to show the synthesized tree structure overlaid on 
   the chip layout."

This module:
  1. Draws the fabric layout (reusing helpers from stages.py)
  2. Parses clock_tree.json for CTS structure
  3. Resolves DFF positions from DEF file
  4. Overlays: tree edges → DFFs → buffers on the layout
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Reuse helpers from stages
from .stages import (
    normalize_cells_by_tile,
    extract_die_core_bbox,
    extract_cell_type,
    collect_pin_list,
)
from .config import VizConfig, load_fabric_db


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


def extract_tree_data(clock_tree: Dict) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """
    Extract buffers and edges from CTS tree.
    
    Returns:
        (buffers, edges) where:
        - buffers: list of {name, x, y, depth}
        - edges: list of (parent_buffer, child_name) - child can be buffer or DFF
    """
    buffers = []
    edges = []
    
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
            
            if parent_buffer:
                edges.append((parent_buffer, buffer_name))
            
            for sink in node.get('sink_logical_names', []):
                edges.append((buffer_name, sink))
            
            for child in node.get('children', []):
                traverse(child, buffer_name, depth + 1)
        else:
            for child in node.get('children', []):
                traverse(child, parent_buffer, depth)
    
    traverse(clock_tree)
    return buffers, edges


def plot_cts_tree(
    def_path: str,
    clock_tree_path: str,
    fabric_db: Dict[str, Any],
    out_png: str = "cts_tree.png",
    figsize: Tuple[int, int] = (14, 10),
    dpi: int = 150,
    alpha: float = 0.5,
    cmap_name: str = 'tab20'
) -> plt.Figure:
    """
    Generate CTS tree visualization overlaid on chip layout.
    
    Uses the same fabric layout rendering as plot_layout() for consistency.
    
    Args:
        def_path: Path to fixed DEF file (for DFF position resolution)
        clock_tree_path: Path to clock_tree.json
        fabric_db: Fabric database dict (same as used by plot_layout)
        out_png: Output PNG path
        figsize: Figure size
        dpi: Output DPI
        alpha: Alpha for fabric cells (same as plot_layout default)
        cmap_name: Colormap name
    
    Returns:
        matplotlib Figure
    """
    # ═══════════════════════════════════════════════════════════════════════════
    # 1. Draw fabric layout (reuse same code as plot_layout)
    # ═══════════════════════════════════════════════════════════════════════════
    
    pins = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_bbox, core_bbox = extract_die_core_bbox(fabric_db)
    
    cell_entries = list(normalize_cells_by_tile(fabric_db))
    if not cell_entries:
        raise ValueError("No fabric cells found in fabric_db")
    
    # Auto-compute die bbox if missing
    if die_bbox is None:
        xs, ys = [], []
        for _, cx, cy, w, h, _ in cell_entries:
            if cx is not None and cy is not None:
                xs.append(cx)
                ys.append(cy)
                if w: xs.append(cx + w)
                if h: ys.append(cy + h)
        if xs and ys:
            margin = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys))
            die_bbox = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
        else:
            die_bbox = (0, 0, 100, 100)
    
    if core_bbox is None:
        dx, dy = die_bbox[2] - die_bbox[0], die_bbox[3] - die_bbox[1]
        core_bbox = (die_bbox[0] + 0.08*dx, die_bbox[1] + 0.08*dy,
                     die_bbox[2] - 0.08*dx, die_bbox[3] - 0.08*dy)
    
    # Build type → color index
    type_to_idx = {}
    for _, _, _, _, _, cell in cell_entries:
        t = extract_cell_type(cell)
        if t not in type_to_idx:
            type_to_idx[t] = len(type_to_idx)
    
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')
    
    # Die and core rectangles
    ax.add_patch(patches.Rectangle(
        (die_bbox[0], die_bbox[1]),
        die_bbox[2] - die_bbox[0], die_bbox[3] - die_bbox[1],
        fill=False, lw=2, edgecolor='blue', zorder=1
    ))
    ax.add_patch(patches.Rectangle(
        (core_bbox[0], core_bbox[1]),
        core_bbox[2] - core_bbox[0], core_bbox[3] - core_bbox[1],
        fill=False, lw=2, ls='--', edgecolor='green', zorder=1
    ))
    
    cmap = matplotlib.colormaps.get_cmap(cmap_name)
    
    # Draw fabric cells (same as plot_layout)
    print(f"Drawing {len(cell_entries)} fabric cells...")
    for tile_name, cx, cy, w, h, cell in cell_entries:
        t = extract_cell_type(cell)
        idx = type_to_idx.get(t, 0)
        if w is None or h is None:
            w, h = 1.0, 1.0
        if cx is None or cy is None:
            cx, cy = 0.0, 0.0
        color = cmap(idx % cmap.N)
        ax.add_patch(patches.Rectangle(
            (cx, cy), w, h,
            facecolor=color, edgecolor='black', lw=0.1, alpha=alpha, zorder=0
        ))
    
    # Draw pins (green triangles to differentiate from DFFs)
    pin_list = collect_pin_list(pins)
    if pin_list:
        ax.scatter([p["x"] for p in pin_list], [p["y"] for p in pin_list],
                   s=25, marker='^', color='limegreen', edgecolors='darkgreen',
                   linewidths=0.5, zorder=2, label=f'Pins ({len(pin_list)})')
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 2. Parse CTS tree and DEF for positions
    # ═══════════════════════════════════════════════════════════════════════════
    
    print(f"Parsing DEF: {def_path}")
    components = parse_def_components(def_path)
    print(f"  Found {len(components)} components in DEF")
    
    print(f"Parsing CTS tree: {clock_tree_path}")
    with open(clock_tree_path, 'r') as f:
        clock_tree = json.load(f)
    
    buffers, edges = extract_tree_data(clock_tree)
    print(f"  Found {len(buffers)} CTS buffers")
    print(f"  Found {len(edges)} tree edges")
    
    # Buffers have positions from CTS tree
    buffer_positions = {b['name']: (b['x'], b['y']) for b in buffers}
    
    # Resolve DFF positions from DEF
    sink_names = set()
    for parent, child in edges:
        if child not in buffer_positions:
            sink_names.add(child)
    
    sink_positions = {}
    for sink in sink_names:
        if sink in components:
            sink_positions[sink] = (components[sink][0], components[sink][1])
    
    print(f"  Resolved {len(sink_positions)}/{len(sink_names)} DFF positions from DEF")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 3. Draw ALL DFF fabric slots (used + unused) as faint markers
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Collect all DFF positions from fabric
    all_dff_slots = []
    used_dff_positions = set((p[0], p[1]) for p in sink_positions.values())
    
    for tile_name, cx, cy, w, h, cell in cell_entries:
        cell_type = cell.get('cell_type', '')
        if 'dfbbp' in cell_type.lower() or 'dff' in cell_type.lower():
            if cx is not None and cy is not None:
                # Check if this is an unused slot
                is_used = (cx, cy) in used_dff_positions
                all_dff_slots.append((cx, cy, is_used))
    
    # Draw unused DFF slots (same style as used DFFs but gray)
    unused_dffs = [(x, y) for x, y, used in all_dff_slots if not used]
    if unused_dffs:
        ax.scatter([p[0] for p in unused_dffs], [p[1] for p in unused_dffs],
                  s=40, c='lightgray', marker='o', alpha=0.7,
                  edgecolors='gray', linewidths=0.5, zorder=4,
                  label=f'Unused DFF slots ({len(unused_dffs)})')
    
    print(f"  Total DFF fabric slots: {len(all_dff_slots)} ({len(unused_dffs)} unused)")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 4. Draw CTS tree overlay
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Draw tree edges
    for parent, child in edges:
        if parent not in buffer_positions:
            continue
        
        px, py = buffer_positions[parent]
        
        if child in buffer_positions:
            # Buffer to buffer edge (purple, solid)
            cx, cy = buffer_positions[child]
            ax.plot([px, cx], [py, cy], 
                   color='purple', linewidth=1.2, alpha=0.7, zorder=3)
        elif child in sink_positions:
            # Buffer to DFF edge (orange, dotted)
            cx, cy = sink_positions[child]
            ax.plot([px, cx], [py, cy],
                   color='orange', linewidth=0.6, alpha=0.5, 
                   linestyle=':', zorder=3)
    
    # Draw DFFs (sinks) as red circles
    if sink_positions:
        dff_xs = [p[0] for p in sink_positions.values()]
        dff_ys = [p[1] for p in sink_positions.values()]
        ax.scatter(dff_xs, dff_ys, 
                  s=50, c='red', marker='o',
                  edgecolors='darkred', linewidths=0.5,
                  label=f'DFFs ({len(sink_positions)})',
                  zorder=5)
    
    # Draw buffers as cyan squares
    if buffers:
        buf_xs = [b['x'] for b in buffers]
        buf_ys = [b['y'] for b in buffers]
        ax.scatter(buf_xs, buf_ys,
                  s=80, c='cyan', marker='s',
                  edgecolors='blue', linewidths=0.8,
                  label=f'CTS Buffers ({len(buffers)})',
                  zorder=6)
        
        # Mark root buffer with star
        root_buf = buffers[0] if buffers else None
        if root_buf:
            ax.scatter([root_buf['x']], [root_buf['y']],
                      s=150, c='yellow', marker='*',
                      edgecolors='black', linewidths=1,
                      label='Root Buffer',
                      zorder=7)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 4. Configure plot (same style as plot_layout)
    # ═══════════════════════════════════════════════════════════════════════════
    
    ax.set_xlim(die_bbox[0] - 0.05*(die_bbox[2]-die_bbox[0]), 
                die_bbox[2] + 0.05*(die_bbox[2]-die_bbox[0]))
    ax.set_ylim(die_bbox[1] - 0.05*(die_bbox[3]-die_bbox[1]), 
                die_bbox[3] + 0.05*(die_bbox[3]-die_bbox[1]))
    ax.set_xlabel("X (μm)")
    ax.set_ylabel("Y (μm)")
    
    # Extract design name from path
    design = Path(clock_tree_path).stem.replace('_clock_tree', '')
    ax.set_title(f"{design} CTS Tree Overlay\n{len(buffers)} buffers, {len(sink_positions)} DFFs")
    
    # Place legend outside the plot to avoid overlap
    ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=8, framealpha=0.9)
    ax.grid(True, lw=0.3, alpha=0.5)
    
    fig.savefig(out_png, dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved: {out_png}")
    
    return fig


def main():
    """CLI entry point."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m visualization.cts_plot <def_file> <clock_tree.json> [output.png]")
        print("       python -m visualization.cts_plot <design_name>")
        print()
        print("Examples:")
        print("  python -m visualization.cts_plot build/6502/6502_fixed.def build/6502/6502_clock_tree.json")
        print("  python -m visualization.cts_plot 6502")
        sys.exit(1)
    
    # Check if single arg (design name) or full paths
    if len(sys.argv) == 2 or (len(sys.argv) >= 2 and not sys.argv[1].endswith('.def')):
        # Design name mode
        design = sys.argv[1]
        def_path = f"build/{design}/{design}_fixed.def"
        tree_path = f"build/{design}/{design}_clock_tree.json"
        out_png = sys.argv[2] if len(sys.argv) > 2 else f"build/{design}/{design}_cts_tree.png"
    else:
        # Full paths mode
        def_path = sys.argv[1]
        tree_path = sys.argv[2]
        out_png = sys.argv[3] if len(sys.argv) > 3 else "cts_tree.png"
    
    # Load fabric_db
    with open("fabric/fabric_db.json", 'r') as f:
        fabric_db = json.load(f)
    
    plot_cts_tree(def_path, tree_path, fabric_db, out_png)


if __name__ == '__main__':
    main()

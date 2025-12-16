"""
visualization/cts_overlay.py

CTS tree visualization: overlays DFFs, buffers, and tree edges on the chip layout.

Functions:
  - plot_cts_tree_overlay(logical_db, placement_file, clock_tree_json, fabric_db, out_png)
"""

import json
import math
from typing import Dict, Any, Optional, List, Tuple
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D


def plot_cts_tree_overlay(
    logical_db: Dict[str, Any],
    placement_file: str,
    clock_tree_json: str,
    fabric_db: Dict[str, Any],
    out_png: str = "cts_tree_overlay.png",
    figsize: Tuple[int, int] = (14, 10),
    dpi: int = 150
):
    """
    Visualize the CTS tree: DFFs (leaves), buffers (nodes), and tree edges on the layout.

    Args:
        logical_db: Logical database with cells and nets
        placement_file: Path to placement.map
        clock_tree_json: Path to clock_tree.json (from cts_htree.py)
        fabric_db: Fabric database (for die/core bounds)
        out_png: Output PNG file path
        figsize: Figure size tuple (width, height)
        dpi: DPI for saving
    """
    # Load placement mapping (updated after CTS, includes new buffers)
    placement = {}
    with open(placement_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or '->' not in line:
                continue
            parts = line.split('->')
            if len(parts) < 2:
                continue
            left = parts[0].strip().split()
            if len(left) < 4:
                continue
            try:
                x, y = float(left[2]), float(left[3])
                instance = parts[1].strip()
                placement[instance] = (x, y)
            except (ValueError, IndexError):
                continue

    # Build fabric cell position lookup from fabric_db (for cells not in placement)
    # This includes unused FFs added from fabric_db
    fabric_positions = {}
    try:
        cells_by_tile = fabric_db.get('fabric', {}).get('cells_by_tile', {})
        for tile_name, tile in cells_by_tile.items():
            for cell in tile.get('cells', []) or []:
                name = cell.get('name')
                if not name:
                    continue
                x = cell.get('x')
                y = cell.get('y')
                if x is not None and y is not None:
                    fabric_positions[name] = (float(x), float(y))
    except Exception as e:
        print(f"Warning: Error loading fabric positions: {e}")
        fabric_positions = {}

    print(f"Loaded {len(placement)} cells from placement file")
    print(f"Loaded {len(fabric_positions)} cells from fabric_db")

    # Load clock tree structure
    with open(clock_tree_json, 'r') as f:
        clock_tree = json.load(f)

    # Get die/core bounds from fabric_db
    pin_placement = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_info = pin_placement.get("die", {})
    core_info = pin_placement.get("core", {})

    die_width = die_info.get("width_um", 100.0)
    die_height = die_info.get("height_um", 100.0)
    core_margin = die_info.get("core_margin_um", 5.0)

    die_bbox = (0.0, 0.0, die_width, die_height)
    core_x0 = (die_width - core_info.get("width_um", die_width - 20)) / 2.0
    core_y0 = (die_height - core_info.get("height_um", die_height - 20)) / 2.0
    core_bbox = (core_x0, core_y0, core_x0 + core_info.get("width_um", die_width - 20), core_y0 + core_info.get("height_um", die_height - 20))

    # Collect DFFs and buffers with proper coordinate resolution
    dffs = []
    buffers = []
    missing_coords = []

    def traverse_tree(node):
        """Traverse tree and collect all FFs and buffers with their coordinates."""
        if not node:
            return
        
        buffer_name = node.get("buffer")
        buffer_type = node.get("buffer_type", "")
        
        # Get buffer coordinates: placement first (for both mapped and CTS-created buffers),
        # then fabric_db for physical cells
        if buffer_name:
            bx, by = None, None
            
            # Try placement file first (updated by CTS with new buffers)
            if buffer_name in placement:
                bx, by = placement[buffer_name]
            # Then try fabric positions (physical cell coordinates)
            elif buffer_name in fabric_positions:
                bx, by = fabric_positions[buffer_name]
            # Last resort: use tree metadata (centroid/computed positions)
            else:
                bp = node.get('buffer_pos') or node.get('centroid')
                if bp:
                    bx, by = float(bp[0]), float(bp[1])
            
            if bx is not None and by is not None:
                buffers.append({
                    "name": buffer_name,
                    "type": buffer_type,
                    "x": bx,
                    "y": by,
                    "level": node.get("level", 0)
                })
            else:
                missing_coords.append(('buffer', buffer_name))

        # Collect sinks (DFFs)
        if "sink_logical_names" in node:
            for sink_name in node["sink_logical_names"]:
                sx, sy = None, None
                
                # Try placement first (mapped DFFs)
                if sink_name in placement:
                    sx, sy = placement[sink_name]
                # Then try fabric positions (unused DFFs from fabric_db)
                elif sink_name in fabric_positions:
                    sx, sy = fabric_positions[sink_name]
                
                if sx is not None and sy is not None:
                    dffs.append({
                        "name": sink_name,
                        "x": sx,
                        "y": sy
                    })
                else:
                    missing_coords.append(('sink', sink_name))

        # Recurse on children
        for child in node.get("children", []):
            traverse_tree(child)

    traverse_tree(clock_tree)
    
    if missing_coords:
        print(f"\nWarning: Could not find coordinates for {len(missing_coords)} cells:")
        for cell_type, cell_name in missing_coords[:10]:  # Show first 10
            print(f"  {cell_type}: {cell_name}")
        if len(missing_coords) > 10:
            print(f"  ... and {len(missing_coords) - 10} more")

    # Create figure
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')

    # Draw die and core
    dx0, dy0, dx1, dy1 = die_bbox
    cx0, cy0, cx1, cy1 = core_bbox

    ax.add_patch(patches.Rectangle((dx0, dy0), dx1 - dx0, dy1 - dy0, fill=False, lw=2, edgecolor='blue', label="Die"))
    ax.add_patch(patches.Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False, lw=2, ls='--', edgecolor='green', label="Core"))

    # Draw tree edges (buffer to buffer, buffer to DFF)
    def draw_edges(node, parent_x=None, parent_y=None):
        """Draw connections between tree nodes."""
        if not node:
            return

        buffer_name = node.get("buffer")
        bx, by = None, None
        
        if buffer_name:
            # Resolve buffer coordinate using same priority as traverse_tree
            if buffer_name in placement:
                bx, by = placement[buffer_name]
            elif buffer_name in fabric_positions:
                bx, by = fabric_positions[buffer_name]
            else:
                bp = node.get('buffer_pos') or node.get('centroid')
                if bp:
                    bx, by = float(bp[0]), float(bp[1])

        if bx is not None and by is not None:
            # Draw edge from parent buffer
            if parent_x is not None and parent_y is not None:
                ax.plot([parent_x, bx], [parent_y, by], color="purple", linewidth=1.5, alpha=0.7, zorder=1)

            # Draw edges to children
            for child in node.get("children", []):
                draw_edges(child, bx, by)

            # Draw edges to sinks (DFFs)
            if "sink_logical_names" in node:
                for sink_name in node["sink_logical_names"]:
                    sx, sy = None, None
                    if sink_name in placement:
                        sx, sy = placement[sink_name]
                    elif sink_name in fabric_positions:
                        sx, sy = fabric_positions[sink_name]
                    
                    if sx is not None and sy is not None and bx is not None and by is not None:
                        ax.plot([bx, sx], [by, sy], color="orange", linewidth=1, alpha=0.6, linestyle=":", zorder=1)

    # Draw from root
    draw_edges(clock_tree)

    # Draw DFFs (red circles)
    if dffs:
        dff_xs = [d["x"] for d in dffs]
        dff_ys = [d["y"] for d in dffs]
        ax.scatter(dff_xs, dff_ys, s=80, color="red", marker="o", label=f"DFFs (sinks) ({len(dffs)})", zorder=5, edgecolors="darkred", linewidths=1)

    # Draw buffers (blue squares)
    if buffers:
        buf_xs = [b["x"] for b in buffers]
        buf_ys = [b["y"] for b in buffers]
        ax.scatter(buf_xs, buf_ys, s=100, color="cyan", marker="s", label=f"Buffers (nodes) ({len(buffers)})", zorder=4, edgecolors="blue", linewidths=1)

    # Labels
    ax.set_xlabel("X (µm)")
    ax.set_ylabel("Y (µm)")
    ax.set_title(f"CTS Tree Overlay: {len(dffs)} DFFs, {len(buffers)} Buffers (color-coded by level)")
    ax.grid(True, lw=0.3, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8, ncol=2)

    # Set bounds
    ax.set_xlim(die_bbox[0] - 10, die_bbox[2] + 10)
    ax.set_ylim(die_bbox[1] - 10, die_bbox[3] + 10)

    plt.tight_layout()
    plt.savefig(out_png, dpi=dpi, bbox_inches='tight')
    print(f"Saved CTS tree overlay to {out_png}")
    plt.close()


def plot_cts_tree_overlay_from_tree(
    clock_tree_json: Dict[str, Any],
    fabric_db: Dict[str, Any],
    out_png: str = "cts_tree_overlay.png",
    figsize: Tuple[int, int] = (14, 10),
    dpi: int = 150
):
    """
    Visualize the CTS tree directly from clock_tree.json using fabric_db background.
    Uses the same visual format as plot_cts_tree_overlay.
    Plots EVERY buffer and FF in the tree.
    
    Args:
        clock_tree_json: Clock tree dictionary from cts_htree.py
        fabric_db: Fabric database (for die/core bounds and cell positions)
        out_png: Output PNG file path
        figsize: Figure size tuple (width, height)
        dpi: DPI for saving
    """
    # Build fabric cell position lookup from fabric_db
    fabric_positions = {}
    try:
        cells_by_tile = fabric_db.get('fabric', {}).get('cells_by_tile', {})
        for tile_name, tile in cells_by_tile.items():
            for cell in tile.get('cells', []) or []:
                name = cell.get('name')
                if not name:
                    continue
                x = cell.get('x')
                y = cell.get('y')
                if x is not None and y is not None:
                    fabric_positions[name] = (float(x), float(y))
    except Exception as e:
        print(f"Warning: Error loading fabric positions: {e}")
        fabric_positions = {}

    print(f"Loaded {len(fabric_positions)} cells from fabric_db")

    # Get die/core bounds from fabric_db
    pin_placement = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_info = pin_placement.get("die", {})
    core_info = pin_placement.get("core", {})

    die_width = die_info.get("width_um", 100.0)
    die_height = die_info.get("height_um", 100.0)
    core_margin = die_info.get("core_margin_um", 5.0)

    die_bbox = (0.0, 0.0, die_width, die_height)
    core_x0 = (die_width - core_info.get("width_um", die_width - 20)) / 2.0
    core_y0 = (die_height - core_info.get("height_um", die_height - 20)) / 2.0
    core_bbox = (core_x0, core_y0, core_x0 + core_info.get("width_um", die_width - 20), core_y0 + core_info.get("height_um", die_height - 20))

    # Collect DFFs and buffers from tree
    dffs = []
    buffers = []
    missing_coords = []

    def traverse_tree(node):
        """Traverse tree and collect ALL FFs and buffers with their coordinates from tree."""
        if not node:
            return
        
        buffer_name = node.get("buffer")
        buffer_type = node.get("buffer_type", "")
        
        # Get buffer coordinates - PRIMARY: use buffer_pos from tree
        if buffer_name:
            bx, by = None, None
            
            # Use buffer_pos from tree FIRST (most reliable)
            if "buffer_pos" in node and node["buffer_pos"]:
                try:
                    bx, by = float(node['buffer_pos'][0]), float(node['buffer_pos'][1])
                except (ValueError, TypeError, IndexError):
                    pass
            
            # Fallback to centroid if buffer_pos not available
            if bx is None or by is None:
                if "centroid" in node and node["centroid"]:
                    try:
                        bx, by = float(node['centroid'][0]), float(node['centroid'][1])
                    except (ValueError, TypeError, IndexError):
                        pass
            
            # Last resort: fabric positions
            if bx is None or by is None:
                if buffer_name in fabric_positions:
                    bx, by = fabric_positions[buffer_name]
            
            if bx is not None and by is not None:
                buffers.append({
                    "name": buffer_name,
                    "type": buffer_type,
                    "x": bx,
                    "y": by,
                    "level": node.get("level", 0)
                })
            else:
                missing_coords.append(('buffer', buffer_name))

        # Collect sinks (DFFs) - use tree coordinates
        if "sinks" in node and node["sinks"]:
            for sink_name in node["sinks"]:
                sx, sy = None, None
                
                # PRIMARY: use centroid from tree (center of sink group)
                if "centroid" in node and node["centroid"]:
                    try:
                        sx, sy = float(node["centroid"][0]), float(node["centroid"][1])
                    except (ValueError, TypeError, IndexError):
                        pass
                
                # Fallback: fabric positions
                if sx is None or sy is None:
                    if sink_name in fabric_positions:
                        sx, sy = fabric_positions[sink_name]
                
                if sx is not None and sy is not None:
                    dffs.append({
                        "name": sink_name,
                        "x": sx,
                        "y": sy,
                        "level": node.get("level", 0)
                    })
                else:
                    missing_coords.append(('sink', sink_name))

        # Recurse on children - IMPORTANT: iterate through ALL children
        if "children" in node and node["children"]:
            for child in node["children"]:
                traverse_tree(child)

    traverse_tree(clock_tree_json)
    
    print(f"Extracted {len(buffers)} buffers and {len(dffs)} DFFs from tree")
    
    if missing_coords:
        print(f"\nWarning: Could not find coordinates for {len(missing_coords)} cells:")
        for cell_type, cell_name in missing_coords[:10]:  # Show first 10
            print(f"  {cell_type}: {cell_name}")
        if len(missing_coords) > 10:
            print(f"  ... and {len(missing_coords) - 10} more")

    # Create figure
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')

    # Draw die and core
    dx0, dy0, dx1, dy1 = die_bbox
    cx0, cy0, cx1, cy1 = core_bbox

    ax.add_patch(patches.Rectangle((dx0, dy0), dx1 - dx0, dy1 - dy0, fill=False, lw=2, edgecolor='blue', label="Die"))
    ax.add_patch(patches.Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False, lw=2, ls='--', edgecolor='green', label="Core"))

    # Draw tree edges (buffer to buffer, buffer to DFF)
    def draw_edges(node, parent_x=None, parent_y=None):
        """Draw connections between tree nodes."""
        if not node:
            return

        buffer_name = node.get("buffer")
        bx, by = None, None
        
        if buffer_name:
            # Use buffer_pos from tree FIRST
            if "buffer_pos" in node and node["buffer_pos"]:
                try:
                    bx, by = float(node['buffer_pos'][0]), float(node['buffer_pos'][1])
                except (ValueError, TypeError, IndexError):
                    pass
            
            # Fallback to centroid
            if bx is None or by is None:
                if "centroid" in node and node["centroid"]:
                    try:
                        bx, by = float(node['centroid'][0]), float(node['centroid'][1])
                    except (ValueError, TypeError, IndexError):
                        pass
            
            # Last resort: fabric positions
            if bx is None or by is None:
                if buffer_name in fabric_positions:
                    bx, by = fabric_positions[buffer_name]

        if bx is not None and by is not None:
            # Draw edge from parent buffer
            if parent_x is not None and parent_y is not None:
                ax.plot([parent_x, bx], [parent_y, by], color="purple", linewidth=1.5, alpha=0.7, zorder=1)

            # Draw edges to children
            if "children" in node and node["children"]:
                for child in node["children"]:
                    draw_edges(child, bx, by)

            # Draw edges to sinks (DFFs)
            if "sinks" in node and node["sinks"] and "centroid" in node and node["centroid"]:
                try:
                    sx, sy = float(node["centroid"][0]), float(node["centroid"][1])
                    if sx is not None and sy is not None and bx is not None and by is not None:
                        ax.plot([bx, sx], [by, sy], color="orange", linewidth=1, alpha=0.6, linestyle=":", zorder=1)
                except (ValueError, TypeError, IndexError):
                    pass

    # Draw from root
    draw_edges(clock_tree_json)

    # Draw DFFs (red circles) - semi-transparent so buffers show through
    if dffs:
        dff_xs = [d["x"] for d in dffs]
        dff_ys = [d["y"] for d in dffs]
        ax.scatter(dff_xs, dff_ys, s=30, c='red', marker='o', label=f"DFFs ({len(dffs)})", zorder=2, edgecolors='darkred', linewidth=0.5, alpha=0.6)

    # Draw buffers with size/color gradient by level
    if buffers:
        # Group buffers by level for better visualization
        by_level = {}
        for buf in buffers:
            level = buf.get("level", 0)
            if level not in by_level:
                by_level[level] = []
            by_level[level].append(buf)
        
        # Color map: deeper levels get different colors
        level_colors = {
            0: 'darkblue',      # Root
            1: 'mediumblue',    # Level 1
            2: 'cornflowerblue',
            3: 'lightblue',
            4: 'cyan',          # Mid-tree
            5: 'lightcyan',
            6: 'lime'           # Leaf buffers
        }
        
        level_sizes = {
            0: 200,   # Root - largest
            1: 180,
            2: 160,
            3: 140,
            4: 120,   # Intermediate buffers - more visible
            5: 100,
            6: 80     # Leaf buffers - still visible
        }
        
        for level in sorted(by_level.keys()):
            level_bufs = by_level[level]
            buf_xs = [b["x"] for b in level_bufs]
            buf_ys = [b["y"] for b in level_bufs]
            color = level_colors.get(level, 'blue')
            size = level_sizes.get(level, 40)
            
            ax.scatter(buf_xs, buf_ys, s=size, c=color, marker='s', 
                      label=f"Buffers L{level} ({len(level_bufs)})", zorder=3, 
                      edgecolors='darkblue', linewidth=0.5, alpha=0.9)

    # Labels
    ax.set_xlabel("X (µm)")
    ax.set_ylabel("Y (µm)")
    ax.set_title(f"CTS Tree Overlay: {len(dffs)} DFFs, {len(buffers)} Buffers (color-coded by level)")
    ax.grid(True, lw=0.3, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8, ncol=2)

    # Set bounds to fit all cells with padding
    all_xs = []
    all_ys = []
    if dffs:
        all_xs.extend([d["x"] for d in dffs])
        all_ys.extend([d["y"] for d in dffs])
    if buffers:
        all_xs.extend([b["x"] for b in buffers])
        all_ys.extend([b["y"] for b in buffers])
    
    if all_xs and all_ys:
        x_min, x_max = min(all_xs), max(all_xs)
        y_min, y_max = min(all_ys), max(all_ys)
        x_pad = (x_max - x_min) * 0.05 if x_max > x_min else 50
        y_pad = (y_max - y_min) * 0.05 if y_max > y_min else 50
        ax.set_xlim(x_min - x_pad, x_max + x_pad)
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
    else:
        # Fallback to die bounds
        ax.set_xlim(die_bbox[0] - 10, die_bbox[2] + 10)
        ax.set_ylim(die_bbox[1] - 10, die_bbox[3] + 10)

    plt.tight_layout()
    plt.savefig(out_png, dpi=dpi, bbox_inches='tight')
    print(f"Saved CTS tree overlay to {out_png}")
    plt.close()

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
    # Load placement mapping
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

    # Build fabric cell position lookup from fabric_db as a fallback
    fabric_positions = {}
    try:
        cells_by_tile = fabric_db.get('fabric', {}).get('cells_by_tile', {})
        for tile_name, tile in cells_by_tile.items():
            for cell in tile.get('cells', []) or []:
                name = cell.get('name')
                if not name:
                    continue
                # prefer explicit width/height-based x,y if provided
                x = cell.get('x')
                y = cell.get('y')
                if x is not None and y is not None:
                    fabric_positions[name] = (float(x), float(y))
    except Exception:
        fabric_positions = {}

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

    # Collect DFFs and buffers
    dffs = []
    buffers = []

    def traverse_tree(node):
        if not node:
            return
        buffer_name = node.get("buffer")
        # Determine buffer position using placement, fabric_db fallback, or tree metadata
        if buffer_name:
            if buffer_name in placement:
                bx, by = placement[buffer_name]
            elif buffer_name in fabric_positions:
                bx, by = fabric_positions[buffer_name]
            else:
                bp = node.get('buffer_pos') or node.get('centroid')
                if bp:
                    bx, by = float(bp[0]), float(bp[1])
                else:
                    bx, by = None, None

            if bx is not None and by is not None:
                buffers.append({
                    "name": buffer_name,
                    "x": bx,
                    "y": by,
                    "level": node.get("level", 0)
                })

        # Check for sink_logical_names (leaf node)
        if "sink_logical_names" in node:
            for sink_name in node["sink_logical_names"]:
                # Try placement first, then fabric_db positions
                if sink_name in placement:
                    sx, sy = placement[sink_name]
                elif sink_name in fabric_positions:
                    sx, sy = fabric_positions[sink_name]
                else:
                    sx, sy = None, None

                if sx is not None and sy is not None:
                    dffs.append({
                        "name": sink_name,
                        "x": sx,
                        "y": sy
                    })

        # Recurse on children
        for child in node.get("children", []):
            traverse_tree(child)

    traverse_tree(clock_tree)

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
        if not node:
            return

        buffer_name = node.get("buffer")

        # Resolve buffer coordinate similarly to traverse_tree
        bx, by = None, None
        if buffer_name:
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
                    # Resolve sink coordinates
                    sx, sy = None, None
                    if sink_name in placement:
                        sx, sy = placement[sink_name]
                    elif sink_name in fabric_positions:
                        sx, sy = fabric_positions[sink_name]
                    # If found, draw edge
                    if sx is not None and sy is not None and bx is not None and by is not None:
                        ax.plot([bx, sx], [by, sy], color="orange", linewidth=1, alpha=0.6, linestyle=":", zorder=1)

    # Draw from root
    draw_edges(clock_tree)

    # Draw DFFs (red circles)
    if dffs:
        dff_xs = [d["x"] for d in dffs]
        dff_ys = [d["y"] for d in dffs]
        ax.scatter(dff_xs, dff_ys, s=80, color="red", marker="o", label="DFFs (sinks)", zorder=5, edgecolors="darkred", linewidths=1)

    # Draw buffers (blue squares)
    if buffers:
        buf_xs = [b["x"] for b in buffers]
        buf_ys = [b["y"] for b in buffers]
        ax.scatter(buf_xs, buf_ys, s=100, color="cyan", marker="s", label="Buffers (nodes)", zorder=4, edgecolors="blue", linewidths=1)

    # Labels
    ax.set_xlabel("X (µm)")
    ax.set_ylabel("Y (µm)")
    ax.set_title(f"CTS Tree Overlay: {len(dffs)} DFFs, {len(buffers)} Buffers")
    ax.grid(True, lw=0.3, alpha=0.3)
    ax.legend(loc="upper right")

    # Set bounds
    ax.set_xlim(die_bbox[0] - 10, die_bbox[2] + 10)
    ax.set_ylim(die_bbox[1] - 10, die_bbox[3] + 10)

    plt.tight_layout()
    plt.savefig(out_png, dpi=dpi, bbox_inches='tight')
    print(f"Saved CTS tree overlay to {out_png}")
    plt.close()

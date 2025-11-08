"""
visualize.py
------------
Ground-truth visualization of the structured-ASIC fabric.

Function:
    plot_fabric_ground_truth(fabric_db, pins=None, ...)

Features:
  - Automatically detects pins from fabric_db["fabric"]["pin_placement"] if not given.
  - Extracts die and core dimensions from pin_placement data.
  - Supports flexible pin formats.
  - Draws die, core, pins, and a semi-transparent rectangle for every fabric slot.
"""

from build_fabric_db import build_fabric_db
from typing import Dict, Any, Tuple, Optional
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import math


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _normalize_cells_by_tile(fabric_db: Dict[str, Any]):
    """Return an iterable of (tile_name, tile_x, tile_y, cell_dict)."""
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    for tile_name, tile in cells_by_tile.items():
        tile_x = tile.get("x", None)
        tile_y = tile.get("y", None)
        cells = tile.get("cells", []) or []

        for cell in cells:
            cx = cell.get("x", tile_x)
            cy = cell.get("y", tile_y)
            w = cell.get("w", cell.get("width", None))
            h = cell.get("h", cell.get("height", None))
            yield tile_name, cx, cy, w, h, cell


def _collect_pin_list(pins):
    """Normalize pins into a list of dicts with x, y, name.
    Handles both flat lists and nested dict structures like pin_placement['pins'].
    """
    pin_list = []
    if pins is None:
        return pin_list

    # If 'pins' key exists inside, descend into it
    if isinstance(pins, dict) and "pins" in pins:
        pins = pins["pins"]

    # Case 1: dict of pin_name -> coords
    if isinstance(pins, dict):
        for name, info in pins.items():
            if isinstance(info, dict):
                x = info.get("x") or info.get("cx") or info.get("pos_x") or info.get("x_um")
                y = info.get("y") or info.get("cy") or info.get("pos_y") or info.get("y_um")
                if x is None or y is None:
                    continue
                pin_list.append({"name": name, "x": float(x), "y": float(y)})
            else:
                try:
                    x, y = info
                    pin_list.append({"name": name, "x": float(x), "y": float(y)})
                except Exception:
                    continue

    # Case 2: list of dicts (standard format)
    elif isinstance(pins, list):
        for item in pins:
            if isinstance(item, dict):
                name = item.get("name", item.get("pin", None))
                x = item.get("x") or item.get("cx") or item.get("pos_x") or item.get("x_um")
                y = item.get("y") or item.get("cy") or item.get("pos_y") or item.get("y_um")
                if x is None or y is None:
                    continue
                pin_list.append({"name": name, "x": float(x), "y": float(y)})

    return pin_list


def _extract_die_core_from_pin_placement(fabric_db: Dict[str, Any]) -> Tuple[Optional[Tuple], Optional[Tuple]]:
    """Extract die and core bounding boxes from pin_placement data."""
    pin_placement = fabric_db.get("fabric", {}).get("pin_placement", {})

    die_bbox = None
    core_bbox = None

    # Extract die dimensions
    die_info = pin_placement.get("die", {})
    if die_info:
        die_width = die_info.get("width_um")
        die_height = die_info.get("height_um")
        if die_width is not None and die_height is not None:
            die_bbox = (0.0, 0.0, float(die_width), float(die_height))

    # Extract core dimensions
    core_info = pin_placement.get("core", {})
    if core_info:
        core_width = core_info.get("width_um")
        core_height = core_info.get("height_um")
        core_margin = die_info.get("core_margin_um", 5.0)

        if core_width is not None and core_height is not None and die_bbox:
            # Center the core within the die
            core_x0 = (die_bbox[2] - core_width) / 2.0
            core_y0 = (die_bbox[3] - core_height) / 2.0
            core_bbox = (core_x0, core_y0, core_x0 + core_width, core_y0 + core_height)

    return die_bbox, core_bbox


# ---------------------------------------------------------------------
# Main Visualization Function
# ---------------------------------------------------------------------
def plot_fabric_ground_truth(
    fabric_db: Dict[str, Any],
    pins: Optional[Any] = None,
    die_bbox: Optional[Tuple[float, float, float, float]] = None,
    core_bbox: Optional[Tuple[float, float, float, float]] = None,
    figsize=(12, 12),
    show: bool = True,
    savepath: Optional[str] = None,
    slot_default_size: Tuple[float, float] = (1.0, 1.0),
    alpha: float = 0.35
):
    """
    Draw the die, core, pins, and all fabric slots.
    Automatically extracts pins, die, and core from fabric_db if not provided.
    """
    # Auto-detect pins if not explicitly passed
    if pins is None:
        pins = fabric_db.get("fabric", {}).get("pin_placement", {})

    # Auto-detect die and core from pin_placement if not provided
    if die_bbox is None or core_bbox is None:
        auto_die, auto_core = _extract_die_core_from_pin_placement(fabric_db)
        if die_bbox is None:
            die_bbox = auto_die
        if core_bbox is None:
            core_bbox = auto_core

    # Collect cells
    cell_entries = list(_normalize_cells_by_tile(fabric_db))
    if not cell_entries:
        raise ValueError("No fabric cells found in fabric_db['fabric']['cells_by_tile'].")

    # Determine world bounds from cells if die_bbox still not available
    if die_bbox is None:
        xs, ys = [], []
        for _, cx, cy, _, _, _ in cell_entries:
            if cx is not None and cy is not None:
                xs.append(cx)
                ys.append(cy)
        if not xs or not ys:
            xs, ys = [0], [0]

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = max(1.0, xmax - xmin)
        yspan = max(1.0, ymax - ymin)
        margin = 0.05 * max(xspan, yspan)

        die_bbox = (xmin - margin, ymin - margin, xmax + margin, ymax + margin)

    # If core_bbox still not available, create default
    if core_bbox is None:
        dx = die_bbox[2] - die_bbox[0]
        dy = die_bbox[3] - die_bbox[1]
        core_bbox = (
            die_bbox[0] + 0.08 * dx,
            die_bbox[1] + 0.08 * dy,
            die_bbox[2] - 0.08 * dx,
            die_bbox[3] - 0.08 * dy
        )

    # Extract cell types for color mapping
    import re
    def extract_type_from_name(name: str):
        if not name:
            return "UNKNOWN"
        m = re.search(r"__R\d+_([A-Za-z0-9]+)_", name)
        if m:
            return m.group(1).upper()
        m2 = re.search(r"__([a-z0-9]+)_[0-9]+", name)
        if m2:
            return m2.group(1).upper()
        m3 = re.search(r"([a-z]+)_\d+$", name)
        if m3:
            return m3.group(1).upper()
        return name.upper()

    type_to_index = {}
    cur_index = 0
    for _, _, _, _, _, cell in cell_entries:
        name = cell.get("name", "")
        t = extract_type_from_name(name)
        if t not in type_to_index:
            type_to_index[t] = cur_index
            cur_index += 1

    # Create plot
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')

    # Draw die and core
    dx0, dy0, dx1, dy1 = die_bbox
    cx0, cy0, cx1, cy1 = core_bbox
    ax.add_patch(patches.Rectangle((dx0, dy0), dx1 - dx0, dy1 - dy0, fill=False, lw=2, edgecolor='blue', label="die"))
    ax.add_patch(patches.Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False, lw=2, ls='--', edgecolor='green', label="core"))

    # Colormap
    import matplotlib
    cmap = matplotlib.colormaps.get_cmap('tab20')

    # Draw fabric slots
    for tile_name, cx, cy, w, h, cell in cell_entries:
        name = cell.get("name", "")
        t = extract_type_from_name(name)
        idx = type_to_index.get(t, 0)
        if w is None or h is None:
            w, h = slot_default_size
        if cx is None or cy is None:
            m = re.match(r".*T(\d+)Y(\d+).*", tile_name)
            if m:
                cx = int(m.group(1)) * w
                cy = int(m.group(2)) * h
            else:
                cx, cy = 0.0, 0.0
        x0, y0 = cx - w / 2.0, cy - h / 2.0
        color = cmap(idx % cmap.N)
        ax.add_patch(patches.Rectangle((x0, y0), w, h, facecolor=color, edgecolor='black', lw=0.4, alpha=alpha))

    # Draw pins (without labels)
    pin_list = _collect_pin_list(pins)
    if pin_list:
        pxs = [p["x"] for p in pin_list]
        pys = [p["y"] for p in pin_list]
        ax.scatter(pxs, pys, s=18, marker='o', color='red', zorder=10, label='pins')

    # Legend
    handles = [
        patches.Patch(facecolor='none', edgecolor='blue', lw=2, label='Die'),
        patches.Patch(facecolor='none', edgecolor='green', lw=2, linestyle='--', label='Core')
    ]
    for t, idx in list(type_to_index.items())[:18]:  # Reduced to 18 to make room for die/core
        c = cmap(idx % cmap.N)
        handles.append(patches.Patch(facecolor=c, alpha=alpha, label=t))
    if handles:
        ax.legend(handles=handles, title="Components", loc='upper right', fontsize=8)

    # Final formatting
    xspan = die_bbox[2] - die_bbox[0]
    yspan = die_bbox[3] - die_bbox[1]
    ax.set_xlim(die_bbox[0] - 0.05 * xspan, die_bbox[2] + 0.05 * xspan)
    ax.set_ylim(die_bbox[1] - 0.05 * yspan, die_bbox[3] + 0.05 * yspan)
    ax.set_xlabel("X (μm)")
    ax.set_ylabel("Y (μm)")
    ax.set_title("Structured ASIC Fabric Visualization")
    ax.grid(True, lw=0.3, alpha=0.5)

    if savepath:
        fig.savefig(savepath, dpi=300, bbox_inches='tight')
        print(f"Saved visualization to {savepath}")

    if show:
        plt.show()

    return fig, ax



if __name__ == "__main__":
    fabric_db = build_fabric_db("fabric/fabric_cells.yaml","fabric/pins.yaml")
    plot_fabric_ground_truth(fabric_db, show=True, savepath="fabric_layout.png")
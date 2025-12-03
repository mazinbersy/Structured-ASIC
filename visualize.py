#!/usr/bin/env python3
"""
visualize.py
--------------
Unified visualization entrypoint for Phase 1 & Phase 2.

Usage:
  python3 visualize.py --design 6502

Outputs (saved under `build/<design>/`):
  - <design>_fabric_layout.png  (ground-truth fabric plot)
  - <design>_density.png       (placement density heatmap)
  - <design>_net_length.png    (net HPWL histogram)

This file consolidates the previous `visualization/visualize.py` and
`visualization/visualize_density_and_netlength.py` scripts and provides
an opinionated CLI to generate all visualization layers for a given design.
"""

import os
import argparse
from typing import Dict, Any, Tuple, Optional

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json


# ---------------------------
# Fabric / Ground-truth plot
# ---------------------------

def _normalize_cells_by_tile(fabric_db: Dict[str, Any]):
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    for tile_name, tile in cells_by_tile.items():
        tile_x = tile.get("x", None)
        tile_y = tile.get("y", None)
        cells = tile.get("cells", []) or []

        for cell in cells:
            cx = cell.get("x", tile_x)
            cy = cell.get("y", tile_y)
            w = cell.get("width_um")
            h = cell.get("height_um")
            yield tile_name, cx, cy, w, h, cell


def _collect_pin_list(pins):
    pin_list = []
    if pins is None:
        return pin_list

    if isinstance(pins, dict) and "pins" in pins:
        pins = pins["pins"]

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
    pin_placement = fabric_db.get("fabric", {}).get("pin_placement", {})

    die_bbox = None
    core_bbox = None

    die_info = pin_placement.get("die", {})
    if die_info:
        die_width = die_info.get("width_um")
        die_height = die_info.get("height_um")
        if die_width is not None and die_height is not None:
            die_bbox = (0.0, 0.0, float(die_width), float(die_height))

    core_info = pin_placement.get("core", {})
    if core_info and die_bbox is not None:
        core_width = core_info.get("width_um")
        core_height = core_info.get("height_um")
        if core_width is not None and core_height is not None:
            core_x0 = (die_bbox[2] - core_width) / 2.0
            core_y0 = (die_bbox[3] - core_height) / 2.0
            core_bbox = (core_x0, core_y0, core_x0 + core_width, core_y0 + core_height)

    return die_bbox, core_bbox


def _extract_cell_type(cell: Dict[str, Any]) -> str:
    cell_type = cell.get("cell_type", "")
    if cell_type:
        parts = cell_type.split("__")
        if len(parts) > 1:
            base = parts[-1]
            import re
            m = re.match(r"([a-z]+)\d*_?_?\d*", base)
            if m:
                return m.group(1).upper()
            return base.upper()

    template_name = cell.get("template_name", "")
    if template_name:
        import re
        m = re.match(r"R\d+_([A-Z]+)_\d+", template_name)
        if m:
            return m.group(1)

    name = cell.get("name", "")
    if name:
        import re
        m = re.search(r"R\d+_([A-Z]+)_\d+", name)
        if m:
            return m.group(1)

    return "UNKNOWN"


def plot_fabric_ground_truth(
    fabric_db: Dict[str, Any],
    pins: Optional[Any] = None,
    die_bbox: Optional[Tuple[float, float, float, float]] = None,
    core_bbox: Optional[Tuple[float, float, float, float]] = None,
    figsize=(12, 12),
    show: bool = False,
    savepath: Optional[str] = None,
    slot_default_size: Tuple[float, float] = (1.0, 1.0),
    alpha: float = 0.35
):
    if pins is None:
        pins = fabric_db.get("fabric", {}).get("pin_placement", {})

    if die_bbox is None or core_bbox is None:
        auto_die, auto_core = _extract_die_core_from_pin_placement(fabric_db)
        if die_bbox is None:
            die_bbox = auto_die
        if core_bbox is None:
            core_bbox = auto_core

    cell_entries = list(_normalize_cells_by_tile(fabric_db))
    if not cell_entries:
        raise ValueError("No fabric cells found in fabric_db['fabric']['cells_by_tile'].'")

    if die_bbox is None:
        xs, ys = [], []
        for _, cx, cy, w, h, _ in cell_entries:
            if cx is not None and cy is not None:
                xs.append(cx)
                ys.append(cy)
                if w is not None:
                    xs.append(cx + w)
                if h is not None:
                    ys.append(cy + h)
        if not xs or not ys:
            xs, ys = [0], [0]

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        xspan = max(1.0, xmax - xmin)
        yspan = max(1.0, ymax - ymin)
        margin = 0.05 * max(xspan, yspan)

        die_bbox = (xmin - margin, ymin - margin, xmax + margin, ymax + margin)

    if core_bbox is None:
        dx = die_bbox[2] - die_bbox[0]
        dy = die_bbox[3] - die_bbox[1]
        core_bbox = (
            die_bbox[0] + 0.08 * dx,
            die_bbox[1] + 0.08 * dy,
            die_bbox[2] - 0.08 * dx,
            die_bbox[3] - 0.08 * dy
        )

    type_to_index = {}
    cur_index = 0
    for _, _, _, _, _, cell in cell_entries:
        t = _extract_cell_type(cell)
        if t not in type_to_index:
            type_to_index[t] = cur_index
            cur_index += 1

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_aspect('equal', adjustable='box')

    dx0, dy0, dx1, dy1 = die_bbox
    cx0, cy0, cx1, cy1 = core_bbox
    ax.add_patch(patches.Rectangle((dx0, dy0), dx1 - dx0, dy1 - dy0, fill=False, lw=2, edgecolor='blue', label="die"))
    ax.add_patch(patches.Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0, fill=False, lw=2, ls='--', edgecolor='green', label="core"))

    import matplotlib
    cmap = matplotlib.colormaps.get_cmap('tab20')

    for tile_name, cx, cy, w, h, cell in cell_entries:
        t = _extract_cell_type(cell)
        idx = type_to_index.get(t, 0)

        if w is None or h is None:
            w, h = slot_default_size

        if cx is None or cy is None:
            import re
            m = re.match(r".*T(\d+)Y(\d+).*", tile_name)
            if m:
                cx = int(m.group(1)) * w
                cy = int(m.group(2)) * h
            else:
                cx, cy = 0.0, 0.0

        color = cmap(idx % cmap.N)
        ax.add_patch(patches.Rectangle((cx, cy), w, h, facecolor=color, edgecolor='black', lw=0.4, alpha=alpha))

    pin_list = _collect_pin_list(pins)
    if pin_list:
        pxs = [p["x"] for p in pin_list]
        pys = [p["y"] for p in pin_list]
        ax.scatter(pxs, pys, s=18, marker='o', color='red', zorder=10, label='pins')

    handles = [
        patches.Patch(facecolor='none', edgecolor='blue', lw=2, label='Die'),
        patches.Patch(facecolor='none', edgecolor='green', lw=2, linestyle='--', label='Core')
    ]
    for t, idx in list(type_to_index.items())[:18]:
        c = cmap(idx % cmap.N)
        handles.append(patches.Patch(facecolor=c, alpha=alpha, label=t))
    if handles:
        ax.legend(handles=handles, title="Components", loc='upper right', fontsize=8)

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


# ---------------------------
# Density & Net-length plots
# ---------------------------

def read_map_file(path):
    placement = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if "->" in line:
                arrow_idx = parts.index("->")
                if arrow_idx >= 3 and arrow_idx + 1 < len(parts):
                    x = float(parts[arrow_idx - 2])
                    y = float(parts[arrow_idx - 1])
                    instance_name = parts[arrow_idx + 1]
                    placement[instance_name] = (x, y)
            elif len(parts) == 3:
                port_name, x, y = parts
                placement[port_name] = (float(x), float(y))
    return placement


def make_density_heatmap(placement_coords, fabric_extent, outpath, bins=(200, 200)):
    xs = [p[0] for p in placement_coords]
    ys = [p[1] for p in placement_coords]
    plt.figure(figsize=(6, 6))
    H, xedges, yedges = np.histogram2d(xs, ys, bins=bins, range=[[0, fabric_extent[0]], [0, fabric_extent[1]]])
    H = np.rot90(H)
    H = np.flipud(H)
    plt.imshow(H, extent=[0, fabric_extent[0], 0, fabric_extent[1]], aspect='auto')
    plt.colorbar(label='Placed cells (counts)')
    plt.title('Placement Density Heatmap')
    plt.xlabel('X')
    plt.ylabel('Y')
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


def compute_net_hpwl(logical_db, placement_coords):
    net_hpwl = []
    for net_id, net in logical_db['nets'].items():
        xs = []
        ys = []
        for endpoint in net['connections']:
            node = endpoint[0]
            if node in placement_coords:
                x, y = placement_coords[node]
                xs.append(x)
                ys.append(y)
            else:
                continue
        if not xs:
            continue
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        net_hpwl.append(hpwl)
    return net_hpwl


def generate_all_visualizations(design: str, map_path: Optional[str] = None, show: bool = False):
    build_dir = os.path.join("build", design)
    os.makedirs(build_dir, exist_ok=True)

    fabric_db = build_fabric_db("fabric/fabric_cells.yaml", "fabric/pins.yaml", "fabric/fabric.yaml")
    print("Loaded fabric DB")

    # Phase 1 ground-truth
    fabric_out = os.path.join(build_dir, f"{design}_fabric_layout.png")
    plot_fabric_ground_truth(fabric_db, show=show, savepath=fabric_out)

    # Phase 2 density & net-length (requires placement map + logical db)
    logical_db, _ = parse_design_json(f"designs/{design}_mapped.json")

    # Try common map filenames before skipping
    candidates = [
        map_path,
        os.path.join(build_dir, f"{design}.map") if map_path is None else None,
        os.path.join(build_dir, f"{design}_cts.map"),
        os.path.join(build_dir, f"{design}_placement.map"),
    ]
    candidates = [p for p in candidates if p]
    found_map = None
    for p in candidates:
        if os.path.exists(p):
            found_map = p
            break

    if not found_map:
        print(f"Map file not found among candidates: {candidates}. Skipping density/net-length plots.")
        return

    placement_coords = read_map_file(found_map)
    if not placement_coords:
        print("No placement coords found in map. Skipping density/net-length plots.")
        return

    xs = [c[0] for c in placement_coords.values()]
    ys = [c[1] for c in placement_coords.values()]
    extent = (max(xs), max(ys))

    density_path = os.path.join(build_dir, f"{design}_density.png")
    make_density_heatmap(list(placement_coords.values()), extent, density_path, bins=(200, 200))
    print("Saved density heatmap ->", density_path)

    net_hpwl = compute_net_hpwl(logical_db, placement_coords)
    hist_path = os.path.join(build_dir, f"{design}_net_length.png")
    plt.figure(figsize=(6, 4))
    plt.hist(net_hpwl, bins=100)
    plt.xlabel("Net HPWL (µm)")
    plt.ylabel("Count")
    plt.title(f"{design} Net Length Histogram")
    plt.tight_layout()
    plt.savefig(hist_path, dpi=200)
    plt.close()
    print("Saved net-length histogram ->", hist_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True, help="Design name (e.g., 6502)")
    parser.add_argument("--map", default=None, help="Optional path to map file (overrides build/<design>/<design>.map)")
    parser.add_argument("--show", action="store_true", help="Show plots interactively")
    args = parser.parse_args()

    generate_all_visualizations(args.design, map_path=args.map, show=args.show)


if __name__ == "__main__":
    main()

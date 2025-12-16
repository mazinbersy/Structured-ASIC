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

from build_fabric_db import build_fabric_db
from typing import Dict, Any, Tuple, Optional, List
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import math
import numpy as np
import re
import argparse
import os


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


# ---------------------------------------------------------------------
# Congestion / STA parsing and visualization utilities
# ---------------------------------------------------------------------


def _get_fabric_bounds(fabric_db: Dict[str, Any]) -> Tuple[float, float]:
    """Return approximate fabric extent (max_x, max_y) in microns."""
    xs = []
    ys = []
    for _, cx, cy, w, h, _ in _normalize_cells_by_tile(fabric_db):
        if cx is None or cy is None:
            continue
        xs.append(cx)
        ys.append(cy)
        if w:
            xs.append(cx + w)
        if h:
            ys.append(cy + h)
    if not xs or not ys:
        return 1.0, 1.0
    return max(xs), max(ys)


def generate_congestion_heatmap_from_report(report_path: str,
                                            fabric_db: Dict[str, Any],
                                            outpath: str,
                                            bins: Tuple[int, int] = (200, 200)) -> None:
    """
    Parse a congestion report (best-effort) and render a heatmap.

    The parser attempts to find lines containing X, Y and a congestion value
    (percentage or float). If that fails it falls back to extracting all
    numeric blocks and producing a 2D histogram.
    """
    if not os.path.exists(report_path):
        raise FileNotFoundError(report_path)

    xs = []
    ys = []
    vals = []

    float_re = r"([-+]?[0-9]*\.?[0-9]+)"
    coord_val_re = re.compile(rf"\b{float_re}\s+{float_re}\s+{float_re}%?\b")

    with open(report_path, 'r') as f:
        for line in f:
            m = coord_val_re.search(line)
            if m:
                x = float(m.group(1))
                y = float(m.group(2))
                v = float(m.group(3))
                xs.append(x)
                ys.append(y)
                vals.append(v)

    # If we found explicit x,y,value triples, rasterize them
    if xs and ys and vals:
        extent_x = max(xs) - min(xs) if max(xs) != min(xs) else 1.0
        extent_y = max(ys) - min(ys) if max(ys) != min(ys) else 1.0
        # Use histogram weighted by congestion value
        H, xedges, yedges = np.histogram2d(xs, ys, bins=bins, weights=vals,
                                           range=[[min(xs), max(xs)], [min(ys), max(ys)]])
        # Normalize by counts to get average congestion per bin
        counts, _, _ = np.histogram2d(xs, ys, bins=bins, range=[[min(xs), max(xs)], [min(ys), max(ys)]])
        with np.errstate(divide='ignore', invalid='ignore'):
            H = np.divide(H, counts)
            H[np.isnan(H)] = 0.0

        plt.figure(figsize=(8, 6))
        plt.imshow(np.flipud(H), extent=[min(xs), max(xs), min(ys), max(ys)], cmap='hot', aspect='auto')
        plt.colorbar(label='Congestion (avg %)')
        plt.title(os.path.basename(report_path))
        plt.xlabel('X (um)')
        plt.ylabel('Y (um)')
        plt.tight_layout()
        plt.savefig(outpath, dpi=200)
        plt.close()
        print(f"Saved congestion heatmap -> {outpath}")
        return

    # Fallback: try to parse grid of floats in the file
    matrix = []
    with open(report_path, 'r') as f:
        for line in f:
            parts = re.findall(float_re, line)
            if parts:
                row = [float(p) for p in parts]
                matrix.append(row)

    if matrix:
        # Convert ragged rows to rectangular by padding with zeros
        maxlen = max(len(r) for r in matrix)
        M = np.zeros((len(matrix), maxlen))
        for i, r in enumerate(matrix):
            M[i, :len(r)] = r

        plt.figure(figsize=(8, 6))
        plt.imshow(M, cmap='hot', aspect='auto')
        plt.colorbar(label='Congestion (arb)')
        plt.title(os.path.basename(report_path))
        plt.tight_layout()
        plt.savefig(outpath, dpi=200)
        plt.close()
        print(f"Saved congestion heatmap -> {outpath} (from numeric grid)")
        return

    raise ValueError(f"Could not parse congestion report: {report_path}")


def parse_setup_report_for_slacks(report_path: str) -> List[float]:
    """
    Extract endpoint slacks from an OpenSTA or report_timing-style setup report.

    Only extracts lines that explicitly contain 'slack' followed by a number,
    typically formatted as "X.XXX   slack (MET)" or "X.XXX   slack (VIOLATED)".
    """
    if not os.path.exists(report_path):
        raise FileNotFoundError(report_path)

    slacks = []
    # Match lines with slack value - must have 'slack' keyword followed by optional (MET/VIOLATED)
    # Format: "                                     7.663   slack (MET)"
    slack_re = re.compile(r'^\s*([-+]?[0-9]*\.?[0-9]+)\s+slack\s*\(', re.IGNORECASE)

    with open(report_path, 'r') as f:
        for line in f:
            m = slack_re.search(line)
            if m:
                try:
                    slacks.append(float(m.group(1)))
                except Exception:
                    pass

    return slacks


def plot_slack_histogram(slacks: List[float], outpath: str, bins: int = 100) -> None:
    if not slacks:
        raise ValueError("No slacks provided to plot")
    plt.figure(figsize=(6, 4))
    plt.hist(slacks, bins=bins)
    plt.xlabel('Slack (ns)')
    plt.ylabel('Count')
    plt.title('Endpoint Slack Histogram')
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()
    print(f"Saved slack histogram -> {outpath}")


def parse_setup_report_for_worst_path(report_path: str, fabric_cell_names: List[str]) -> List[str]:
    """
    Extract the sequence of instance names from the worst (smallest slack) clock path in a setup report.

    Strategy:
      1. Split report into individual path sections
      2. Find the clock path with smallest slack
      3. Extract only cells from that specific path
    """
    if not os.path.exists(report_path):
        raise FileNotFoundError(report_path)

    fabric_set = set(fabric_cell_names)
    
    with open(report_path, 'r') as f:
        content = f.read()
    
    # Split into individual path reports by "Startpoint:"
    path_sections = re.split(r'\nStartpoint:', content)
    if path_sections and not path_sections[0].strip().startswith('Startpoint'):
        path_sections = path_sections[1:]  # Skip any header
    else:
        # First section already has Startpoint, restore it
        if len(path_sections) > 1:
            path_sections = [path_sections[0]] + ['Startpoint:' + s for s in path_sections[1:]]
    
    # Find the worst clock path (smallest slack in "Path Group: clk")
    worst_slack = float('inf')
    worst_path_section = None
    
    for section in path_sections:
        # Only consider clock domain paths
        if 'Path Group: clk' in section or 'Path Group:clk' in section:
            # Extract slack value
            slack_match = re.search(r'([-\d.]+)\s+slack', section, re.IGNORECASE)
            if slack_match:
                slack = float(slack_match.group(1))
                if slack < worst_slack:
                    worst_slack = slack
                    worst_path_section = section
    
    if not worst_path_section:
        # Fallback: no clock paths found, return empty
        return []
    
    # Extract cells from the worst path section only
    path_cells = []
    cell_re = re.compile(r'(T\d+Y\d+__R\d+_[A-Z]+_\d+)')
    
    for line in worst_path_section.split('\n'):
        # Look for lines with cell instances (contain fabric pattern and sky130)
        if '__' in line and 'sky130' in line:
            match = cell_re.search(line)
            if match:
                cell_name = match.group(1)
                # Only add if it's in the fabric and not already in the path
                if cell_name in fabric_set and (not path_cells or path_cells[-1] != cell_name):
                    path_cells.append(cell_name)
    
    return path_cells


def draw_critical_path_overlay(fabric_db: Dict[str, Any], path_cells: List[str], outpath: str,
                               base_figsize=(12, 12), show: bool = False) -> None:
    """
    Draw the fabric layout and overlay a bright red polyline connecting the centers of
    the cells listed in `path_cells` (which are fabric cell names).
    """
    fig, ax = plot_fabric_ground_truth(fabric_db, show=False, savepath=None, figsize=base_figsize, slot_default_size=(1.0, 1.0), alpha=0.3)

    # Build a mapping from cell name -> center coordinates
    name_to_center = {}
    for tile_name, cx, cy, w, h, cell in _normalize_cells_by_tile(fabric_db):
        cell_name = cell.get('name')
        if not cell_name:
            continue
        if w is None or h is None:
            w = 1.0
            h = 1.0
        cx_center = cx + w / 2.0
        cy_center = cy + h / 2.0
        name_to_center[cell_name] = (cx_center, cy_center)

    pts_x = []
    pts_y = []
    for name in path_cells:
        if name in name_to_center:
            x, y = name_to_center[name]
            pts_x.append(x)
            pts_y.append(y)

    if len(pts_x) >= 2:
        ax.plot(pts_x, pts_y, color='red', linewidth=3.0, alpha=0.95, zorder=20)
        ax.scatter(pts_x, pts_y, color='red', s=30, zorder=21)

    plt.title('Critical Path Overlay')
    fig.savefig(outpath, dpi=300, bbox_inches='tight')
    print(f"Saved critical path overlay -> {outpath}")
    if show:
        plt.show()
    plt.close(fig)


def _collect_all_fabric_cell_names(fabric_db: Dict[str, Any]) -> List[str]:
    names = []
    for _, _, _, _, _, cell in _normalize_cells_by_tile(fabric_db):
        n = cell.get('name')
        if n:
            names.append(n)
    return names


def _ensure_build_dir(design: str) -> str:
    build_dir = os.path.join('build', design)
    os.makedirs(build_dir, exist_ok=True)
    return build_dir


def main_cli():
    parser = argparse.ArgumentParser(description='Fabric visualizations: layout, congestion, slack, critical path')
    parser.add_argument('--design', required=False, help='Design name (used to write into build/<design>/)')
    parser.add_argument('--congestion', help='Path to congestion report (.rpt)')
    parser.add_argument('--setup', help='Path to setup report (.rpt)')
    parser.add_argument('--fabric-cells', default='fabric/fabric_cells.yaml')
    parser.add_argument('--pins', default='fabric/pins.yaml')
    parser.add_argument('--fabric', default='fabric/fabric.yaml')
    parser.add_argument('--layout-out', help='Output PNG for layout (default: build/<design>/_layout.png)')
    args = parser.parse_args()

    fabric_db = build_fabric_db(args.fabric_cells, args.pins, args.fabric)

    design = args.design or 'design'
    build_dir = _ensure_build_dir(design)

    # 1) Layout
    layout_out = args.layout_out or os.path.join(build_dir, f"{design}_layout.png")
    plot_fabric_ground_truth(fabric_db, show=False, savepath=layout_out)

    # 2) Congestion
    if args.congestion:
        cong_out = os.path.join(build_dir, f"{design}_congestion.png")
        generate_congestion_heatmap_from_report(args.congestion, fabric_db, cong_out)

    # 3) STA: slack histogram and critical path
    if args.setup:
        slacks = parse_setup_report_for_slacks(args.setup)
        slack_out = os.path.join(build_dir, f"{design}_slack.png")
        try:
            plot_slack_histogram(slacks, slack_out)
        except Exception as e:
            print(f"Could not plot slack histogram: {e}")

        # Critical path
        fabric_names = _collect_all_fabric_cell_names(fabric_db)
        path_cells = parse_setup_report_for_worst_path(args.setup, fabric_names)
        if path_cells:
            crit_out = os.path.join(build_dir, f"{design}_critical_path.png")
            draw_critical_path_overlay(fabric_db, path_cells, crit_out)


if __name__ == "__main__":
    main_cli()

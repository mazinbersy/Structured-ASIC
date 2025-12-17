#!/usr/bin/env python3
"""
visualization/viz.py
--------------------
Lightweight visualization layer for Structured-ASIC.

Consolidates all plot outputs into a single module with:
  - VizConfig: dataclass for paths, design name, style options
  - Individual plot functions (reusing existing logic)
  - run_all(): orchestrator that produces all outputs
  - CLI entrypoint for Makefile integration

Outputs:
  - <design>_layout.png         Fabric ground-truth layout
  - <design>_density.png        Placement density heatmap
  - <design>_net_length.png     Net HPWL histogram
  - <design>_congestion.png     Congestion heatmap (from .rpt)
  - <design>_slack.png          Slack histogram (from STA .rpt)
  - <design>_critical_path.png  Critical path overlay
  - <design>_cts_tree.png       CTS tree overlay

Usage:
  python visualization/viz.py --design 6502
  python visualization/viz.py --design 6502 --only layout density
  python visualization/viz.py --design 6502 --skip congestion slack
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Callable
import os
import sys
import re
import json

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches


# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VizConfig:
    """Configuration for all visualization outputs."""
    design: str
    build_dir: Path = field(default_factory=lambda: Path("build"))
    fabric_cells: Path = field(default_factory=lambda: Path("fabric/fabric_cells.yaml"))
    pins_yaml: Path = field(default_factory=lambda: Path("fabric/pins.yaml"))
    fabric_yaml: Path = field(default_factory=lambda: Path("fabric/fabric.yaml"))
    design_json: Optional[Path] = None  # Auto-derived if None
    placement_map: Optional[Path] = None  # Auto-derived if None
    
    # Style options
    figsize: Tuple[int, int] = (12, 12)
    dpi: int = 300
    alpha: float = 0.35
    cmap: str = 'tab20'
    hist_bins: int = 100
    heatmap_bins: Tuple[int, int] = (200, 200)

    def __post_init__(self):
        self.build_dir = Path(self.build_dir)
        self.fabric_cells = Path(self.fabric_cells)
        self.pins_yaml = Path(self.pins_yaml)
        self.fabric_yaml = Path(self.fabric_yaml)
        
        if self.design_json is None:
            self.design_json = Path(f"designs/{self.design}_mapped.json")
        else:
            self.design_json = Path(self.design_json)
            
        if self.placement_map is None:
            # Try common map filenames
            candidates = [
                self.out_dir / f"{self.design}.map",
                self.out_dir / f"{self.design}_cts.map",
                self.out_dir / f"{self.design}_placement.map",
                self.out_dir / "placement.map",
            ]
            for c in candidates:
                if c.exists():
                    self.placement_map = c
                    break

    @property
    def out_dir(self) -> Path:
        return self.build_dir / self.design

    def out_path(self, suffix: str) -> Path:
        """Generate output path: build/<design>/<design>_<suffix>"""
        return self.out_dir / f"{self.design}_{suffix}"

    def report_path(self, suffix: str) -> Path:
        """Generate report input path: build/<design>/<design>_<suffix>"""
        return self.out_dir / f"{self.design}_{suffix}"


# ═══════════════════════════════════════════════════════════════════════════════
# Result Container
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VizResult:
    """Result of a single visualization stage."""
    stage: str
    ok: bool
    path: Optional[Path] = None
    error: Optional[str] = None
    skipped: bool = False
    missing_input: bool = False  # True if failure is due to missing input file

    def __str__(self):
        if self.skipped:
            return f"  ⊘ {self.stage}: skipped"
        sym = "✓" if self.ok else ("⚠" if self.missing_input else "✗")
        detail = str(self.path) if self.path else self.error
        return f"  {sym} {self.stage}: {detail}"


class MissingDataError(Exception):
    """Raised when input file exists but contains no usable data."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Fabric DB Helpers (inlined to avoid circular imports)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_fabric_db(cfg: VizConfig) -> Dict[str, Any]:
    """Load fabric database using build_fabric_db."""
    # Import here to avoid circular dependency
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from build_fabric_db import build_fabric_db
    return build_fabric_db(str(cfg.fabric_cells), str(cfg.pins_yaml), str(cfg.fabric_yaml))


def _load_logical_db(cfg: VizConfig) -> Tuple[Dict[str, Any], Any]:
    """Load logical database from design JSON."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from parse_design import parse_design_json
    return parse_design_json(str(cfg.design_json))


def _normalize_cells_by_tile(fabric_db: Dict[str, Any]):
    """Yield (tile_name, x, y, width, height, cell_dict) for each fabric cell."""
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


def _extract_die_core_bbox(fabric_db: Dict[str, Any]) -> Tuple[Optional[Tuple], Optional[Tuple]]:
    """Extract die and core bounding boxes from pin_placement."""
    pin_placement = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_bbox = None
    core_bbox = None

    die_info = pin_placement.get("die", {})
    if die_info:
        dw = die_info.get("width_um")
        dh = die_info.get("height_um")
        if dw is not None and dh is not None:
            die_bbox = (0.0, 0.0, float(dw), float(dh))

    core_info = pin_placement.get("core", {})
    if core_info and die_bbox:
        cw = core_info.get("width_um")
        ch = core_info.get("height_um")
        if cw is not None and ch is not None:
            cx0 = (die_bbox[2] - cw) / 2.0
            cy0 = (die_bbox[3] - ch) / 2.0
            core_bbox = (cx0, cy0, cx0 + cw, cy0 + ch)

    return die_bbox, core_bbox


def _collect_pin_list(pins) -> List[Dict[str, Any]]:
    """Normalize pins into list of {name, x, y}."""
    pin_list = []
    if pins is None:
        return pin_list
    if isinstance(pins, dict) and "pins" in pins:
        pins = pins["pins"]
    if isinstance(pins, dict):
        for name, info in pins.items():
            if isinstance(info, dict):
                x = info.get("x") or info.get("cx") or info.get("x_um")
                y = info.get("y") or info.get("cy") or info.get("y_um")
                if x is not None and y is not None:
                    pin_list.append({"name": name, "x": float(x), "y": float(y)})
    elif isinstance(pins, list):
        for item in pins:
            if isinstance(item, dict):
                x = item.get("x") or item.get("cx") or item.get("x_um")
                y = item.get("y") or item.get("cy") or item.get("y_um")
                if x is not None and y is not None:
                    pin_list.append({"name": item.get("name", ""), "x": float(x), "y": float(y)})
    return pin_list


def _extract_cell_type(cell: Dict[str, Any]) -> str:
    """Extract short cell type name for legend."""
    cell_type = cell.get("cell_type", "")
    if cell_type:
        parts = cell_type.split("__")
        if len(parts) > 1:
            base = parts[-1]
            m = re.match(r"([a-z]+)\d*", base)
            if m:
                return m.group(1).upper()
            return base.upper()
    template = cell.get("template_name", "")
    if template:
        m = re.match(r"R\d+_([A-Z]+)_\d+", template)
        if m:
            return m.group(1)
    return "UNKNOWN"


def _get_all_fabric_cell_names(fabric_db: Dict[str, Any]) -> List[str]:
    """Return list of all fabric cell names."""
    return [cell.get("name") for _, _, _, _, _, cell in _normalize_cells_by_tile(fabric_db) if cell.get("name")]


# ═══════════════════════════════════════════════════════════════════════════════
# Placement Map Parser
# ═══════════════════════════════════════════════════════════════════════════════

def _read_placement_map(map_path: Path) -> Dict[str, Tuple[float, float]]:
    """Parse placement .map file → {instance: (x, y)}."""
    placement = {}
    if not map_path or not map_path.exists():
        return placement
    with open(map_path) as f:
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
                    instance = parts[arrow_idx + 1]
                    placement[instance] = (x, y)
            elif len(parts) == 3:
                port_name, x, y = parts
                placement[port_name] = (float(x), float(y))
    return placement


# ═══════════════════════════════════════════════════════════════════════════════
# Plot Functions
# ═══════════════════════════════════════════════════════════════════════════════

def plot_layout(cfg: VizConfig, fabric_db: Dict[str, Any] = None) -> Path:
    """
    Render fabric ground-truth layout with die/core boundaries, cells, and pins.
    """
    if fabric_db is None:
        fabric_db = _load_fabric_db(cfg)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("layout.png")

    pins = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_bbox, core_bbox = _extract_die_core_bbox(fabric_db)

    cell_entries = list(_normalize_cells_by_tile(fabric_db))
    if not cell_entries:
        raise ValueError("No fabric cells found")

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
        t = _extract_cell_type(cell)
        if t not in type_to_idx:
            type_to_idx[t] = len(type_to_idx)

    fig, ax = plt.subplots(figsize=cfg.figsize)
    ax.set_aspect('equal', adjustable='box')

    # Die and core rectangles
    ax.add_patch(patches.Rectangle((die_bbox[0], die_bbox[1]),
                                    die_bbox[2] - die_bbox[0], die_bbox[3] - die_bbox[1],
                                    fill=False, lw=2, edgecolor='blue'))
    ax.add_patch(patches.Rectangle((core_bbox[0], core_bbox[1]),
                                    core_bbox[2] - core_bbox[0], core_bbox[3] - core_bbox[1],
                                    fill=False, lw=2, ls='--', edgecolor='green'))

    cmap = matplotlib.colormaps.get_cmap(cfg.cmap)

    # Draw cells
    for tile_name, cx, cy, w, h, cell in cell_entries:
        t = _extract_cell_type(cell)
        idx = type_to_idx.get(t, 0)
        if w is None or h is None:
            w, h = 1.0, 1.0
        if cx is None or cy is None:
            cx, cy = 0.0, 0.0
        color = cmap(idx % cmap.N)
        ax.add_patch(patches.Rectangle((cx, cy), w, h, facecolor=color,
                                        edgecolor='black', lw=0.4, alpha=cfg.alpha))

    # Draw pins
    pin_list = _collect_pin_list(pins)
    if pin_list:
        ax.scatter([p["x"] for p in pin_list], [p["y"] for p in pin_list],
                   s=18, marker='o', color='red', zorder=10)

    # Legend
    handles = [
        patches.Patch(facecolor='none', edgecolor='blue', lw=2, label='Die'),
        patches.Patch(facecolor='none', edgecolor='green', lw=2, ls='--', label='Core'),
    ]
    for t, idx in list(type_to_idx.items())[:18]:
        handles.append(patches.Patch(facecolor=cmap(idx % cmap.N), alpha=cfg.alpha, label=t))
    ax.legend(handles=handles, title="Components", loc='upper right', fontsize=8)

    ax.set_xlim(die_bbox[0] - 0.05*(die_bbox[2]-die_bbox[0]), die_bbox[2] + 0.05*(die_bbox[2]-die_bbox[0]))
    ax.set_ylim(die_bbox[1] - 0.05*(die_bbox[3]-die_bbox[1]), die_bbox[3] + 0.05*(die_bbox[3]-die_bbox[1]))
    ax.set_xlabel("X (μm)")
    ax.set_ylabel("Y (μm)")
    ax.set_title(f"{cfg.design} Fabric Layout")
    ax.grid(True, lw=0.3, alpha=0.5)

    fig.savefig(out_path, dpi=cfg.dpi, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved layout → {out_path}")
    return out_path


def plot_density(cfg: VizConfig, fabric_db: Dict[str, Any] = None) -> Optional[Path]:
    """
    Render placement density heatmap from .map file.
    """
    if cfg.placement_map is None or not cfg.placement_map.exists():
        raise FileNotFoundError(f"Placement map not found (tried auto-discovery)")

    if fabric_db is None:
        fabric_db = _load_fabric_db(cfg)

    placement = _read_placement_map(cfg.placement_map)
    if not placement:
        raise ValueError("No placement coords in map file")

    coords = list(placement.values())
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    extent = (max(xs), max(ys))

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("density.png")

    plt.figure(figsize=(6, 6))
    H, xedges, yedges = np.histogram2d(xs, ys, bins=cfg.heatmap_bins,
                                       range=[[0, extent[0]], [0, extent[1]]])
    H = np.flipud(np.rot90(H))
    plt.imshow(H, extent=[0, extent[0], 0, extent[1]], aspect='auto', cmap='hot')
    plt.colorbar(label='Cell count')
    plt.title(f'{cfg.design} Placement Density')
    plt.xlabel('X (μm)')
    plt.ylabel('Y (μm)')
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved density → {out_path}")
    return out_path


def plot_net_length(cfg: VizConfig) -> Optional[Path]:
    """
    Render net HPWL histogram from logical_db + placement map.
    """
    if cfg.placement_map is None or not cfg.placement_map.exists():
        raise FileNotFoundError("Placement map not found")
    if not cfg.design_json.exists():
        raise FileNotFoundError(f"Design JSON not found: {cfg.design_json}")

    logical_db, _ = _load_logical_db(cfg)
    placement = _read_placement_map(cfg.placement_map)

    net_hpwl = []
    for net_id, net in logical_db.get('nets', {}).items():
        xs, ys = [], []
        for endpoint in net.get('connections', []):
            node = endpoint[0] if isinstance(endpoint, (list, tuple)) else endpoint
            if node in placement:
                x, y = placement[node]
                xs.append(x)
                ys.append(y)
        if len(xs) >= 2:
            hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            net_hpwl.append(hpwl)

    if not net_hpwl:
        raise ValueError("No nets with valid HPWL")

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("net_length.png")

    plt.figure(figsize=(6, 4))
    plt.hist(net_hpwl, bins=cfg.hist_bins)
    plt.xlabel("Net HPWL (μm)")
    plt.ylabel("Count")
    plt.title(f"{cfg.design} Net Length Distribution")
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved net_length → {out_path}")
    return out_path


def plot_congestion(cfg: VizConfig, fabric_db: Dict[str, Any] = None) -> Optional[Path]:
    """
    Render congestion heatmap from _congestion.rpt.
    """
    rpt_path = cfg.report_path("congestion.rpt")
    if not rpt_path.exists():
        raise FileNotFoundError(f"Congestion report not found: {rpt_path}")

    xs, ys, vals = [], [], []
    float_re = r"([-+]?[0-9]*\.?[0-9]+)"
    coord_val_re = re.compile(rf"\b{float_re}\s+{float_re}\s+{float_re}%?\b")

    with open(rpt_path) as f:
        for line in f:
            m = coord_val_re.search(line)
            if m:
                xs.append(float(m.group(1)))
                ys.append(float(m.group(2)))
                vals.append(float(m.group(3)))

    if not xs:
        # Fallback: try grid of floats
        matrix = []
        with open(rpt_path) as f:
            for line in f:
                parts = re.findall(float_re, line)
                if parts:
                    matrix.append([float(p) for p in parts])
        if not matrix:
            raise MissingDataError(f"No congestion data in report: {rpt_path}")
        maxlen = max(len(r) for r in matrix)
        M = np.zeros((len(matrix), maxlen))
        for i, r in enumerate(matrix):
            M[i, :len(r)] = r

        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        out_path = cfg.out_path("congestion.png")
        plt.figure(figsize=(8, 6))
        plt.imshow(M, cmap='hot', aspect='auto')
        plt.colorbar(label='Congestion')
        plt.title(f'{cfg.design} Congestion')
        plt.tight_layout()
        plt.savefig(out_path, dpi=200)
        plt.close()
        print(f"Saved congestion → {out_path}")
        return out_path

    # x,y,value triples
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("congestion.png")

    H, xedges, yedges = np.histogram2d(xs, ys, bins=cfg.heatmap_bins, weights=vals,
                                       range=[[min(xs), max(xs)], [min(ys), max(ys)]])
    counts, _, _ = np.histogram2d(xs, ys, bins=cfg.heatmap_bins,
                                  range=[[min(xs), max(xs)], [min(ys), max(ys)]])
    with np.errstate(divide='ignore', invalid='ignore'):
        H = np.divide(H, counts)
        H[np.isnan(H)] = 0.0

    plt.figure(figsize=(8, 6))
    plt.imshow(np.flipud(H), extent=[min(xs), max(xs), min(ys), max(ys)], cmap='hot', aspect='auto')
    plt.colorbar(label='Congestion (%)')
    plt.title(f'{cfg.design} Congestion Heatmap')
    plt.xlabel('X (μm)')
    plt.ylabel('Y (μm)')
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved congestion → {out_path}")
    return out_path


def plot_slack_histogram(cfg: VizConfig) -> Optional[Path]:
    """
    Render slack histogram from _setup_timing.rpt.
    """
    rpt_path = cfg.report_path("setup_timing.rpt")
    if not rpt_path.exists():
        raise FileNotFoundError(f"Setup timing report not found: {rpt_path}")

    slacks = []
    slack_re = re.compile(r'^\s*([-+]?[0-9]*\.?[0-9]+)\s+slack\s*\(', re.IGNORECASE)

    with open(rpt_path) as f:
        for line in f:
            m = slack_re.search(line)
            if m:
                slacks.append(float(m.group(1)))

    if not slacks:
        raise MissingDataError(f"No slacks parsed from {rpt_path}")

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("slack.png")

    plt.figure(figsize=(6, 4))
    plt.hist(slacks, bins=cfg.hist_bins)
    plt.xlabel('Slack (ns)')
    plt.ylabel('Count')
    plt.title(f'{cfg.design} Endpoint Slack Distribution')
    plt.axvline(x=0, color='r', linestyle='--', lw=1, label='Zero slack')
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()
    print(f"Saved slack → {out_path}")
    return out_path


def plot_critical_path(cfg: VizConfig, fabric_db: Dict[str, Any] = None) -> Optional[Path]:
    """
    Render critical path overlay on fabric layout.
    """
    rpt_path = cfg.report_path("setup_timing.rpt")
    if not rpt_path.exists():
        raise FileNotFoundError(f"Setup timing report not found: {rpt_path}")

    if fabric_db is None:
        fabric_db = _load_fabric_db(cfg)

    fabric_names = set(_get_all_fabric_cell_names(fabric_db))

    with open(rpt_path) as f:
        content = f.read()

    # Split into path sections
    path_sections = re.split(r'\nStartpoint:', content)
    if path_sections and not path_sections[0].strip().startswith('Startpoint'):
        path_sections = path_sections[1:]

    worst_slack = float('inf')
    worst_section = None
    for section in path_sections:
        if 'Path Group: clk' in section or 'Path Group:clk' in section:
            m = re.search(r'([-\d.]+)\s+slack', section, re.IGNORECASE)
            if m:
                slack = float(m.group(1))
                if slack < worst_slack:
                    worst_slack = slack
                    worst_section = section

    if not worst_section:
        raise MissingDataError("No clock paths found in setup report")

    cell_re = re.compile(r'(T\d+Y\d+__R\d+_[A-Z]+_\d+)')
    path_cells = []
    for line in worst_section.split('\n'):
        if '__' in line and 'sky130' in line:
            m = cell_re.search(line)
            if m:
                name = m.group(1)
                if name in fabric_names and (not path_cells or path_cells[-1] != name):
                    path_cells.append(name)

    if not path_cells:
        raise MissingDataError("No path cells extracted from worst path")

    # Build name → center lookup
    name_to_center = {}
    for _, cx, cy, w, h, cell in _normalize_cells_by_tile(fabric_db):
        name = cell.get('name')
        if not name:
            continue
        if w is None: w = 1.0
        if h is None: h = 1.0
        if cx is None: cx = 0.0
        if cy is None: cy = 0.0
        name_to_center[name] = (cx + w/2, cy + h/2)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = cfg.out_path("critical_path.png")

    # Reuse layout plot then overlay path
    pins = fabric_db.get("fabric", {}).get("pin_placement", {})
    die_bbox, core_bbox = _extract_die_core_bbox(fabric_db)
    cell_entries = list(_normalize_cells_by_tile(fabric_db))

    if die_bbox is None:
        xs, ys = [], []
        for _, cx, cy, w, h, _ in cell_entries:
            if cx is not None and cy is not None:
                xs.append(cx)
                ys.append(cy)
                if w: xs.append(cx + w)
                if h: ys.append(cy + h)
        margin = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys)) if xs else 5
        die_bbox = (min(xs, default=0) - margin, min(ys, default=0) - margin,
                    max(xs, default=100) + margin, max(ys, default=100) + margin)

    if core_bbox is None:
        dx, dy = die_bbox[2] - die_bbox[0], die_bbox[3] - die_bbox[1]
        core_bbox = (die_bbox[0] + 0.08*dx, die_bbox[1] + 0.08*dy,
                     die_bbox[2] - 0.08*dx, die_bbox[3] - 0.08*dy)

    type_to_idx = {}
    for _, _, _, _, _, cell in cell_entries:
        t = _extract_cell_type(cell)
        if t not in type_to_idx:
            type_to_idx[t] = len(type_to_idx)

    fig, ax = plt.subplots(figsize=cfg.figsize)
    ax.set_aspect('equal', adjustable='box')

    ax.add_patch(patches.Rectangle((die_bbox[0], die_bbox[1]),
                                    die_bbox[2] - die_bbox[0], die_bbox[3] - die_bbox[1],
                                    fill=False, lw=2, edgecolor='blue'))
    ax.add_patch(patches.Rectangle((core_bbox[0], core_bbox[1]),
                                    core_bbox[2] - core_bbox[0], core_bbox[3] - core_bbox[1],
                                    fill=False, lw=2, ls='--', edgecolor='green'))

    cmap_obj = matplotlib.colormaps.get_cmap(cfg.cmap)
    for _, cx, cy, w, h, cell in cell_entries:
        t = _extract_cell_type(cell)
        idx = type_to_idx.get(t, 0)
        if w is None: w = 1.0
        if h is None: h = 1.0
        if cx is None: cx = 0.0
        if cy is None: cy = 0.0
        ax.add_patch(patches.Rectangle((cx, cy), w, h, facecolor=cmap_obj(idx % cmap_obj.N),
                                        edgecolor='black', lw=0.4, alpha=0.3))

    # Draw critical path
    pts_x, pts_y = [], []
    for name in path_cells:
        if name in name_to_center:
            x, y = name_to_center[name]
            pts_x.append(x)
            pts_y.append(y)

    if len(pts_x) >= 2:
        ax.plot(pts_x, pts_y, color='red', linewidth=3.0, alpha=0.95, zorder=20)
        ax.scatter(pts_x, pts_y, color='red', s=40, zorder=21)

    ax.set_xlim(die_bbox[0] - 0.05*(die_bbox[2]-die_bbox[0]), die_bbox[2] + 0.05*(die_bbox[2]-die_bbox[0]))
    ax.set_ylim(die_bbox[1] - 0.05*(die_bbox[3]-die_bbox[1]), die_bbox[3] + 0.05*(die_bbox[3]-die_bbox[1]))
    ax.set_xlabel("X (μm)")
    ax.set_ylabel("Y (μm)")
    ax.set_title(f"{cfg.design} Critical Path (slack={worst_slack:.3f}ns)")
    ax.grid(True, lw=0.3, alpha=0.5)

    fig.savefig(out_path, dpi=cfg.dpi, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved critical_path → {out_path}")
    return out_path


def plot_cts_tree(cfg: VizConfig) -> Optional[Path]:
    """
    Render CTS tree overlay (delegates to cts_overlay module).
    Uses the new plot_cts_tree_overlay_from_tree which reads directly from clock_tree.json.
    """
    cts_json_path = cfg.out_dir / f"{cfg.design}_clock_tree.json"
    if not cts_json_path.exists():
        raise FileNotFoundError(f"Clock tree JSON not found: {cts_json_path}")

    # Load clock tree JSON
    with open(cts_json_path) as f:
        clock_tree = json.load(f)

    fabric_db = _load_fabric_db(cfg)
    out_path = cfg.out_path("cts_tree.png")

    # Try new function first (from Routing branch), fallback to old
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from cts_overlay import plot_cts_tree_overlay_from_tree
        plot_cts_tree_overlay_from_tree(clock_tree, fabric_db, str(out_path))
    except ImportError:
        # Fallback to old function if new one not available
        from cts_overlay import plot_cts_tree_overlay
        if cfg.placement_map is None or not cfg.placement_map.exists():
            raise FileNotFoundError("Placement map not found for CTS overlay (legacy mode)")
        logical_db, _ = _load_logical_db(cfg)
        plot_cts_tree_overlay(logical_db, str(cfg.placement_map), str(cts_json_path), fabric_db, str(out_path))
    print(f"Saved cts_tree → {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

# Stage registry: (name, function, requires_map, requires_report)
STAGES = [
    ("layout",        plot_layout,          False, None),
    ("density",       plot_density,         True,  None),
    ("net_length",    plot_net_length,      True,  None),
    ("congestion",    plot_congestion,      False, "congestion.rpt"),
    ("slack",         plot_slack_histogram, False, "setup_timing.rpt"),
    ("critical_path", plot_critical_path,   False, "setup_timing.rpt"),
    ("cts_tree",      plot_cts_tree,        False, None),  # No longer requires map with new function
]


def run_all(cfg: VizConfig, only: List[str] = None, skip: List[str] = None) -> List[VizResult]:
    """
    Run all visualization stages, returning results for each.

    Args:
        cfg: VizConfig with design name and paths
        only: If set, only run these stages
        skip: If set, skip these stages
    """
    results = []
    fabric_db = None  # Lazy-load and cache

    for name, fn, needs_map, needs_rpt in STAGES:
        # Filter logic
        if only and name not in only:
            results.append(VizResult(name, False, skipped=True))
            continue
        if skip and name in skip:
            results.append(VizResult(name, False, skipped=True))
            continue

        # Pre-check requirements (missing input = soft failure)
        if needs_map and (cfg.placement_map is None or not cfg.placement_map.exists()):
            results.append(VizResult(name, False, error="No placement map found", missing_input=True))
            continue
        if needs_rpt:
            rpt = cfg.report_path(needs_rpt)
            if not rpt.exists():
                results.append(VizResult(name, False, error=f"Missing {needs_rpt}", missing_input=True))
                continue

        # Run
        try:
            if fabric_db is None and name in ("layout", "congestion", "critical_path", "cts_tree"):
                fabric_db = _load_fabric_db(cfg)
            
            if name in ("layout", "density", "congestion", "critical_path"):
                path = fn(cfg, fabric_db)
            else:
                path = fn(cfg)
            results.append(VizResult(name, True, path=path))
        except (FileNotFoundError, MissingDataError) as e:
            # Missing input file or no usable data in file
            results.append(VizResult(name, False, error=str(e), missing_input=True))
        except Exception as e:
            # Real error (parser bug, etc.)
            results.append(VizResult(name, False, error=str(e), missing_input=False))

    return results


def print_summary(results: List[VizResult], design: str):
    """Print a formatted summary of results."""
    print(f"\n{'='*60}")
    print(f"Visualization Summary: {design}")
    print(f"{'='*60}")

    passed = sum(1 for r in results if r.ok)
    skipped = sum(1 for r in results if r.skipped)
    missing = sum(1 for r in results if not r.ok and not r.skipped and r.missing_input)
    failed = sum(1 for r in results if not r.ok and not r.skipped and not r.missing_input)
    total = len(results) - skipped

    for r in results:
        print(r)

    print(f"\n{'='*60}")
    summary_parts = [f"Passed: {passed}/{total}"]
    if missing:
        summary_parts.append(f"{missing} missing inputs")
    if failed:
        summary_parts.append(f"{failed} errors")
    if skipped:
        summary_parts.append(f"{skipped} skipped")
    print(" | ".join(summary_parts))
    print(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Structured-ASIC Visualization Layer")
    parser.add_argument("--design", help="Design name (e.g., 6502)")
    parser.add_argument("--only", nargs="+", help="Only run these stages")
    parser.add_argument("--skip", nargs="+", help="Skip these stages")
    parser.add_argument("--map", help="Path to placement .map file")
    parser.add_argument("--list", action="store_true", help="List available stages")
    parser.add_argument("--strict", action="store_true", 
                        help="Exit with error even if failures are just missing inputs")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-stage output")
    args = parser.parse_args()

    if args.list:
        print("Available stages:")
        for name, _, needs_map, needs_rpt in STAGES:
            reqs = []
            if needs_map: reqs.append(".map")
            if needs_rpt: reqs.append(needs_rpt)
            req_str = f" (requires: {', '.join(reqs)})" if reqs else ""
            print(f"  • {name}{req_str}")
        return

    if not args.design:
        parser.error("--design is required (unless using --list)")

    cfg = VizConfig(design=args.design)
    if args.map:
        cfg.placement_map = Path(args.map)

    results = run_all(cfg, only=args.only, skip=args.skip)
    
    if not args.quiet:
        print_summary(results, args.design)

    # Determine exit code
    hard_failures = [r for r in results if not r.ok and not r.skipped and not r.missing_input]
    soft_failures = [r for r in results if not r.ok and not r.skipped and r.missing_input]
    
    if hard_failures:
        # Real errors always cause exit 1
        sys.exit(1)
    elif soft_failures and args.strict:
        # Missing inputs only cause exit 1 in strict mode
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

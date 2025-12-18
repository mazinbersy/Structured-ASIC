#!/usr/bin/env python3
"""
visualization/config.py
-----------------------
Configuration dataclasses and helper loaders for the visualization layer.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
import sys


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
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


# ═══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════════════════════

class MissingDataError(Exception):
    """Raised when input file exists but contains no usable data."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Loader Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_fabric_db(cfg: VizConfig) -> Dict[str, Any]:
    """Load fabric database using build_fabric_db."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from build_fabric_db import build_fabric_db
    return build_fabric_db(str(cfg.fabric_cells), str(cfg.pins_yaml), str(cfg.fabric_yaml))


def load_logical_db(cfg: VizConfig) -> Tuple[Dict[str, Any], Any]:
    """Load logical database from design JSON."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from parse_design import parse_design_json
    return parse_design_json(str(cfg.design_json))


def read_placement_map(map_path: Path) -> Dict[str, Tuple[float, float]]:
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

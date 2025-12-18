"""
visualization package
---------------------
Structured-ASIC visualization layer.

Usage:
    from visualization import VizConfig, run_all, main
    
    # Programmatic usage
    cfg = VizConfig(design="6502")
    results = run_all(cfg)
    
    # CLI usage
    python -m visualization --design 6502
"""

from .config import VizConfig, VizResult, MissingDataError
from .pipeline import run_all, print_summary, main, STAGES
from .stages import (
    plot_layout,
    plot_density,
    plot_net_length,
    plot_congestion,
    plot_slack_histogram,
    plot_critical_path,
    plot_cts_tree,
)

__all__ = [
    # Config
    "VizConfig",
    "VizResult", 
    "MissingDataError",
    # Pipeline
    "run_all",
    "print_summary",
    "main",
    "STAGES",
    # Individual stages
    "plot_layout",
    "plot_density",
    "plot_net_length",
    "plot_congestion",
    "plot_slack_histogram",
    "plot_critical_path",
    "plot_cts_tree",
]

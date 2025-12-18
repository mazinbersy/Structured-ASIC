#!/usr/bin/env python3
"""
visualization/pipeline.py
-------------------------
Stage registry, orchestrator, and CLI for the visualization layer.
"""

from pathlib import Path
from typing import List, Optional
import sys
import argparse

from .config import VizConfig, VizResult, MissingDataError, load_fabric_db
from .stages import (
    plot_layout,
    plot_density,
    plot_net_length,
    plot_congestion,
    plot_slack_histogram,
    plot_critical_path,
    plot_cts_tree,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Stage Registry
# ═══════════════════════════════════════════════════════════════════════════════

# (name, function, requires_map, requires_report)
STAGES = [
    ("layout",        plot_layout,          False, None),
    ("density",       plot_density,         True,  None),
    ("net_length",    plot_net_length,      True,  None),
    ("congestion",    plot_congestion,      False, "congestion.rpt"),
    ("slack",         plot_slack_histogram, False, "setup_timing.rpt"),
    ("critical_path", plot_critical_path,   False, "setup_timing.rpt"),
    ("cts_tree",      plot_cts_tree,        False, None),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

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
                fabric_db = load_fabric_db(cfg)
            
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


# ═══════════════════════════════════════════════════════════════════════════════
# Summary Printer
# ═══════════════════════════════════════════════════════════════════════════════

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
    """Main CLI entrypoint."""
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

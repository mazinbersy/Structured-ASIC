#!/usr/bin/env python3
"""
sa_analysis.py

Consolidated SA/HPWL plotting wrapper.
Usage examples:

python sa_analysis.py --csv build/refine_temp_experiments/sa_refine_temp_results.csv --x runtime_s --y final_hpwl --label "T0={initial_temp},pref={prob_refine}" --out refine_temp_pareto.png --title "Refine/Temp Sweep"

python sa_analysis.py --csv build/window_cooling_experiments/sa_window_cooling_results.csv --x runtime_s --y final_hpwl --label "beta={cooling_rate}, W={w_initial}" --out window_cooling_pareto.png

This script centralizes what used to be in `plot_sa_results.py` and `plot_sa_window_cooling_results.py`.
"""

import argparse
from visualization.plot_utils import load_csv_xy_labels, plot_pareto


def label_builder_factory(fmt: str):
    def lb(row: dict):
        try:
            return fmt.format(**row)
        except Exception:
            # Fallback: attempt simple key substitution
            try:
                return ",".join([f"{k}={row[k]}" for k in fmt.split(",") if k in row])
            except Exception:
                return ""
    return lb


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, help="CSV file with experiment results")
    p.add_argument("--x", default="runtime_s", help="CSV column to use for X axis (default runtime_s)")
    p.add_argument("--y", default="final_hpwl", help="CSV column to use for Y axis (default final_hpwl)")
    p.add_argument("--label", default=None, help="Label format string (python format) using CSV headers, e.g. 'T0={initial_temp}, Pref={prob_refine}'")
    p.add_argument("--out", required=True, help="Output PNG path")
    p.add_argument("--title", default=None, help="Plot title")
    args = p.parse_args()

    if args.label:
        lb = label_builder_factory(args.label)
    else:
        lb = None

    x, y, labels, rows = load_csv_xy_labels(args.csv, args.x, args.y, lb)

    plot_pareto(x, y, labels, out_png=args.out, title=args.title)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
sa_window_cooling_experiments.py

Sweep SA:
  - cooling_rate  (alpha)
  - window size (w_initial)

WITHOUT modifying the simulated annealing code.

Results stored in CSV and per-run stats files.
"""

import csv
import os
import time
import random
import json
from itertools import product

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
from placer import initial_placement, calculate_hpwl, write_map_file
from optimized import simulated_annealing, SAConfig


# ================================================================
# SWEEP PARAMETERS
# ================================================================

cooling_rates = [0.90, 0.95, 0.98]
window_initials = [0.3, 0.5, 0.7, 1.0]

seeds_per_config = 1   # change to 1 if only quick sweep


# ================================================================
# I/O PATHS
# ================================================================

DESIGN_JSON = "designs/6502_mapped.json"
FABRIC_ARGS = (
    "fabric/fabric_cells.yaml",
    "fabric/pins.yaml",
    "fabric/fabric.yaml"
)

OUT_DIR = "build/window_cooling_experiments"
os.makedirs(OUT_DIR, exist_ok=True)

CSV_PATH = os.path.join(OUT_DIR, "sa_window_cooling_results.csv")

fieldnames = [
    "cooling_rate",
    "w_initial",
    "seed",
    "runtime_s",
    "final_hpwl",
    "initial_hpwl",
    "iterations",
    "accepted_moves",
    "rejected_moves",
    "refine_moves",
    "explore_moves",
]


# ================================================================
# LOAD DESIGN + FABRIC ONCE
# ================================================================

print("Loading design and fabric...")
fabric_db = build_fabric_db(*FABRIC_ARGS)
logical_db, netlist_graph = parse_design_json(DESIGN_JSON)

print("Running greedy placement...")
greedy_placement = initial_placement(fabric_db, logical_db, netlist_graph)
greedy_hpwl = calculate_hpwl(netlist_graph, greedy_placement, logical_db)
print(f"Greedy HPWL = {greedy_hpwl:.2f}")


# ================================================================
# RUN EXPERIMENTS
# ================================================================

with open(CSV_PATH, "w", newline="") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    for alpha, w_i in product(cooling_rates, window_initials):
        for seed in range(seeds_per_config):

            random_seed = random.randint(0, 2**32 - 1)
            random.seed(random_seed)

            # Create a new config
            cfg = SAConfig()
            cfg.cooling_rate = alpha
            cfg.w_initial = w_i

            print(f"[RUN] alpha={alpha}, w_initial={w_i}, seed={seed}")

            start_place = dict(greedy_placement)

            t0 = time.time()
            best_place, stats = simulated_annealing(
                fabric_db,
                logical_db,
                netlist_graph,
                start_place,
                cfg
            )
            runtime = time.time() - t0

            best_hpwl = stats.get("best_cost")
            iterations = stats.get("iterations")

            # Save .map placement file
            fname = f"alpha{alpha}_w{w_i}_seed{seed}.map"
            fpath = os.path.join(OUT_DIR, fname)
            write_map_file(best_place, filename=fpath)

            # Save .json stats
            with open(fpath + "_stats.json", "w") as jf:
                json.dump(
                    {"stats": stats, "config": cfg.__dict__, "seed": random_seed},
                    jf,
                    indent=2
                )

            # Record in CSV
            writer.writerow({
                "cooling_rate": alpha,
                "w_initial": w_i,
                "seed": seed,
                "runtime_s": round(runtime, 3),
                "final_hpwl": round(best_hpwl, 3),
                "initial_hpwl": round(greedy_hpwl, 3),
                "iterations": iterations,
                "accepted_moves": stats.get("accepted_moves"),
                "rejected_moves": stats.get("rejected_moves"),
                "refine_moves": stats.get("refine_moves"),
                "explore_moves": stats.get("explore_moves"),
            })

print("\nAll experiments complete!")
print("Results written to:", CSV_PATH)

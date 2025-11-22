#!/usr/bin/env python3
"""
sa_refine_temp_experiments.py

Sweep over both:

* initial_temp
* prob_refine (vs prob_explore)

while keeping other SA settings fixed.
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

# ===================================================================

# FIXED "BEST-KNOWN" KNOBS

# ===================================================================

BEST_COOLING = 0.92          # alpha
BEST_MOVES_PER_TEMP = 200    # N

# ===================================================================

# SWEEP PARAMETERS

# ===================================================================

initial_temps = [25.0, 50.0, 100.0, 200.0]
refine_probs  = [0.25, 0.50, 0.70, 0.85]

seeds_per_setting = 1

# ===================================================================

# FILE PATHS

# ===================================================================

DESIGN_JSON = "designs/6502_mapped.json"
FABRIC_ARGS = ("fabric/fabric_cells.yaml", "fabric/pins.yaml", "fabric/fabric.yaml")

OUT_DIR = "build/refine_temp_experiments"
os.makedirs(OUT_DIR, exist_ok=True)

CSV_PATH = os.path.join(OUT_DIR, "sa_refine_temp_results.csv")

fieldnames = [
"initial_temp",
"prob_refine",
"prob_explore",
"seed",
"runtime_s",
"final_hpwl",
"initial_hpwl",
"iterations",
"accepted_moves",
"rejected_moves",
"cooling_rate",
"moves_per_temp"
]

# ===================================================================

# LOAD DESIGN AND FABRIC ONCE

# ===================================================================

print("Loading inputs...")
fabric_db = build_fabric_db(*FABRIC_ARGS)
logical_db, netlist_graph = parse_design_json(DESIGN_JSON)

print("Computing greedy placement...")
greedy_place = initial_placement(fabric_db, logical_db, netlist_graph)
greedy_hpwl = calculate_hpwl(netlist_graph, greedy_place, logical_db)
print(f"Greedy HPWL = {greedy_hpwl:.2f}")

# ===================================================================

# RUN EXPERIMENTS

# ===================================================================

with open(CSV_PATH, "w", newline="") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    
    for T0, pref_refine in product(initial_temps, refine_probs):
        pref_explore = 1.0 - pref_refine

        for seed in range(seeds_per_setting):

            # independently seeded runs
            random_seed = random.randint(0, 2**32 - 1)
            random.seed(random_seed)

            cfg = SAConfig()
            cfg.initial_temp     = T0
            cfg.cooling_rate     = BEST_COOLING
            cfg.moves_per_temp   = BEST_MOVES_PER_TEMP
            cfg.prob_refine      = pref_refine
            cfg.prob_explore     = pref_explore
            cfg.max_iterations   = 200000    # safety

            print(f"[RUN] T0={T0}, refine={pref_refine:.2f}, seed={seed}")

            start_place = dict(greedy_place)
            t0 = time.time()
            best_place, stats = simulated_annealing(
                fabric_db, logical_db, netlist_graph,
                start_place, cfg
            )
            t1 = time.time()
            runtime = t1 - t0

            best_hpwl = stats.get("best_cost")
            its = stats.get("iterations")

            # Save placement map
            fname = f"T0_{int(T0)}_refine{int(pref_refine*100)}_seed{seed}.map"
            fpath = os.path.join(OUT_DIR, fname)
            write_map_file(best_place, filename=fpath)

            # Store stats JSON as well
            with open(fpath + "_stats.json", "w") as jf:
                json.dump(
                    {
                        "stats": stats,
                        "config": cfg.__dict__,
                        "random_seed": random_seed
                    },
                    jf,
                    indent=2
                )

            # Save summary row
            writer.writerow({
                "initial_temp": T0,
                "prob_refine": pref_refine,
                "prob_explore": pref_explore,
                "seed": seed,
                "runtime_s": round(runtime, 3),
                "final_hpwl": round(best_hpwl, 3),
                "initial_hpwl": round(greedy_hpwl, 3),
                "iterations": its,
                "accepted_moves": stats.get("accepted_moves"),
                "rejected_moves": stats.get("rejected_moves"),
                "cooling_rate": BEST_COOLING,
                "moves_per_temp": BEST_MOVES_PER_TEMP
            })
    

print("\nFull sweep finished!")
print("Results saved to:", CSV_PATH)

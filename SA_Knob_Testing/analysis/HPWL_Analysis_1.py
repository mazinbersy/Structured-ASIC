#!/usr/bin/env python3
"""
sa_experiments.py

Run SA parameter sweep experiments for Structured-ASIC placer.
Outputs: CSV with results and one JSON per run (optional).
"""

import csv
import os
import time
import random
import json
from itertools import product

# import your modules (adjust imports if your files/paths differ)
from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
from placer import initial_placement, calculate_hpwl, write_map_file
from optimized import simulated_annealing, SAConfig

# ---------- Experiment config ----------
DESIGN_JSON = "designs/6502_mapped.json"   # pick a design for experiments
FABRIC_ARGS = ("fabric/fabric_cells.yaml", "fabric/pins.yaml", "fabric/fabric.yaml")
BUILD_DIR = "build/experiments"
os.makedirs(BUILD_DIR, exist_ok=True)

# Parameter grid (example)
cooling_rates = [0.85, 0.92, 0.97]
moves_per_temps = [100, 200, 400]
initial_temps = [50.0]        # keep fixed for initial sweep
prob_refines = [0.7]          # keep fixed for initial sweep
seeds_per_config = 3

# CSV output
csv_path = os.path.join(BUILD_DIR, "sa_experiment_results.csv")
fieldnames = [
    "config_id", "seed", "cooling_rate", "moves_per_temp", "initial_temp", "prob_refine",
    "run_time_s", "final_hpwl", "initial_hpwl", "iterations", "accepted_moves", "rejected_moves"
]

# ---------- Build fabric and parse design once ----------
print("Loading fabric_db and logical_db (one-time)...")
fabric_db = build_fabric_db(*FABRIC_ARGS)
logical_db, netlist_graph = parse_design_json(DESIGN_JSON)

print("Computing greedy placement once...")
initial_greedy = initial_placement(fabric_db, logical_db, netlist_graph)
initial_hpwl = calculate_hpwl(netlist_graph, initial_greedy, logical_db)
print(f"Greedy HPWL = {initial_hpwl:.2f}")

# ---------- Run experiments ----------
config_id = 0
with open(csv_path, "w", newline="") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    for (alpha, N, T0, pref_refine) in product(cooling_rates, moves_per_temps, initial_temps, prob_refines):
        config_id += 1
        for seed in range(seeds_per_config):
            random_seed = random.randint(0, 2**32 - 1)
            random.seed(random_seed)

            # prepare SA config
            cfg = SAConfig()
            cfg.cooling_rate = alpha
            cfg.moves_per_temp = N
            cfg.initial_temp = T0
            cfg.prob_refine = pref_refine
            cfg.prob_explore = 1.0 - pref_refine
            cfg.max_iterations = 200000  # safety

            # copy greedy placement as initial state (so same starting point each config if you want)
            initial_placement_dict = dict(initial_greedy)  # shallow copy of mapping

            # run SA and time it
            print(f"[RUN] cfg={config_id} alpha={alpha} N={N} T0={T0} pref_refine={pref_refine} seed={seed}")
            t0 = time.time()
            best_place, stats = simulated_annealing(
                fabric_db, logical_db, netlist_graph, initial_placement_dict, cfg
            )
            t1 = time.time()
            run_time = t1 - t0

            final_hpwl = stats.get("best_cost", None)
            iterations = stats.get("iterations", None)

            # Save placement map for best of each config-seed
            run_name = f"cfg{config_id}_seed{seed}_alpha{alpha}_N{N}_T0{int(T0)}"
            map_filename = os.path.join(BUILD_DIR, run_name + ".map")
            write_map_file(best_place, filename=map_filename)

            # Save stats JSON
            json_path = os.path.join(BUILD_DIR, run_name + "_stats.json")
            with open(json_path, "w") as jf:
                json.dump({"stats": stats, "config": {"alpha": alpha, "N": N, "T0": T0, "pref_refine": pref_refine, "seed": random_seed}}, jf, indent=2)

            # Write CSV row
            writer.writerow({
                "config_id": config_id,
                "seed": seed,
                "cooling_rate": alpha,
                "moves_per_temp": N,
                "initial_temp": T0,
                "prob_refine": pref_refine,
                "run_time_s": round(run_time, 3),
                "final_hpwl": round(final_hpwl, 3) if final_hpwl is not None else None,
                "initial_hpwl": round(initial_hpwl, 3),
                "iterations": iterations,
                "accepted_moves": stats.get("accepted_moves"),
                "rejected_moves": stats.get("rejected_moves"),
            })

print("All experiments complete. CSV saved to:", csv_path)

#!/usr/bin/env python3
import csv
import matplotlib.pyplot as plt
import numpy as np

CSV_PATH = "build/window_cooling_experiments/sa_window_cooling_results.csv"
OUT_PNG = "build/window_cooling_experiments/sa_window_cooling_pareto.png"

def compute_pareto_front(points):
    """
    points = [(runtime, hpwl, idx)]
    Returns indices on the Pareto frontier (min HPWL for increasing runtime)
    """
    points = sorted(points, key=lambda x: x[0])  # sort by runtime ascending
    pareto = []
    best_hpwl = float('inf')
    for rt, hpwl, idx in points:
        if hpwl < best_hpwl:
            best_hpwl = hpwl
            pareto.append(idx)
    return pareto

# -------------------------------------------------------
# Load CSV
# -------------------------------------------------------
rows = []
with open(CSV_PATH, newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        if not row.get("final_hpwl") or not row.get("runtime_s"):
            continue
        rows.append(row)

# Convert to numeric lists
runtimes   = np.array([float(r["runtime_s"])   for r in rows])
hpwls      = np.array([float(r["final_hpwl"]) for r in rows])
labels     = np.array([
    f"β={r['cooling_rate']}, W={r['w_initial']}"
    for r in rows
])

# -------------------------------------------------------
# Remove runtime outliers (>2× median)
# -------------------------------------------------------
med = np.median(runtimes)
keep = runtimes < (2 * med)

runtimes = runtimes[keep]
hpwls = hpwls[keep]
labels = labels[keep]

# Rebuild list
points = [(runtimes[i], hpwls[i], i) for i in range(len(runtimes))]

# -------------------------------------------------------
# Compute Pareto Frontier
# -------------------------------------------------------
pareto_idx = compute_pareto_front(points)
pareto_pts = sorted(
    [(runtimes[i], hpwls[i], i) for i in pareto_idx],
    key=lambda x: x[0]
)

# -------------------------------------------------------
# Plot
# -------------------------------------------------------
plt.figure(figsize=(10, 7))

# All points
plt.scatter(runtimes, hpwls, s=40, color="lightgray", label="All runs (filtered)")

# Pareto frontier points
px = [p[0] for p in pareto_pts]
py = [p[1] for p in pareto_pts]
pidx = [p[2] for p in pareto_pts]

plt.scatter(px, py, s=120, color="red", label="Pareto Frontier")
plt.plot(px, py, color="red")

# Annotate frontier
for idx in pidx:
    plt.annotate(
        labels[idx],
        (runtimes[idx], hpwls[idx]),
        xytext=(6, 6),
        textcoords="offset points",
        fontsize=8
    )

plt.xlabel("Runtime (s)")
plt.ylabel("Final HPWL (µm)")
plt.title(
    "SA Window + Cooling Sweep on 6502\n"
    "Runtime vs Final HPWL with Pareto Frontier"
)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=250)
print("Saved:", OUT_PNG)

plt.show()

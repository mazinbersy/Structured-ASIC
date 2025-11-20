# #!/usr/bin/env python3
# import csv
# import matplotlib.pyplot as plt
# import numpy as np
# from math import isfinite

# CSV_PATH = "build/experiments/sa_experiment_results.csv"
# OUT_PNG = "build/experiments/sa_knob_analysis.png"

# def compute_pareto_front(points):
#     points = sorted(points, key=lambda x: x[0])  # by runtime ascend
#     pareto = []
#     best_hpwl = float('inf')
#     for rt, hpwl, idx in points:
#         if hpwl < best_hpwl:
#             pareto.append(idx)
#             best_hpwl = hpwl
#     return pareto

# # load rows
# rows = []
# with open(CSV_PATH, newline='') as f:
#     r = csv.DictReader(f)
#     for row in r:
#         # skip rows missing final hpwl
#         if row.get("final_hpwl") is None or row["final_hpwl"] == "":
#             continue
#         rows.append(row)

# runtimes = [float(r["run_time_s"]) for r in rows]
# hpwls = [float(r["final_hpwl"]) for r in rows]
# labels = [f"a={r['cooling_rate']},N={r['moves_per_temp']}" for r in rows]

# points = [(runtimes[i], hpwls[i], i) for i in range(len(rows))]
# pareto_idx = compute_pareto_front(points)
# pareto_pts = sorted([(runtimes[i], hpwls[i], i) for i in pareto_idx], key=lambda x: x[0])

# plt.figure(figsize=(9,6))
# plt.scatter(runtimes, hpwls, s=40, color="lightgray", label="All runs")

# runtimes = np.array(runtimes)
# hpwls = np.array(hpwls)

# med = np.median(runtimes)
# keep = runtimes < (2 * med)

# runtimes = runtimes[keep]
# hpwls = hpwls[keep]
# labels = [labels[i] for i in range(len(labels)) if keep[i]]

# # highlight Pareto points
# px = [p[0] for p in pareto_pts]
# py = [p[1] for p in pareto_pts]
# pidx = [p[2] for p in pareto_pts]
# plt.scatter(px, py, s=120, color="red", label="Pareto frontier")
# plt.plot(px, py, color="red", linewidth=2)

# # Annotate Pareto points with labels (alpha and N)
# for i in pidx:
#     plt.annotate(labels[i], (runtimes[i], hpwls[i]), textcoords="offset points", xytext=(5,5), fontsize=8)

# plt.xlabel("Run time (s)")
# plt.ylabel("Final HPWL (µm)")
# plt.title("SA knob sweep: Run Time vs Final HPWL (Pareto frontier)")
# plt.grid(True)
# plt.legend()
# plt.tight_layout()
# plt.savefig(OUT_PNG, dpi=250)
# print("Saved:", OUT_PNG)
# plt.show()

#!/usr/bin/env python3
import csv
import matplotlib.pyplot as plt
import numpy as np
from math import isfinite

CSV_PATH = "build/experiments/sa_experiment_results.csv"
OUT_PNG = "build/experiments/sa_knob_analysis.png"

def compute_pareto_front(points):
    """
    points = [(runtime, hpwl, idx), ...]
    Return list of indices that form the Pareto frontier
    (minimizing hpwl for increasing runtime)
    """
    points = sorted(points, key=lambda x: x[0])  # sort by runtime ascending
    pareto = []
    best_hpwl = float('inf')
    for rt, hpwl, idx in points:
        if hpwl < best_hpwl:
            pareto.append(idx)
            best_hpwl = hpwl
    return pareto


# -------------------------------
# Load CSV
# -------------------------------
rows = []
with open(CSV_PATH, newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        if not row.get("final_hpwl"):
            continue
        rows.append(row)

# Convert to numeric lists
runtimes = np.array([float(r["run_time_s"]) for r in rows])
hpwls = np.array([float(r["final_hpwl"]) for r in rows])
labels = np.array([f"a={r['cooling_rate']},N={r['moves_per_temp']}" for r in rows])


# -------------------------------
# Remove outliers (2× median cutoff)
# -------------------------------
med = np.median(runtimes)
keep = runtimes < (2 * med)

runtimes = runtimes[keep]
hpwls = hpwls[keep]
labels = labels[keep]

# Build point list with NEW sequential indices
points = [(runtimes[i], hpwls[i], i) for i in range(len(runtimes))]


# -------------------------------
# Compute Pareto frontier
# -------------------------------
pareto_idx = compute_pareto_front(points)
pareto_pts = [(runtimes[i], hpwls[i], i) for i in pareto_idx]
pareto_pts = sorted(pareto_pts, key=lambda x: x[0])


# -------------------------------
# Plot
# -------------------------------
plt.figure(figsize=(10, 7))

# Scatter all remaining runs
plt.scatter(runtimes, hpwls, s=40, color="lightgray", label="All runs (filtered)")


# Plot Pareto frontier points
px = [p[0] for p in pareto_pts]
py = [p[1] for p in pareto_pts]
pidx = [p[2] for p in pareto_pts]

plt.scatter(px, py, s=120, color="red", label="Pareto frontier")
plt.plot(px, py, color="red", linewidth=2)


# Annotate each Pareto point
for idx in pidx:
    plt.annotate(
        labels[idx],
        (runtimes[idx], hpwls[idx]),
        textcoords="offset points",
        xytext=(5, 5),
        fontsize=8
    )


plt.xlabel("Run time (s)")
plt.ylabel("Final HPWL (µm)")
plt.title(
    "SA knob sweep: Run Time vs Final HPWL (Pareto frontier)\n"
    "Constants: T0=50, P_refine=0.7, same greedy starting point"
)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=250)
plt.title("SA knob sweep (6502): Varying cooling rate α and moves-per-temperature N\nT0=50, P_refine=0.7, Same greedy seed")

print("Saved:", OUT_PNG)
plt.show()

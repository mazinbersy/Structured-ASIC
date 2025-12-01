"""
visualization/plot_utils.py

Common plotting helpers used by SA/HPWL analysis scripts.
Provides:
 - compute_pareto_front(points)
 - load_csv_xy_labels(csv_path, x_field, y_field, label_builder)
 - plot_pareto(runtimes, hpwls, labels, out_png, title)

This centralizes duplicated plotting logic from existing `plot_sa_results.py` and
`plot_sa_window_cooling_results.py`.
"""

from typing import List, Tuple, Callable
import csv
import numpy as np
import matplotlib.pyplot as plt


def compute_pareto_front(points: List[Tuple[float, float, int]]) -> List[int]:
    """Return indices of Pareto frontier (minimizing y for increasing x).

    points: list of (x_value, y_value, idx)
    """
    points = sorted(points, key=lambda x: x[0])
    pareto = []
    best_y = float('inf')
    for x, y, idx in points:
        if y < best_y:
            pareto.append(idx)
            best_y = y
    return pareto


def load_csv_xy_labels(csv_path: str, x_field: str, y_field: str, label_builder: Callable[[dict], str] = None):
    """Load CSV and return (x_array, y_array, labels_array, rows_list).

    - Skips rows where x_field or y_field is missing.
    - label_builder(row) -> string for annotation; if None, uses empty strings.
    """
    rows = []
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r.get(x_field) or not r.get(y_field):
                continue
            rows.append(r)

    x = np.array([float(r[x_field]) for r in rows])
    y = np.array([float(r[y_field]) for r in rows])

    if label_builder is None:
        labels = np.array(["" for _ in rows])
    else:
        labels = np.array([label_builder(r) for r in rows])

    return x, y, labels, rows


def plot_pareto(
    runtimes, hpwls, labels, out_png: str, title: str = None,
    x_label: str = "Runtime (s)", y_label: str = "Final HPWL (µm)",
    out_dpi: int = 250
):
    """Plot scatter + Pareto frontier and save to PNG.

    Accepts numpy arrays or python lists for runtimes, hpwls, labels.
    """
    import numpy as _np

    runtimes = _np.array(runtimes)
    hpwls = _np.array(hpwls)
    labels = list(labels)

    # Remove runtime outliers (> 2x median)
    med = _np.median(runtimes)
    keep_mask = runtimes < (2 * med)
    runtimes_f = runtimes[keep_mask]
    hpwls_f = hpwls[keep_mask]
    labels_f = [labels[i] for i, k in enumerate(keep_mask) if k]

    points = [(runtimes_f[i], hpwls_f[i], i) for i in range(len(runtimes_f))]
    pareto_idx = compute_pareto_front(points)
    pareto_pts = sorted([(runtimes_f[i], hpwls_f[i], i) for i in pareto_idx], key=lambda x: x[0])

    plt.figure(figsize=(10, 7))

    # All filtered points
    plt.scatter(runtimes_f, hpwls_f, s=40, color="lightgray", label="All runs (filtered)")

    # Pareto frontier
    if pareto_pts:
        px = [p[0] for p in pareto_pts]
        py = [p[1] for p in pareto_pts]
        pidx = [p[2] for p in pareto_pts]
        plt.scatter(px, py, s=120, color="red", label="Pareto frontier")
        plt.plot(px, py, color="red", linewidth=2)

        # Annotate
        for idx in pidx:
            if idx < len(labels_f):
                label = labels_f[idx]
            else:
                label = ""
            if label:
                plt.annotate(label, (runtimes_f[idx], hpwls_f[idx]), xytext=(5, 5), textcoords="offset points", fontsize=8)

    plt.xlabel(x_label)
    plt.ylabel(y_label)
    if title:
        plt.title(title)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=out_dpi)
    print("Saved:", out_png)
    plt.show()

#!/usr/bin/env python3
"""
visualize_density_and_netlength.py

Produces:
 - build/<design>/<design>_density.png
 - build/<design>/<design>_net_length.png

Usage:
  python3 visualize_density_and_netlength.py --design 6502
"""
import os
import argparse
import json
import math
import numpy as np
import matplotlib.pyplot as plt

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json


def read_map_file(path):
    """
    Reads the new map file format with lines:
    - "port_name x y" for I/O ports
    - "tile_slot cell_type x y -> instance_name" for placed cells

    Returns:
        dict: {instance_name: (x, y), ...}
    """
    placement = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()

            # Check if this is a cell placement line (contains "->")
            if "->" in line:
                # Format: tile_slot cell_type x y -> instance_name
                arrow_idx = parts.index("->")
                if arrow_idx >= 3:
                    x = float(parts[arrow_idx - 2])
                    y = float(parts[arrow_idx - 1])
                    instance_name = parts[arrow_idx + 1]
                    placement[instance_name] = (x, y)
            elif len(parts) == 3:
                # Format: port_name x y (for I/O ports)
                port_name, x, y = parts
                placement[port_name] = (float(x), float(y))

    return placement


def make_density_heatmap(placement_coords, fabric_extent, outpath, bins=(200, 200)):
    xs = [p[0] for p in placement_coords]
    ys = [p[1] for p in placement_coords]
    plt.figure(figsize=(6, 6))
    H, xedges, yedges = np.histogram2d(xs, ys, bins=bins, range=[[0, fabric_extent[0]], [0, fabric_extent[1]]])
    H = np.rot90(H)
    H = np.flipud(H)
    plt.imshow(H, extent=[0, fabric_extent[0], 0, fabric_extent[1]], aspect='auto')
    plt.colorbar(label='Placed cells (counts)')
    plt.title('Placement Density Heatmap')
    plt.xlabel('X')
    plt.ylabel('Y')
    plt.tight_layout()
    plt.savefig(outpath, dpi=200)
    plt.close()


def compute_net_hpwl(logical_db, placement_coords):
    net_hpwl = []
    for net_id, net in logical_db['nets'].items():
        xs = []
        ys = []
        for endpoint in net['connections']:
            node = endpoint[0]
            if node in placement_coords:
                x, y = placement_coords[node]
                xs.append(x);
                ys.append(y)
            else:
                # ports or unplaced nodes: skip or approximate
                continue
        if not xs:
            continue
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        net_hpwl.append(hpwl)
    return net_hpwl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True)
    parser.add_argument("--map", default=None, help="path to map file (overrides default build/<design>/<design>.map)")
    args = parser.parse_args()

    design = args.design
    build_dir = os.path.join("build", design)
    os.makedirs(build_dir, exist_ok=True)

    # Fabric DB to get die extents or slot to coords mapping
    fabric_db = build_fabric_db("fabric/fabric_cells.yaml", "fabric/pins.yaml", "fabric/fabric.yaml")
    # you must adapt the above call to your build_fabric_db signature if different

    # parse logical db
    logical_db, netlist_graph = parse_design_json(f"designs/{design}_mapped.json")

    # load placement map
    map_path = args.map or os.path.join(build_dir, f"{design}.map")
    placement_coords = read_map_file(map_path)

    if not placement_coords:
        print("No placement coords found. Check your map format.")
        return

    # Compute die extents (simple bounding box)
    xs = [c[0] for c in placement_coords.values()]
    ys = [c[1] for c in placement_coords.values()]
    extent = (max(xs), max(ys))

    # 1) Density heatmap
    density_path = os.path.join(build_dir, f"{design}_density.png")
    make_density_heatmap(list(placement_coords.values()), extent, density_path, bins=(200, 200))
    print("Saved density heatmap ->", density_path)

    # 2) Net length histogram
    net_hpwl = compute_net_hpwl(logical_db, placement_coords)
    hist_path = os.path.join(build_dir, f"{design}_net_length.png")
    plt.figure(figsize=(6, 4))
    plt.hist(net_hpwl, bins=100)
    plt.xlabel("Net HPWL (µm)")
    plt.ylabel("Count")
    plt.title(f"{design} Net Length Histogram")
    plt.tight_layout()
    plt.savefig(hist_path, dpi=200)
    plt.close()
    print("Saved net-length histogram ->", hist_path)


if __name__ == "__main__":
    main()
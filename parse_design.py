#!/usr/bin/env python3
"""
parse_design.py
---------------
Parses a Yosys-generated [design_name]_mapped.json file and constructs:

  • logical_db: Internal Python data structure representing the logical netlist
  • netlist_graph: NetworkX graph capturing instance-to-instance connectivity

This module is part of Phase 1 (Database, Validation & Visualization)
for the Structured ASIC project.

Usage (import example):
    from parse_design import parse_design_json
    logical_db, netlist_graph = parse_design_json("designs/6502_mapped.json")
"""

import json
import os
from collections import defaultdict
from typing import Dict, Any, Tuple
import networkx as nx


# ===============================================================
# 1. Utility Helpers
# ===============================================================

def _get_single_bit(bit_list):
    """Helper for Yosys-style 'bits' arrays. Returns (bit_id, multi_bit_flag)."""
    if not isinstance(bit_list, list) or len(bit_list) == 0:
        raise ValueError(f"Expected non-empty list of bits, got {bit_list}")
    if len(bit_list) > 1:
        return bit_list[0], True
    return bit_list[0], False


def _find_top_module(modules: Dict[str, Any]) -> Tuple[str, Dict]:
    """Return (name, module_data) for the top-level module."""
    for name, m in modules.items():
        if m.get("attributes", {}).get("top"):
            return name, m
    # fallback
    first_name = next(iter(modules))
    return first_name, modules[first_name]


# ===============================================================
# 2. Main Parser
# ===============================================================

def parse_design_json(json_path: str) -> Tuple[Dict[str, Any], nx.Graph]:
    """
    Parse a Yosys *_mapped.json file and construct logical_db + netlist_graph.

    Args:
        json_path (str): path to the JSON netlist
    Returns:
        logical_db (dict), netlist_graph (nx.Graph)
    """
    with open(json_path, "r") as f:
        data = json.load(f)

    modules = data.get("modules", {})
    if not modules:
        raise ValueError(f"No modules found in {json_path}")

    top_name, top_module = _find_top_module(modules)

    # Initialize core data containers
    instances = {}
    instances_by_type = defaultdict(list)
    nets = {}
    ports = {"inputs": {}, "outputs": {}}
    multi_bit_warnings = []

    # --------------------------
    # Parse Ports
    # --------------------------
    for port_name, port_info in top_module.get("ports", {}).items():
        direction = port_info.get("direction", "unknown")
        bits = port_info.get("bits", [])
        if not bits:
            continue

        net_id, multi = _get_single_bit(bits)
        if multi:
            multi_bit_warnings.append(f"Port '{port_name}' is multi-bit; using first bit only.")

        if direction == "input":
            ports["inputs"][port_name] = net_id
        elif direction == "output":
            ports["outputs"][port_name] = net_id
        else:
            ports.setdefault("inouts", {})[port_name] = net_id

        # ensure net exists
        if net_id not in nets:
            nets[net_id] = {"name": port_name, "connections": []}
        nets[net_id]["connections"].append((port_name, "PORT"))

    # --------------------------
    # Parse Instances (Cells)
    # --------------------------
    for inst_name, cell_info in top_module.get("cells", {}).items():
        cell_type = cell_info.get("type", "")
        if not cell_type:
            continue

        instances[inst_name] = {"type": cell_type, "pins": {}}
        instances_by_type[cell_type].append(inst_name)

        for pin_name, net_bits in cell_info.get("connections", {}).items():
            net_id, multi = _get_single_bit(net_bits)
            if multi:
                multi_bit_warnings.append(f"{inst_name}.{pin_name} is multi-bit; using bit {net_id} only.")

            instances[inst_name]["pins"][pin_name] = net_id

            # create net if needed
            if net_id not in nets:
                nets[net_id] = {"name": f"net_{net_id}", "connections": []}
            nets[net_id]["connections"].append((inst_name, pin_name))

    # ===============================================================
    # 3. Build logical_db (Internal Representation)
    # ===============================================================
    logical_db = {
        "cells": instances,
        "cells_by_type": dict(instances_by_type),
        "nets": nets,
        "ports": ports,
        "meta": {
            "top_module": top_name,
            "source_file": os.path.basename(json_path),
            "multi_bit_warnings": multi_bit_warnings,
        },
    }

    # ===============================================================
    # 4. Build Netlist Graph (for connectivity and placement)
    # ===============================================================
    netlist_graph = _build_netlist_graph(logical_db)

    return logical_db, netlist_graph


# ===============================================================
# 5. Netlist Graph Builder
# ===============================================================

def _build_netlist_graph(logical_db: Dict[str, Any]) -> nx.Graph:
    """
    Build an undirected graph of the netlist:
      • Each instance and port is a node
      • Each shared net creates edges between all connected nodes
    """
    G = nx.Graph()

    # Add instance nodes
    for inst_name, inst_info in logical_db["cells"].items():
        G.add_node(inst_name, type=inst_info["type"], node_type="cell")

    # Add port nodes
    for pd in ("inputs", "outputs"):
        for pname in logical_db["ports"].get(pd, {}):
            G.add_node(pname, type="PORT", node_type="port", direction=pd)

    # Add edges
    for net_id, net_info in logical_db["nets"].items():
        endpoints = net_info.get("connections", [])
        node_list = [n for (n, _) in endpoints]
        if len(node_list) <= 1:
            continue
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                u, v = node_list[i], node_list[j]
                if not G.has_edge(u, v):
                    G.add_edge(u, v, nets=[net_info["name"]], net_id=net_id)
                else:
                    G[u][v]["nets"].append(net_info["name"])
    return G


# ===============================================================
# 6. (Optional) Minimal Test Mode
# ===============================================================

if __name__ == "__main__":
    import sys
    import json
    from networkx.readwrite import json_graph

    if len(sys.argv) != 2:
        print("Usage: python parse_design.py path/to/[design]_mapped.json")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print("Error: file not found:", path)
        sys.exit(1)

    logical_db, netlist_graph = parse_design_json(path)
    print(f"[OK] Parsed {logical_db['meta']['source_file']}")
    print(f"Nodes: {len(netlist_graph.nodes())}, Edges: {len(netlist_graph.edges())}")

    # -------------------------
    # Write logical_db to JSON
    # -------------------------
    logical_json_file = "logical_db.json"
    with open(logical_json_file, "w") as f:
        json.dump(logical_db, f, indent=2)
    print(f"logical_db written to {logical_json_file}")

    # -------------------------
    # Write netlist_graph to JSON
    # -------------------------
    graph_data = json_graph.node_link_data(netlist_graph)
    netlist_json_file = "netlist_graph.json"
    with open(netlist_json_file, "w") as f:
        json.dump(graph_data, f, indent=2)
    print(f"netlist_graph written to {netlist_json_file}")

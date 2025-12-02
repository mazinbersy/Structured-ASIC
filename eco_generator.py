#!/usr/bin/env python3
"""
eco_generator.py

End-to-end ECO flow combining CTS and Power-Down optimization.
Generates final Verilog netlist with both clock tree and power-down modifications.

Usage:
    python eco_generator.py --design 6502 [--placement placement_sa_optimized.map]

Outputs:
    - build/[design]/[design]_final.v     (Final Verilog netlist)
    - build/[design]/[design]_cts_tree.png (CTS visualization)
    - build/[design]/[design]_eco_report.txt (ECO summary)
"""

import argparse
import os
import sys
import json
import copy
from typing import Dict, Any, Tuple

# Import local modules
from cts_htree import HTreeCTS
from power_down import run_power_down_eco_from_sources
from visualization.cts_overlay import plot_cts_tree_overlay
from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
import subprocess


def generate_verilog_from_logical_db(logical_db: Dict[str, Any], design_name: str) -> str:
    """
    Generate a minimal Verilog netlist from logical_db.

    This is NOT a full synthesis output, but a netlist representation.
    For production, you'd integrate with commercial tools or more complete generators.

    Args:
        logical_db: Updated logical database with CTS/ECO modifications
        design_name: Design name for module/prefix

    Returns:
        String containing Verilog netlist
    """
    lines = []

    # Module header
    ports = logical_db.get("ports", {})
    input_ports = list(ports.get("inputs", {}).keys())
    output_ports = list(ports.get("outputs", {}).keys())

    all_ports = input_ports + output_ports
    port_str = ", ".join(all_ports)
    lines.append(f"module {design_name} ({port_str});")
    lines.append("")

    # Port declarations
    if input_ports:
        lines.append(f"  input {', '.join(input_ports)};")
    if output_ports:
        lines.append(f"  output {', '.join(output_ports)};")
    lines.append("")

    # Internal wire declarations (from nets)
    nets = logical_db.get("nets", {})
    internal_nets = []
    for net_id, net_info in nets.items():
        net_name = net_info.get("name", f"net_{net_id}")
        if net_name not in all_ports:
            internal_nets.append(net_name)

    if internal_nets:
        lines.append(f"  wire {', '.join(internal_nets)};")
        lines.append("")

    # Cell instantiations
    cells = logical_db.get("cells", {})
    for cell_name, cell_info in sorted(cells.items()):
        cell_type = cell_info.get("type", "")
        pins = cell_info.get("pins", {})

        # Build port connections
        connections = []
        for pin_name, net_id in pins.items():
            net_info = nets.get(net_id, {})
            net_name = net_info.get("name", f"net_{net_id}")
            connections.append(f".{pin_name}({net_name})")

        if connections:
            conn_str = ", ".join(connections)
            # Use fabric name if available in placement_map, otherwise use original name
            lines.append(f"  {cell_type} {cell_name} ({conn_str});")
            lines.append(f"  {cell_type} {cell_name} ({conn_str});")

    lines.append("")
    lines.append("endmodule")
    lines.append("")

    return "\n".join(lines)


def run_eco_generator(design_name: str, placement_file: str = None, output_dir: str = None, verbose: bool = True):
    """
    Run full ECO flow: CTS + Power-Down ECO + Verilog generation.

    Args:
        design_name: Design name (e.g., '6502')
        placement_file: Path to placement.map (default: build/[design]/[design]_sa_optimized.map)
        output_dir: Output directory (default: build/[design]/)
        verbose: Print progress messages

    Returns:
        Tuple of (final_verilog, eco_report)
    """
    if verbose:
        print("=" * 70)
        print(f"ECO GENERATOR: {design_name}")
        print("=" * 70)
        print()

    # Setup paths
    if output_dir is None:
        output_dir = f"build/{design_name}"
    os.makedirs(output_dir, exist_ok=True)

    if placement_file is None:
        # Try common placement file names
        for candidate in [
            f"{output_dir}/{design_name}_sa_optimized.map",
            f"{output_dir}/{design_name}.map",
            "placement_sa_optimized.map",
            "placement.map"
        ]:
            if os.path.exists(candidate):
                placement_file = candidate
                break
        if not placement_file:
            print(f"Error: No placement file found. Please specify --placement")
            return None, None

    design_json = f"designs/{design_name}_mapped.json"
    if not os.path.exists(design_json):
        print(f"Error: Design JSON not found: {design_json}")
        return None, None

    if verbose:
        print(f"Design: {design_name}")
        print(f"Placement: {placement_file}")
        print(f"Output directory: {output_dir}")
        print()

    # ========================================
    # Step 1: Run CTS
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 1: Clock Tree Synthesis (CTS)")
        print("=" * 70)

    try:
        cts = HTreeCTS(placement_file, design_json)
        cts.find_clock_net()
        cts.find_sinks()
        cts.find_resources()
        cts.build_clock_tree()
        cts.update_logical_db_and_graph()

        cts_placement_file = os.path.join(output_dir, f"{design_name}_cts.map")
        clock_tree_file = os.path.join(output_dir, f"{design_name}_clock_tree.json")

        cts.write_placement(cts_placement_file)
        cts.write_clock_tree(clock_tree_file)

        if verbose:
            cts.print_summary()

        # Keep the updated logical_db and netlist_graph from CTS
        logical_db_cts = cts.logical_db
        netlist_graph_cts = cts.netlist_graph

        if verbose:
            print()

    except Exception as e:
        print(f"Error during CTS: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return None, None

    # ========================================
    # Step 2: Run Power-Down ECO
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 2: Power-Down ECO")
        print("=" * 70)

    try:
        updated_logical_db, eco_report = run_power_down_eco_from_sources(
            design_json=design_json,
            fabric_cells_yaml="fabric/fabric_cells.yaml",
            pins_yaml="fabric/pins.yaml",
            fabric_def_yaml="fabric/fabric.yaml",
            placement_map_file=cts_placement_file,
            output_dir=output_dir,
            verbose=verbose
        )

        if verbose:
            print()

    except Exception as e:
        print(f"Error during ECO: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return None, None

    # ========================================
    # Step 3: Merge CTS and ECO logical_db
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 3: Merging CTS and ECO modifications")
        print("=" * 70)

    # Start from ECO-modified logical_db (which includes CTS buffers already added)
    merged_logical_db = copy.deepcopy(updated_logical_db)

    if verbose:
        print(f"  Merged logical_db has {len(merged_logical_db['cells'])} cells")
        print(f"  Merged logical_db has {len(merged_logical_db['nets'])} nets")
        print()

    # ========================================
    # Step 4: Generate final Verilog
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 4: Generating final Verilog netlist")
        print("=" * 70)

    try:
        final_verilog = generate_verilog_from_logical_db(merged_logical_db, design_name)
        
        verilog_file = os.path.join(output_dir, f"{design_name}_final.v")
        with open(verilog_file, 'w') as f:
            f.write(final_verilog)

        if verbose:
            print(f"  Written: {verilog_file}")
            print(f"  Lines: {len(final_verilog.split(chr(10)))}")
            print()

        # Run renamer to ensure final netlist uses fabric placement names
        try:
            if verbose:
                print("  Running renamer to apply fabric names to final netlist...")
            cmd = [
                sys.executable, "tools/rename_verilog_cells.py",
                "--verilog", verilog_file,
                "--placement", cts_placement_file,
                "--output", verilog_file
            ]
            # Run the script; allow it to overwrite the file
            proc = subprocess.run(cmd, capture_output=not verbose, text=True)
            if proc.returncode != 0:
                print("Warning: renamer script exited with non-zero status")
                if not verbose:
                    print(proc.stdout)
                    print(proc.stderr)
            else:
                if verbose:
                    print("  Renamer completed; final netlist updated with fabric names.")

            # Read back renamed netlist into memory
            with open(verilog_file, 'r') as f:
                final_verilog = f.read()

        except Exception as e:
            print(f"Warning: failed to run renamer: {e}")

    except Exception as e:
        print(f"Error generating Verilog: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return None, None

    # ========================================
    # Step 5: Generate CTS visualization
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 5: Generating CTS visualization")
        print("=" * 70)

    try:
        fabric_db = build_fabric_db(
            "fabric/fabric_cells.yaml",
            "fabric/pins.yaml",
            "fabric/fabric.yaml"
        )

        cts_png = os.path.join(output_dir, f"{design_name}_cts_tree.png")
        plot_cts_tree_overlay(
            merged_logical_db,
            cts_placement_file,
            clock_tree_file,
            fabric_db,
            out_png=cts_png
        )

        if verbose:
            print()

    except Exception as e:
        print(f"Error generating CTS visualization: {e}")
        if verbose:
            import traceback
            traceback.print_exc()

    # ========================================
    # Step 6: Write summary
    # ========================================
    if verbose:
        print("=" * 70)
        print("ECO GENERATION COMPLETE")
        print("=" * 70)
        print()
        print("Outputs:")
        print(f"  - {verilog_file}")
        print(f"  - {cts_png}")
        print(f"  - {os.path.join(output_dir, 'eco_report.txt')}")
        print()

    # Write ECO report to file
    eco_report_file = os.path.join(output_dir, "eco_report.txt")
    with open(eco_report_file, 'w') as f:
        f.write(eco_report)

    return final_verilog, eco_report


def main():
    parser = argparse.ArgumentParser(
        description="ECO Generator: CTS + Power-Down ECO + Final Verilog generation"
    )
    parser.add_argument("--design", required=True, help="Design name (e.g., 6502)")
    parser.add_argument("--placement", default=None, help="Path to placement.map file")
    parser.add_argument("--output", default=None, help="Output directory (default: build/[design]/)")
    args = parser.parse_args()

    final_verilog, eco_report = run_eco_generator(
        design_name=args.design,
        placement_file=args.placement,
        output_dir=args.output,
        verbose=True
    )

    if final_verilog is None:
        sys.exit(1)


if __name__ == "__main__":
    main()

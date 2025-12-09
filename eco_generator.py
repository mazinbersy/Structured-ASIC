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
import re
from typing import Dict, Any, Tuple, Set

# Import local modules
from cts_htree import HTreeCTS, parse_placement_map
from power_down import run_power_down_eco, load_placement_mapping
from parse_lib import parse_liberty_leakage
from visualization.cts_overlay import plot_cts_tree_overlay
from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
import subprocess


def parse_lef_for_pins(lef_file: str) -> Dict[str, Set[str]]:
    """
    Parse LEF file to extract pin names for each cell type.
    
    Args:
        lef_file: Path to LEF file
        
    Returns:
        Dict mapping cell type names to sets of pin names
        Example: {'sky130_fd_sc_hd__clkbuf_4': {'A', 'X', 'VDD', 'VSS'}}
    """
    cell_pins = {}
    
    if not os.path.exists(lef_file):
        print(f"[WARN] LEF file not found: {lef_file}")
        return cell_pins
    
    try:
        with open(lef_file, 'r') as f:
            content = f.read()
    except IOError as e:
        print(f"[WARN] Cannot read LEF file: {e}")
        return cell_pins
    
    # Extract MACRO definitions and their PINs
    macro_pattern = r'MACRO\s+(\S+)\s*\n(.*?)END\s+\1'
    
    for macro_match in re.finditer(macro_pattern, content, re.DOTALL | re.IGNORECASE):
        macro_name = macro_match.group(1)
        macro_body = macro_match.group(2)
        
        # Find all PIN statements within this MACRO
        pin_pattern = r'PIN\s+(\S+)\s*\n(.*?)END\s+\1'
        pins = set()
        
        for pin_match in re.finditer(pin_pattern, macro_body, re.DOTALL | re.IGNORECASE):
            pin_name = pin_match.group(1)
            pins.add(pin_name)
        
        if pins:
            cell_pins[macro_name] = pins
    
    if cell_pins:
        print(f"[INFO] Extracted pin definitions for {len(cell_pins)} cell types from LEF")
    
    return cell_pins


def generate_verilog_from_logical_db(logical_db: Dict[str, Any], design_name: str, cell_pins: Dict[str, Set[str]] = None) -> str:
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
    if cell_pins is None:
        cell_pins = {}
    
    cells = logical_db.get("cells", {})
    for cell_name, cell_info in sorted(cells.items()):
        cell_type = cell_info.get("type", "")
        pins = cell_info.get("pins", {})

        # Get valid pins for this cell type from LEF
        valid_pins = cell_pins.get(cell_type, set())

        # Build port connections - only include pins that exist in the cell definition
        connections = []
        for pin_name, net_id in pins.items():
            # Skip pins not in LEF definition (use case-insensitive matching)
            valid_pin_name = None
            for vpin in valid_pins:
                if pin_name.upper() == vpin.upper():
                    valid_pin_name = vpin
                    break
            
            if valid_pin_name is None:
                # Pin not found in LEF - try common mappings
                pin_mapping = {
                    'Y': ['X', 'Y', 'Q'],  # Try X for output pins
                    'A': ['A', 'I', 'IN'],
                    'B': ['B', 'IN2'],
                }
                for mapped_pin in pin_mapping.get(pin_name, []):
                    if mapped_pin.upper() in {p.upper() for p in valid_pins}:
                        valid_pin_name = mapped_pin
                        break
                
                if valid_pin_name is None:
                    # Still not found - warn and skip
                    if valid_pins:
                        print(f"[WARN] Pin '{pin_name}' not found in LEF for cell type '{cell_type}' (available: {', '.join(sorted(valid_pins))})")
                    continue
            
            net_info = nets.get(net_id, {})
            net_name = net_info.get("name", f"net_{net_id}")
            connections.append(f".{valid_pin_name}({net_name})")

        if connections:
            conn_str = ", ".join(connections)
            lines.append(f"  {cell_type} {cell_name} ({conn_str});")

    lines.append("")
    lines.append("endmodule")
    lines.append("")

    return "\n".join(lines)


def run_eco_generator(
        io_ports: Dict,
        fabric_cells: Dict,
        fabric_db: Dict,
        logical_db: Dict,
        netlist_graph: Any,
        placement_file: str,
        output_dir: str,
        design_name: str,
        verbose: bool = True
) -> Tuple[str, str]:
    """
    Run full ECO flow: CTS + Power-Down ECO + Verilog generation.

    Args:
        io_ports: I/O port positions from placement
        fabric_cells: Fabric cell info from placement
        fabric_db: Pre-built fabric database
        logical_db: Pre-built logical database
        netlist_graph: Pre-built netlist graph
        placement_file: Path to original placement.map file
        output_dir: Output directory
        design_name: Design name (e.g., '6502')
        verbose: Print progress messages

    Returns:
        Tuple of (final_verilog, eco_report)
    """
    if verbose:
        print("=" * 70)
        print(f"ECO GENERATOR: {design_name}")
        print("=" * 70)
        print()
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
        # Initialize CTS with pre-built databases and placement data
        cts = HTreeCTS(io_ports, fabric_cells, fabric_db, logical_db, netlist_graph)

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
        # Parse Liberty file for leakage data
        liberty_file = "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
        if verbose:
            print(f"Loading leakage data from: {liberty_file}")
        leakage_db = parse_liberty_leakage(liberty_file, verbose=False)
        
        # Load placement mapping for ECO
        placement_map = load_placement_mapping(cts_placement_file)

        # Run ECO with per-input tie selection
        updated_logical_db, eco_report = run_power_down_eco(
            logical_db=logical_db_cts,
            fabric_db=fabric_db,
            leakage_db=leakage_db,
            placement_map=placement_map,
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

    # The updated_logical_db from ECO already includes CTS buffers
    merged_logical_db = copy.deepcopy(updated_logical_db)

    if verbose:
        print(f"  Merged logical_db has {len(merged_logical_db['cells'])} cells")
        print(f"  Merged logical_db has {len(merged_logical_db['nets'])} nets")
        print()

    # ========================================
    # Step 3.5: Parse LEF for cell pin definitions
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 3.5: Parsing LEF file for pin definitions")
        print("=" * 70)

    cell_pins = {}
    for lef_file in ['tech/sky130_fd_sc_hd.lef', 'tech/fabric_cells.lef']:
        if os.path.exists(lef_file):
            if verbose:
                print(f"  Reading: {lef_file}")
            lef_pins = parse_lef_for_pins(lef_file)
            cell_pins.update(lef_pins)
            if verbose:
                print(f"    Found pins for {len(lef_pins)} cell types")

    if verbose:
        print(f"  Total: {len(cell_pins)} cell types with pin definitions")
        print()

    # ========================================
    # Step 4: Generate final Verilog
    # ========================================
    if verbose:
        print("=" * 70)
        print("STEP 4: Generating final Verilog netlist")
        print("=" * 70)

    try:
        final_verilog = generate_verilog_from_logical_db(merged_logical_db, design_name, cell_pins)

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

    design_name = args.design

    # Setup paths
    output_dir = args.output
    if output_dir is None:
        output_dir = f"build/{design_name}"
    os.makedirs(output_dir, exist_ok=True)

    placement_file = args.placement
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
            sys.exit(1)

    design_json = f"designs/{design_name}_mapped.json"
    if not os.path.exists(design_json):
        print(f"Error: Design JSON not found: {design_json}")
        sys.exit(1)

    print(f"Design: {design_name}")
    print(f"Design JSON: {design_json}")
    print(f"Placement: {placement_file}")
    print(f"Output directory: {output_dir}")
    print()

    # ========================================
    # Build databases (once, upfront)
    # ========================================
    print("=" * 70)
    print("Building databases...")
    print("=" * 70)

    try:
        # Build fabric database
        print("Building fabric database from YAML files...")
        fabric_db = build_fabric_db(
            'fabric/fabric_cells.yaml',
            'fabric/pins.yaml',
            'fabric/fabric.yaml'
        )

        # Parse design netlist
        print(f"Loading design netlist: {design_json}")
        logical_db, netlist_graph = parse_design_json(design_json)
        print(f"Loaded logical_db with {len(logical_db['cells'])} cells")
        print(f"Loaded netlist_graph with {len(netlist_graph.nodes())} nodes")

        # Parse placement map
        io_ports, fabric_cells = parse_placement_map(placement_file)
        print(f"Loaded {len(io_ports)} I/O ports and {len(fabric_cells)} fabric cells")
        print()

    except Exception as e:
        print(f"Error building databases: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Run ECO generator with pre-built databases
    final_verilog, eco_report = run_eco_generator(
        io_ports=io_ports,
        fabric_cells=fabric_cells,
        fabric_db=fabric_db,
        logical_db=logical_db,
        netlist_graph=netlist_graph,
        placement_file=placement_file,
        output_dir=output_dir,
        design_name=design_name,
        verbose=True
    )

    if final_verilog is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
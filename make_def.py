#!/usr/bin/env python3
"""
make_def.py
-----------
Generates a DEF (Design Exchange Format) file for the design after CTS and Power-Down ECO.

The DEF file contains:
- DIEAREA (from fabric dimensions)
- All PINS (I/O ports) marked as + FIXED
- All COMPONENTS from fabric_cells.yaml (both used and unused) marked as + FIXED

This integrates:
1. Clock Tree Synthesis (cts_htree.py)
2. Power-Down ECO (power_down_eco.py)
3. Final DEF generation with complete placement

Usage:
    python make_def.py <design_name> <design_json> <fabric_cells.yaml> <pins.yaml> <fabric.yaml> <placement.map> [options]

Example:
    python make_def.py 6502 designs/6502_mapped.json fabric/fabric_cells.yaml fabric/pins.yaml fabric/fabric.yaml placement.map
"""

import sys
import os
import yaml
import json
from typing import Dict, List, Tuple, Any
from collections import defaultdict
import string

# Import required modules
from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
from cts_htree import HTreeCTS, parse_placement_map
from power_down import run_power_down_eco, load_placement_mapping


def load_placement_map(placement_file: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load placement mapping in both directions.

    Parses .map format:
    fabric_slot  cell_type  x  y  ->  logical_instance

    Args:
        placement_file: Path to .map file

    Returns:
        Tuple of (logical_to_fabric, fabric_to_logical) dictionaries
        - logical_to_fabric: Dict[logical_instance, fabric_slot]
        - fabric_to_logical: Dict[fabric_slot, logical_instance]
    """
    logical_to_fabric = {}
    fabric_to_logical = {}

    if not os.path.exists(placement_file):
        print(f"Error: Placement file not found: {placement_file}")
        return logical_to_fabric, fabric_to_logical

    with open(placement_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or '->' not in line:
                continue

            # Split on '->'
            parts = line.split('->')
            if len(parts) != 2:
                continue

            left_part = parts[0].strip().split()
            logical_inst = parts[1].strip()

            if len(left_part) >= 1:
                fabric_cell = left_part[0]

                # Always map fabric to logical (even if UNUSED)
                fabric_to_logical[fabric_cell] = logical_inst

                # Only map logical to fabric if not UNUSED
                if logical_inst != "UNUSED":
                    logical_to_fabric[logical_inst] = fabric_cell

    return logical_to_fabric, fabric_to_logical


def get_die_area(fabric_db: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """
    Extract DIEAREA from fabric database.

    Returns:
        Tuple of (llx, lly, urx, ury) in database units (typically nanometers)
    """
    fabric_info = fabric_db.get('fabric', {})
    pin_placement_info = fabric_info.get('pin_placement', {})
    die_info = pin_placement_info.get('die',{})
    # Get die dimensions in microns
    width_um = die_info.get('width_um', 0)
    height_um = die_info.get('height_um', 0)

    # Convert to database units (assuming microns, convert to nanometers)
    # DEF typically uses database units where 1 micron = 1000 units
    llx = 0
    lly = 0
    urx = int(width_um * 1000)  # Convert microns to nanometers
    ury = int(height_um * 1000)

    return (llx, lly, urx, ury)


def extract_io_pins(logical_db: Dict[str, Any],
                    fabric_db: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract I/O pin information from logical database and fabric.

    Returns:
        List of pin dictionaries with name, direction, position, and layer
    """
    pins = []

    # Get I/O ports from logical_db
    input_ports = logical_db.get('ports', {}).get('inputs', {})
    output_ports = logical_db.get('ports', {}).get('outputs', {})

    # Get pin list from fabric_db
    fabric_info = fabric_db.get('fabric', {})
    pin_placement_info = fabric_info.get('pin_placement', {})
    pin_list = pin_placement_info.get('pins', [])

    # Create a mapping from pin name to pin info for quick lookup
    pin_info_map = {pin['name']: pin for pin in pin_list}

    # Process input ports
    for port_name, net_id in input_ports.items():
        pin_info = pin_info_map.get(port_name, {})

        pins.append({
            'name': port_name,
            'direction': 'INPUT',
            'net': net_id,
            'x': int(pin_info.get('x_um', 0) * 1000),  # Convert to nanometers
            'y': int(pin_info.get('y_um', 0) * 1000),
            'layer': pin_info.get('layer', 'met3')
        })

    # Process output ports
    for port_name, net_id in output_ports.items():
        pin_info = pin_info_map.get(port_name, {})

        pins.append({
            'name': port_name,
            'direction': 'OUTPUT',
            'net': net_id,
            'x': int(pin_info.get('x_um', 0) * 1000),  # Convert to nanometers
            'y': int(pin_info.get('y_um', 0) * 1000),
            'layer': pin_info.get('layer', 'met3')
        })

    return pins


def extract_components(fabric_db: Dict[str, Any],
                       logical_db: Dict[str, Any],
                       fabric_to_logical: Dict[str, str]) -> [List[Dict[str, Any]], int, string]:
    """
    Extract all component placements from fabric database.

    Includes:
    - All cells from fabric_cells.yaml (both used and unused)
    - Maps fabric positions to logical instances
    - Marks all components as FIXED

    Args:
        fabric_db: Fabric database
        logical_db: Logical database (logical_db['cells'] contains instances)
        fabric_to_logical: Dict mapping fabric_cell -> logical_instance

    Returns:
        List of component dictionaries
    """
    components = []

    # Get all fabric cells from cells_by_tile
    cells_by_tile = fabric_db.get('fabric', {}).get('cells_by_tile', {})
    units = fabric_db.get('fabric', {}).get('pin_placement', {}).get('units', {}).get('dbu_per_micron', 1000)
    coords = fabric_db.get('fabric', {}).get('pin_placement', {}).get('units', {}).get('coords', 'micron').upper()


    # Track added cells to avoid duplicates
    added_cells = set()

    for tile_name, tile_data in cells_by_tile.items():
        for cell in tile_data.get('cells', []):
            fabric_cell_name = cell.get('name', '')
            cell_type = cell.get('cell_type', '')
            x = cell.get('x', 0)
            y = cell.get('y', 0)
            orientation = cell.get('orientation', 'N')

            if not fabric_cell_name or fabric_cell_name in added_cells:
                continue

            # Check if this fabric cell is used (mapped to logical instance)
            logical_name = fabric_to_logical.get(fabric_cell_name, fabric_cell_name)

            # If mapped to UNUSED, use fabric cell name
            if logical_name == "UNUSED":
                logical_name = fabric_cell_name

            # Verify the logical instance exists in logical_db (for mapped cells)
            # logical_db['cells'] contains the instances (not 'instances')
            elif logical_name not in logical_db.get('cells', {}):
                # Use fabric name if logical instance not found
                logical_name = fabric_cell_name

            components.append({
                'name': logical_name,
                'cell_type': cell_type,
                'x': int(x * units),  # Convert to nanometers
                'y': int(y * units),
                'orientation': orientation,
                'status': 'FIXED',
                'fabric_cell': fabric_cell_name
            })

            added_cells.add(fabric_cell_name)

    return components,units, coords


def write_def_file(design_name: str,
                   die_area: Tuple[int, int, int, int],
                   pins: List[Dict[str, Any]],
                   components: List[Dict[str, Any]],
                   output_file: str,
                   units: int = 1000,
                   coords = 'MICRONS'):
    """
    Write DEF file with all placement information.

    Args:
        design_name: Name of the design
        die_area: Tuple of (llx, lly, urx, ury)
        pins: List of I/O pins
        components: List of components
        output_file: Path to output DEF file
        units: Database units per micron (default 1000)
        coords: Coordinate system (default MICRONS)
    """
    with open(output_file, 'w') as f:
        # Header
        f.write(f"VERSION 5.8 ;\n")
        f.write(f"DIVIDERCHAR \"/\" ;\n")
        f.write(f"BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {design_name} ;\n")
        f.write(f"UNITS DISTANCE {coords} {units} ;\n")
        f.write("\n")

        # Die area
        llx, lly, urx, ury = die_area
        f.write(f"DIEAREA ( {llx} {lly} ) ( {urx} {ury} ) ;\n")
        f.write("\n")

        # Pins section
        f.write(f"PINS {len(pins)} ;\n")
        for pin in sorted(pins, key=lambda p: p['name']):
            f.write(f"  - {pin['name']} + NET {pin['name']}\n")
            f.write(f"    + DIRECTION {pin['direction']}\n")
            f.write(f"    + FIXED ( {pin['x']} {pin['y']} ) {pin['layer']}\n")
            f.write(f"    ;\n")
        f.write("END PINS\n")
        f.write("\n")

        # Components section
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in sorted(components, key=lambda c: c['name']):
            f.write(f"  - {comp['name']} {comp['cell_type']}\n")
            f.write(f"    + {comp['status']} ( {comp['x']} {comp['y']} ) {comp['orientation']}\n")
            f.write(f"    ;\n")
        f.write("END COMPONENTS\n")
        f.write("\n")

        # End design
        f.write(f"END DESIGN\n")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python make_def.py <design_name> [options]")
        sys.exit(1)

    # Required argument
    design_name = sys.argv[1]

    # Optional positional arguments (allow missing)
    design_json = sys.argv[2] if len(sys.argv) > 2 else None
    fabric_cells_yaml = sys.argv[3] if len(sys.argv) > 3 else None
    pins_yaml = sys.argv[4] if len(sys.argv) > 4 else None
    fabric_yaml = sys.argv[5] if len(sys.argv) > 5 else None
    placement_map_file = sys.argv[6] if len(sys.argv) > 6 else None

    # Flags
    run_cts = True
    run_eco = True
    clock_net = None
    output_dir = None

    i = 7
    while i < len(sys.argv):
        if sys.argv[i] == '--no-cts':
            run_cts = False
        elif sys.argv[i] == '--no-eco':
            run_eco = False
        elif sys.argv[i] == '--clock' and i + 1 < len(sys.argv):
            clock_net = sys.argv[i + 1]
            i += 1
        elif sys.argv[i] == '--output' and i + 1 < len(sys.argv):
            output_dir = sys.argv[i + 1]
            i += 1
        i += 1

    # -------------------------------
    # Default argument values
    # -------------------------------
    if design_json is None:
        design_json = f"designs/{design_name}_mapped.json"

    if fabric_cells_yaml is None:
        fabric_cells_yaml = "fabric/fabric_cells.yaml"

    if pins_yaml is None:
        pins_yaml = "fabric/pins.yaml"

    if fabric_yaml is None:
        fabric_yaml = "fabric/fabric.yaml"

    if placement_map_file is None:
        placement_map_file = f"placement_cts.map"

    if output_dir is None:
        output_dir = f"build/{design_name}"

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    print(f"{'=' * 70}")
    print(f"DEF Generation for {design_name}")
    print(f"{'=' * 70}")

    # ========================================
    # Build databases (once, upfront)
    # ========================================
    print("\n[1/5] Building databases...")

    # Load fabric database
    print("  Building fabric database from YAML files...")
    fabric_db = build_fabric_db(fabric_cells_yaml, pins_yaml, fabric_yaml)

    # Parse logical design
    print("  Loading logical design...")
    logical_db, netlist_graph = parse_design_json(design_json)
    print(f"  Loaded logical_db with {len(logical_db['cells'])} cells")
    print(f"  Loaded netlist_graph with {len(netlist_graph.nodes())} nodes")

    # Parse placement map
    print("  Loading placement map...")
    io_ports, fabric_cells = parse_placement_map(placement_map_file)
    print(f"  Loaded {len(io_ports)} I/O ports and {len(fabric_cells)} fabric cells")

    # ========================================
    # Run CTS
    # ========================================
    if run_cts:
        print("\n[2/5] Running CTS...")
        # Create CTS instance with pre-built databases
        cts = HTreeCTS(
            io_ports=io_ports,
            fabric_cells=fabric_cells,
            fabric_db=fabric_db,
            logical_db=logical_db,
            netlist_graph=netlist_graph
        )

        # Run CTS flow
        cts.find_clock_net(clock_net)
        cts.find_sinks()
        cts.find_resources()
        cts.build_clock_tree()

        # Update databases
        logical_db, netlist_graph = cts.update_logical_db_and_graph()

        # Write CTS outputs
        cts_placement_file = "placement_cts.map"
        cts.write_placement(cts_placement_file)
        cts.write_clock_tree("clock_tree.json")

        print(f"  CTS placement written to: {cts_placement_file}")

        # Reload placement map after CTS for both directions
        logical_to_fabric, fabric_to_logical = load_placement_map(cts_placement_file)

        # Use CTS placement for subsequent steps
        placement_map_file = cts_placement_file
    else:
        print("\n[2/5] Skipping CTS (--no-cts)")
        # Load original placement map
        logical_to_fabric, fabric_to_logical = load_placement_map(placement_map_file)

    # ========================================
    # Run ECO
    # ========================================
    if run_eco:
        print("\n[3/5] Running Power-Down ECO...")

        # Load placement mapping in the format expected by power_down_eco
        placement_map_dict = load_placement_mapping(placement_map_file)

        # Run ECO with pre-built databases
        logical_db, eco_report = run_power_down_eco(
            logical_db=logical_db,
            fabric_db=fabric_db,
            placement_map=placement_map_dict,
            output_dir=output_dir,
            verbose=True
        )
    else:
        print("\n[3/5] Skipping ECO (--no-eco)")

    # ========================================
    # Generate DEF
    # ========================================
    print("\n[4/5] Generating DEF...")
    die_area = get_die_area(fabric_db)
    pins = extract_io_pins(logical_db, fabric_db)
    components, units, coords = extract_components(fabric_db, logical_db, fabric_to_logical)

    print(f"  Die area: {die_area}")
    print(f"  Pins: {len(pins)}")
    print(f"  Components: {len(components)}")

    # Write DEF file
    def_path = os.path.join(output_dir, f"{design_name}_fixed.def")
    write_def_file(design_name, die_area, pins, components, def_path, units, coords)

    print("\n[5/5] Done!")
    print(f"DEF written to: {def_path}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
power_down_eco.py
-----------------
Implements Power-Down ECO by tying unused logic cell inputs to conb_1 cells.

Strategy:
  1. Parse logical netlist and fabric database using parse_design.py and build_fabric_db.py
  2. Identify unused cells in the fabric
  3. Claim one sky130_fd_sc_hd__conb_1 per tile (tie-low)
  4. Modify netlist to connect unused inputs to conb_1 output
  5. Generate updated netlist and placement

Usage:
    # Direct from source files (recommended):
    python power_down_eco.py design_mapped.json fabric_cells.yaml pins.yaml fabric.yaml placement.map
    
    # From Python (in-memory):
    from power_down_eco import run_power_down_eco_from_sources
    
    updated_db, report = run_power_down_eco_from_sources(
        design_json="design_mapped.json",
        fabric_cells_yaml="fabric_cells.yaml",
        pins_yaml="pins.yaml",
        fabric_def_yaml="fabric.yaml",
        placement_map_file="placement.map"
    )
"""

import json
import yaml
import sys
import os
from collections import defaultdict
from typing import Dict, Any, List, Set, Tuple, Optional

# Import the parsing modules
from parse_design import parse_design_json
from build_fabric_db import build_fabric_db


# ===============================================================
# Cell Type Classification
# ===============================================================

def is_macro(cell_type: str) -> bool:
    """
    Check if cell is a macro (not a standard cell).
    Macros should NOT be tied - they have complex internal structure.
    """
    macro_patterns = [
        "dfbbp",      # Flip-flop banks
        "sram",       # Memory
        "regfile",    # Register files
        "macro",      # Generic macro indicator
        "dffram",     # DFF RAM blocks
        "fifo",       # FIFO blocks
    ]
    cell_lower = cell_type.lower()
    return any(p in cell_lower for p in macro_patterns)


def is_infrastructure(cell_type: str) -> bool:
    """
    Check if cell is infrastructure (tap, decap, filler, etc.).
    These should be skipped - they're not logic.
    """
    infra_patterns = [
        "tap",        # Tap cells (power/ground)
        "decap",      # Decoupling capacitors
        "conb",       # Tie cells (already serving this purpose)
        "fill",       # Filler cells
        "diode",      # Antenna diodes
        "antenna",    # Antenna protection
        "endcap",     # End caps
        "welltap",    # Well taps
    ]
    cell_lower = cell_type.lower()
    return any(p in cell_lower for p in infra_patterns)


# ===============================================================
# Pin Enumeration from Cell Library
# ===============================================================

def get_cell_input_pins(cell_type: str, fabric_db: Dict[str, Any] = None) -> List[str]:
    """
    Return list of input pins for a cell type.
    
    Priority:
    1. Look up in fabric_db cell library (if available)
    2. Parse from cell type name
    3. Return empty list if unknown (safer than guessing)
    
    Args:
        cell_type: Cell type string (e.g., "sky130_fd_sc_hd__nand2_2")
        fabric_db: Fabric database with cell library definitions
    
    Returns:
        List of input pin names, or empty list if unknown
    """
    # Try to find cell definition in fabric_db
    if fabric_db:
        cell_defs = fabric_db.get("cell_library", {})
        
        if cell_type in cell_defs:
            pins = cell_defs[cell_type].get("pins", {})
            input_pins = [p for p, dir in pins.items() if dir in ["input", "INPUT", "in"]]
            if input_pins:
                return input_pins
    
    # Fallback: parse from cell name patterns
    cell_lower = cell_type.lower()
    
    # 2-input gates
    if any(gate in cell_lower for gate in ["nand2", "nor2", "and2", "or2", "xor2", "xnor2"]):
        return ["A", "B"]
    
    # 3-input gates
    if any(gate in cell_lower for gate in ["nand3", "nor3", "and3", "or3"]):
        return ["A", "B", "C"]
    
    # 4-input gates
    if any(gate in cell_lower for gate in ["nand4", "nor4", "and4", "or4"]):
        return ["A", "B", "C", "D"]
    
    # Single-input gates
    if any(gate in cell_lower for gate in ["inv", "buf", "clkbuf"]):
        return ["A"]
    
    # 2:1 Mux
    if "mux2" in cell_lower:
        return ["A0", "A1", "S"]
    
    # 4:1 Mux
    if "mux4" in cell_lower:
        return ["A0", "A1", "A2", "A3", "S0", "S1"]
    
    # Flip-flops - only tie data input, NOT clock
    if "dff" in cell_lower or "dlatch" in cell_lower:
        # NOTE: Only tie D (data), never CLK
        # In practice, DFFs should probably be skipped entirely
        return ["D"]
    
    # Unknown cell type - don't guess, return empty
    print(f"  Warning: Unknown cell type '{cell_type}', skipping pin enumeration")
    return []


# ===============================================================
# Placement Mapping
# ===============================================================

def load_placement_mapping(placement_file: str = None) -> Dict[str, str]:
    """
    Load the placement mapping: logical_instance -> fabric_cell.
    
    Supports multiple formats:
    - .map format: "fabric_cell  cell_type  x  y  ->  logical_instance"
    - .json format: {"logical_inst": "fabric_cell", ...}
    - .yaml format: same as json
    
    Returns:
        Dict[logical_instance, fabric_cell]
    """
    if not placement_file or not os.path.exists(placement_file):
        return {}
    
    placement_map = {}
    
    with open(placement_file, 'r') as f:
        if placement_file.endswith('.json'):
            placement_map = json.load(f)
        elif placement_file.endswith(('.yaml', '.yml')):
            placement_map = yaml.safe_load(f)
        elif placement_file.endswith('.map'):
            # Parse .map format:
            # fabric_cell  cell_type  x  y  ->  logical_instance
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
                    # Map: logical_instance -> fabric_cell
                    placement_map[logical_inst] = fabric_cell
        else:
            print(f"Warning: Unknown placement file format: {placement_file}")
    
    return placement_map


# ===============================================================
# Identify Unused Cells
# ===============================================================

def identify_unused_cells(logical_db: Dict[str, Any], 
                          fabric_db: Dict[str, Any],
                          placement_map: Dict[str, str] = None) -> Dict[str, List[str]]:
    """
    Find unused cells in fabric that are not used in logical netlist.
    
    Enhanced version with proper filtering:
    - Skip macros (DFBBP, SRAM, etc.)
    - Skip infrastructure (TAP, DECAP, etc.)
    - Verify cell is truly unused (not placed AND not in netlist)
    
    Args:
        logical_db: The logical netlist database
        fabric_db: The fabric database with all available cells
        placement_map: Dict mapping logical_instance -> fabric_cell_name
    
    Returns:
        Dict[tile_key, List[unused_cell_dicts]]
    """
    # Get set of fabric cells that are actually used (placed)
    if placement_map:
        used_fabric_cells = set(placement_map.values())
        print(f"  Using placement map: {len(used_fabric_cells)} cells placed")
    else:
        used_fabric_cells = set()
        print(f"  Warning: No placement map provided")
        print(f"  Will check logical netlist only")
    
    # Also get cells that appear in logical netlist
    logical_cells = set(logical_db.get("cells", {}).keys())
    print(f"  Logical netlist contains: {len(logical_cells)} cells")
    
    # Track unused cells per tile
    unused_by_tile = defaultdict(list)
    
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    
    total_fabric_cells = 0
    skipped_macro = 0
    skipped_infra = 0
    skipped_used = 0
    
    for tile_key, tile_data in cells_by_tile.items():
        for cell in tile_data.get("cells", []):
            total_fabric_cells += 1
            
            cell_name = cell.get("name", "")
            cell_type = cell.get("cell_type", "")
            
            # Filter 1: Skip macros (DFBBP, SRAM, etc.)
            if is_macro(cell_type):
                skipped_macro += 1
                continue
            
            # Filter 2: Skip infrastructure (TAP, DECAP, CONB, etc.)
            if is_infrastructure(cell_type):
                skipped_infra += 1
                continue
            
            # Filter 3: Check if cell is used
            is_placed = cell_name in used_fabric_cells
            is_in_netlist = cell_name in logical_cells
            
            if is_placed or is_in_netlist:
                skipped_used += 1
                continue
            
            # This cell is unused - add it
            unused_by_tile[tile_key].append(cell)
    
    print(f"  Total fabric cells: {total_fabric_cells}")
    print(f"  Skipped macros: {skipped_macro}")
    print(f"  Skipped infrastructure: {skipped_infra}")
    print(f"  Skipped used cells: {skipped_used}")
    
    return dict(unused_by_tile)


# ===============================================================
# Claim TIE Cells (conb_1)
# ===============================================================

def claim_tie_cells(fabric_db: Dict[str, Any], 
                    unused_by_tile: Dict[str, List]) -> Dict[str, str]:
    """
    Claim one sky130_fd_sc_hd__conb_1 cell per tile for tie-low.
    Search ALL cells in fabric (not just unused_by_tile since we filtered out CONBs).
    
    Returns:
        Dict[tile_key, tie_cell_name]
    """
    tie_cells = {}
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    
    # Only claim tie cells for tiles that have unused logic cells
    for tile_key in unused_by_tile.keys():
        tile_data = cells_by_tile.get(tile_key, {})
        
        # Search ALL cells in this tile for conb_1
        for cell in tile_data.get("cells", []):
            cell_type = cell.get("cell_type", "")
            if "conb_1" in cell_type.lower():
                tie_cells[tile_key] = cell.get("name")
                print(f"  Tile {tile_key}: Claimed tie cell {cell.get('name')}")
                break
        
        if tile_key not in tie_cells:
            print(f"  Warning: No conb_1 available in tile {tile_key}")
    
    return tie_cells


# ===============================================================
# Modify Netlist - Add TIE Connections
# ===============================================================

def add_tie_connections(logical_db: Dict[str, Any],
                        fabric_db: Dict[str, Any],
                        unused_by_tile: Dict[str, List],
                        tie_cells: Dict[str, str]) -> Dict[str, Any]:
    """
    Modify logical_db to add conb_1 cells and tie unused inputs.
    
    Simple version: One tie cell per tile, ties all unused cells.
    
    Returns:
        Updated logical_db
    """
    cells = logical_db.get("cells", {})
    nets = logical_db.get("nets", {})
    cells_by_type = logical_db.get("cells_by_type", {})
    
    # Create new net ID generator
    max_net_id = max((int(nid) for nid in nets.keys() if str(nid).isdigit()), 
                     default=0)
    
    def get_new_net_id():
        nonlocal max_net_id
        max_net_id += 1
        return max_net_id
    
    modifications = []
    warnings = []
    
    # Add conb_1 cells to logical netlist
    for tile_key, tie_cell_name in tie_cells.items():
        if tie_cell_name not in cells:
            # Add tie cell
            cells[tie_cell_name] = {
                "type": "sky130_fd_sc_hd__conb_1",
                "pins": {}
            }
            cells_by_type.setdefault("sky130_fd_sc_hd__conb_1", []).append(tie_cell_name)
            
            # Create output net for LO (logic 0) only
            # Power-down ECO typically only needs LO
            lo_net_id = get_new_net_id()
            
            # Connect LO pin
            cells[tie_cell_name]["pins"]["LO"] = lo_net_id
            nets[lo_net_id] = {
                "name": f"tie_lo_{tile_key}",
                "connections": [(tie_cell_name, "LO")]
            }
            
            modifications.append(f"Added tie cell {tie_cell_name} in tile {tile_key}")
    
    # Tie unused cell inputs
    for tile_key, unused_cells in unused_by_tile.items():
        if tile_key not in tie_cells:
            continue
            
        tie_cell_name = tie_cells[tile_key]
        
        # Find the LO net for this tie cell
        if tie_cell_name not in cells:
            continue
        
        tie_lo_net = cells[tie_cell_name]["pins"].get("LO")
        if not tie_lo_net:
            continue
        
        # Track statistics for this tile
        cells_tied = 0
        pins_tied = 0
        
        for cell in unused_cells:
            cell_name = cell.get("name")
            cell_type = cell.get("cell_type", "")
            
            # Skip the tie cell itself
            if cell_name == tie_cell_name:
                continue
            
            # Double-check: skip macros and infrastructure
            if is_macro(cell_type) or is_infrastructure(cell_type):
                continue
            
            # Get actual input pins for this cell type
            input_pins = get_cell_input_pins(cell_type, fabric_db)
            
            if not input_pins:
                # Unknown cell type or no inputs - skip it
                warnings.append(f"Skipped {cell_name} (type: {cell_type}) - no pins found")
                continue
            
            # Add cell if not in logical netlist
            if cell_name not in cells:
                cells[cell_name] = {
                    "type": cell_type,
                    "pins": {}
                }
                cells_by_type.setdefault(cell_type, []).append(cell_name)
                
                # Tie all inputs to LO
                for pin in input_pins:
                    cells[cell_name]["pins"][pin] = tie_lo_net
                    nets[tie_lo_net]["connections"].append((cell_name, pin))
                
                cells_tied += 1
                pins_tied += len(input_pins)
        
        if cells_tied > 0:
            modifications.append(
                f"Tile {tile_key}: Tied {cells_tied} cells ({pins_tied} pins) to tie-low"
            )
    
    # Update logical_db
    logical_db["cells"] = cells
    logical_db["nets"] = nets
    logical_db["cells_by_type"] = cells_by_type
    logical_db["meta"]["eco_modifications"] = modifications
    logical_db["meta"]["eco_warnings"] = warnings
    
    return logical_db


# ===============================================================
# Generate Statistics
# ===============================================================

def generate_eco_report(unused_by_tile: Dict[str, List],
                       tie_cells: Dict[str, str],
                       logical_db: Dict[str, Any]) -> str:
    """Generate a human-readable ECO report."""
    report = []
    report.append("=" * 70)
    report.append("POWER-DOWN ECO REPORT")
    report.append("=" * 70)
    report.append("")
    
    # Summary
    total_unused = sum(len(cells) for cells in unused_by_tile.values())
    report.append(f"Total unused cells found: {total_unused}")
    report.append(f"Tiles with unused cells: {len(unused_by_tile)}")
    report.append(f"Tie cells claimed: {len(tie_cells)}")
    report.append("")
    
    # Per-tile breakdown
    report.append("PER-TILE BREAKDOWN:")
    report.append("-" * 70)
    for tile_key in sorted(unused_by_tile.keys()):
        unused_count = len(unused_by_tile[tile_key])
        tie_cell = tie_cells.get(tile_key, "NONE")
        report.append(f"  {tile_key}: {unused_count} unused cells, Tie: {tie_cell}")
    
    report.append("")
    
    # Modifications
    modifications = logical_db.get("meta", {}).get("eco_modifications", [])
    if modifications:
        report.append("MODIFICATIONS:")
        report.append("-" * 70)
        for mod in modifications:
            report.append(f"  • {mod}")
    
    report.append("")
    
    # Warnings
    warnings = logical_db.get("meta", {}).get("eco_warnings", [])
    if warnings:
        report.append("WARNINGS:")
        report.append("-" * 70)
        for warn in warnings:
            report.append(f"  ⚠ {warn}")
        report.append("")
    
    report.append("=" * 70)
    
    return "\n".join(report)


# ===============================================================
# Main ECO Flow - From Source Files
# ===============================================================

def run_power_down_eco_from_sources(
    design_json: str,
    fabric_cells_yaml: str,
    pins_yaml: str,
    fabric_def_yaml: str,
    placement_map_file: str = None,
    output_dir: str = "eco_output",
    verbose: bool = True
) -> Tuple[Dict[str, Any], str]:
    """
    Run Power-Down ECO flow directly from source files using parse modules.
    
    This is the PRIMARY interface - parses source files directly without
    needing intermediate logical_db.json or fabric_db.yaml files.
    
    Args:
        design_json: Path to Yosys-generated design_mapped.json
        fabric_cells_yaml: Path to fabric_cells.yaml
        pins_yaml: Path to pins.yaml
        fabric_def_yaml: Path to fabric.yaml (fabric definition)
        placement_map_file: Path to placement mapping file (optional)
        output_dir: Output directory for results
        verbose: If True, print progress messages
    
    Returns:
        Tuple of (updated_logical_db, eco_report_text)
    
    Example:
        >>> from power_down_eco import run_power_down_eco_from_sources
        >>> 
        >>> updated_db, report = run_power_down_eco_from_sources(
        ...     design_json="designs/6502_mapped.json",
        ...     fabric_cells_yaml="fabric/fabric_cells.yaml",
        ...     pins_yaml="fabric/pins.yaml",
        ...     fabric_def_yaml="fabric/fabric.yaml",
        ...     placement_map_file="placement.map"
        ... )
        >>> print(report)
    """
    if verbose:
        print("=" * 70)
        print("POWER-DOWN ECO FLOW (FIXED VERSION)")
        print("=" * 70)
        print()
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Step 1: Parse design and fabric databases
    if verbose:
        print("Step 1: Parsing source files...")
        print(f"  Design JSON: {design_json}")
        print(f"  Fabric cells: {fabric_cells_yaml}")
        print(f"  Pins: {pins_yaml}")
        print(f"  Fabric def: {fabric_def_yaml}")
    
    # Parse logical netlist
    logical_db, _ = parse_design_json(design_json)
    if verbose:
        print(f"  ✓ Parsed logical DB: {len(logical_db.get('cells', {}))} cells")
    
    # Build fabric database
    fabric_db = build_fabric_db(fabric_cells_yaml, pins_yaml, fabric_def_yaml)
    if verbose:
        print(f"  ✓ Built fabric DB: {len(fabric_db.get('fabric', {}).get('cells_by_tile', {}))} tiles")
    
    # Load placement mapping
    placement_map = load_placement_mapping(placement_map_file)
    if placement_map:
        if verbose:
            print(f"  ✓ Loaded placement map: {len(placement_map)} placements")
    else:
        if verbose:
            print(f"  ⚠ WARNING: No placement map provided!")
    print()
    
    # Step 2: Identify unused cells
    if verbose:
        print("Step 2: Identifying unused cells (with enhanced filtering)...")
    unused_by_tile = identify_unused_cells(logical_db, fabric_db, placement_map)
    total_unused = sum(len(cells) for cells in unused_by_tile.values())
    if verbose:
        print(f"  Found {total_unused} unused cells across {len(unused_by_tile)} tiles")
        print()
    
    # Step 3: Claim tie cells
    if verbose:
        print("Step 3: Claiming tie cells (conb_1)...")
    tie_cells = claim_tie_cells(fabric_db, unused_by_tile)
    if verbose:
        print()
    
    # Step 4: Modify netlist
    if verbose:
        print("Step 4: Modifying netlist...")
    updated_logical_db = add_tie_connections(logical_db, fabric_db, unused_by_tile, tie_cells)
    if verbose:
        mods = len(updated_logical_db.get('meta', {}).get('eco_modifications', []))
        warns = len(updated_logical_db.get('meta', {}).get('eco_warnings', []))
        print(f"  Applied {mods} modifications")
        if warns > 0:
            print(f"  Generated {warns} warnings")
        print()
    
    # Step 5: Generate outputs
    if verbose:
        print("Step 5: Generating outputs...")
    
    # Write updated logical_db
    output_db_path = os.path.join(output_dir, "logical_db_eco.json")
    with open(output_db_path, 'w') as f:
        json.dump(updated_logical_db, f, indent=2)
    if verbose:
        print(f"  Written: {output_db_path}")
    
    # Generate report
    report = generate_eco_report(unused_by_tile, tie_cells, updated_logical_db)
    
    # Write ECO report
    report_path = os.path.join(output_dir, "eco_report.txt")
    with open(report_path, 'w') as f:
        f.write(report)
    if verbose:
        print(f"  Written: {report_path}")
    
    # Write unused cells list
    unused_list_path = os.path.join(output_dir, "unused_cells.yaml")
    with open(unused_list_path, 'w') as f:
        yaml.dump({"unused_by_tile": unused_by_tile}, f, default_flow_style=False)
    if verbose:
        print(f"  Written: {unused_list_path}")
    
    if verbose:
        print()
        print(report)
        print()
        print(f"ECO completed successfully! Outputs in: {output_dir}/")
        
        if not placement_map:
            print()
            print("⚠️  IMPORTANT: Rerun with placement map for accurate results!")
    
    return updated_logical_db, report


# ===============================================================
# Entry Point
# ===============================================================

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python power_down_eco.py <design_mapped.json> <fabric_cells.yaml> <pins.yaml> <fabric.yaml> [placement.map]")
        print()
        print("Arguments:")
        print("  design_mapped.json  - Yosys-generated netlist (input to parse_design.py)")
        print("  fabric_cells.yaml   - Fabric cell placement data")
        print("  pins.yaml          - Pin placement data")
        print("  fabric.yaml        - Fabric definition with cell dimensions")
        print("  placement.map      - [Optional] Placement mapping file")
        print()
        print("Example:")
        print("  python power_down_eco.py designs/6502_mapped.json \\")
        print("                           fabric/fabric_cells.yaml \\")
        print("                           fabric/pins.yaml \\")
        print("                           fabric/fabric.yaml \\")
        print("                           placement.map")
        print()
        print("Placement file format (.map):")
        print('  fabric_cell  cell_type  x  y  ->  logical_instance')
        print("Or JSON/YAML:")
        print('  {"logical_inst": "fabric_cell", ...}')
        print()
        print("=" * 70)
        print("For Python integration:")
        print("=" * 70)
        print("from power_down_eco import run_power_down_eco_from_sources")
        print()
        print("updated_db, report = run_power_down_eco_from_sources(")
        print("    design_json='designs/6502_mapped.json',")
        print("    fabric_cells_yaml='fabric/fabric_cells.yaml',")
        print("    pins_yaml='fabric/pins.yaml',")
        print("    fabric_def_yaml='fabric/fabric.yaml',")
        print("    placement_map_file='placement.map'")
        print(")")
        sys.exit(1)
    
    design_json = sys.argv[1]
    fabric_cells_yaml = sys.argv[2]
    pins_yaml = sys.argv[3]
    fabric_def_yaml = sys.argv[4]
    placement_map_file = sys.argv[5] if len(sys.argv) > 5 else None
    
    if not os.path.exists(design_json):
        print(f"Error: Design JSON not found: {design_json}")
        sys.exit(1)
    
    if not os.path.exists(fabric_cells_yaml):
        print(f"Error: Fabric cells YAML not found: {fabric_cells_yaml}")
        sys.exit(1)
    
    if not os.path.exists(pins_yaml):
        print(f"Error: Pins YAML not found: {pins_yaml}")
        sys.exit(1)
    
    if not os.path.exists(fabric_def_yaml):
        print(f"Error: Fabric definition YAML not found: {fabric_def_yaml}")
        sys.exit(1)
    
    if placement_map_file and not os.path.exists(placement_map_file):
        print(f"Error: Placement map not found: {placement_map_file}")
        sys.exit(1)
    
    run_power_down_eco_from_sources(
        design_json,
        fabric_cells_yaml,
        pins_yaml,
        fabric_def_yaml,
        placement_map_file
    )
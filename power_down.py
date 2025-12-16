#!/usr/bin/env python3
"""
power_down.py
-----------------
Implements Power-Down ECO by tying unused logic cell inputs to optimal conb cells.

Enhanced Strategy:
  1. Parse logical netlist and fabric database using parse_design.py and build_fabric_db.py
  2. Load tie database (tie_db.yaml) with optimal tie configurations per cell type
  3. Identify unused cells in the fabric
  4. Claim both sky130_fd_sc_hd__conb_1 cells per tile (tie-low and tie-high)
  5. Modify netlist to connect unused inputs to optimal conb output (HI or LO)
  6. Generate updated netlist and placement with power savings report

Usage:
    # Direct from source files (recommended):
    python power_down_eco.py design_mapped.json fabric_cells.yaml pins.yaml fabric.yaml tie_db.yaml [placement.map]

    # From Python (in-memory):
    from power_down_eco import run_power_down_eco

    updated_db, report = run_power_down_eco(
        logical_db=logical_db,
        fabric_db=fabric_db,
        tie_db=tie_db,
        placement_map=placement_map
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
from parse_lib import parse_liberty_leakage, get_optimal_tie_for_cell, heuristic_tie_selection


# ===============================================================
# Cell Type Classification
# ===============================================================

def is_macro(cell_type: str) -> bool:
    """
    Check if cell is a macro (not a standard cell).
    Macros should NOT be tied - they have complex internal structure.
    """
    macro_patterns = [
        "dfbbp",  # Flip-flop banks
        "sram",  # Memory
        "regfile",  # Register files
        "macro",  # Generic macro indicator
        "dffram",  # DFF RAM blocks
        "fifo",  # FIFO blocks
    ]
    cell_lower = cell_type.lower()
    return any(p in cell_lower for p in macro_patterns)


def is_infrastructure(cell_type: str) -> bool:
    """
    Check if cell is infrastructure (tap, decap, filler, etc.).
    These should be skipped - they're not logic.
    """
    infra_patterns = [
        "tap",  # Tap cells (power/ground)
        "decap",  # Decoupling capacitors
        "conb",  # Tie cells (already serving this purpose)
        "fill",  # Filler cells
        "diode",  # Antenna diodes
        "antenna",  # Antenna protection
        "endcap",  # End caps
        "welltap",  # Well taps
    ]
    cell_lower = cell_type.lower()
    return any(p in cell_lower for p in infra_patterns)


# ===============================================================
# Tie Database Management
# ===============================================================

def load_leakage_database(liberty_file: str) -> Dict[str, Dict[str, Any]]:
    """
    Load leakage power data from Liberty file using parse_lib.
    
    Args:
        liberty_file: Path to Liberty .lib file (e.g., sky130_fd_sc_hd__tt_025C_1v80.lib)
        
    Returns:
        Dict[cell_type, {leakage_states, min_state, min_power, optimal_tie}]
    """
    if not os.path.exists(liberty_file):
        print(f"Warning: Liberty file not found: {liberty_file}")
        print("  Will use heuristic-based tie selection for all cells")
        return {}
    
    try:
        leakage_db = parse_liberty_leakage(liberty_file, verbose=False)
        print(f"Loaded Liberty file: {len(leakage_db)} cell types with leakage data")
        return leakage_db
    except Exception as e:
        print(f"Warning: Error parsing Liberty file: {e}")
        print("  Will use heuristic-based tie selection for all cells")
        return {}


def get_input_tie_states(cell_type: str, leakage_db: Dict[str, Any]) -> Dict[str, str]:
    """
    Get per-input tie states for a cell type using leakage data from Liberty.
    
    Each input can be tied to either "HI" or "LO" based on the minimum leakage state.
    
    Args:
        cell_type: Cell type string (e.g., "sky130_fd_sc_hd__nand2_1")
        leakage_db: Leakage database loaded from Liberty file via parse_lib
        
    Returns:
        Dict[input_name, "HI" or "LO"] mapping each input to its optimal tie state
        Example: {"A": "HI", "B": "LO", "C": "HI"}
        Falls back to heuristic (all HI or all LO) if not in database
    """
    if cell_type in leakage_db:
        cell_data = leakage_db[cell_type]
        input_ties = cell_data.get("input_ties", {})
        if input_ties:
            return input_ties
    
    # Fallback: use heuristic for all inputs
    optimal_summary = get_optimal_tie_for_cell(cell_type, leakage_db)
    return {"__summary__": optimal_summary}


def get_power_savings(cell_type: str, leakage_db: Dict[str, Any]) -> float:
    """
    Get expected power savings for a cell type based on leakage data.
    
    Calculates savings as the ratio of minimum leakage to average leakage.
    
    Args:
        cell_type: Cell type string
        leakage_db: Leakage database loaded from Liberty file via parse_lib
        
    Returns:
        Savings percentage (0.0 if not found)
    """
    if cell_type in leakage_db:
        data = leakage_db[cell_type]
        min_power = data.get("min_power", 0.0)
        avg_power = data.get("avg_power", 1.0)
        if avg_power > 0:
            return ((avg_power - min_power) / avg_power) * 100.0
    return 0.0


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
                          placement_map: Dict[str, str] = None,
                          limit_tie_cells: bool = True) -> Dict[str, List[str]]:
    """
    Find unused cells in fabric that are not used in logical netlist.

    Enhanced version with proper filtering:
    - Skip macros (DFBBP, SRAM, etc.)
    - Skip infrastructure (TAP, DECAP, etc.)
    - Verify cell is truly unused (not placed AND not in netlist)
    - IMPORTANT: Optionally limit tie cell usage to reduce routing congestion

    Args:
        logical_db: The logical netlist database
        fabric_db: The fabric database with all available cells
        placement_map: Dict mapping logical_instance -> fabric_cell_name
        limit_tie_cells: If True, limit tie cells per tile to reduce congestion

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
# Claim TIE Cells (conb_1 for both HI and LO)
# ===============================================================

def claim_tie_cells(fabric_db: Dict[str, Any],
                    unused_by_tile: Dict[str, List],
                    logical_db: Dict[str, Any] = None,
                    placement_map: Dict[str, str] = None,
                    max_ties_per_tile: int = 1) -> Dict[str, Dict[str, str]]:
    """
    Claim sky130_fd_sc_hd__conb_1 cells per tile for tie connections.
    
    **OPTIMIZED LOGIC:**
    One conb_1 cell has BOTH HI and LO output pins, so claiming 1 cell per tile
    provides BOTH tie_hi and tie_lo nets. This reduces physical cell count by 50%
    while preserving full HI/LO optimization for tied cells.
    
    Search ALL cells in fabric (not just unused_by_tile since we filtered out CONBs).
    Skip CONB cells that are already used in the logical netlist or already placed in the placement map.

    Args:
        fabric_db: Fabric database with available cells
        unused_by_tile: Dict of unused cells per tile
        logical_db: Logical netlist database to check for already-used CONB cells
        placement_map: Dict mapping logical_instance -> fabric_cell_name (used cells from placement)
        max_ties_per_tile: Maximum number of tie CELLS to claim per tile (default 1, provides 2 nets: HI+LO)

    Returns:
        Dict[tile_key, {"HI": cell_name, "LO": cell_name}] where both outputs come from same cell
    """
    tie_cells = {}
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    
    # Get set of cells already used in logical netlist
    used_cells = set(logical_db.get("cells", {}).keys()) if logical_db else set()
    
    # Get set of fabric cells already placed via placement map
    placed_fabric_cells = set(placement_map.values()) if placement_map else set()
    
    # Combined set of unavailable cells (both in netlist and physically placed)
    unavailable_cells = used_cells | placed_fabric_cells

    # Only claim tie cells for tiles that have unused logic cells
    ties_claimed = 0
    for tile_key in unused_by_tile.keys():
        if ties_claimed >= max_ties_per_tile * len(unused_by_tile):
            print(f"  [CONGESTION FIX] Stopping tie cell claims at {ties_claimed} total")
            break
            
        tile_data = cells_by_tile.get(tile_key, {})
        
        # Search for ONE available CONB cell
        available_conb = None

        # Search ALL cells in this tile for conb_1
        for cell in tile_data.get("cells", []):
            cell_type = cell.get("cell_type", "")
            cell_name = cell.get("name", "")
            
            if "conb_1" in cell_type.lower():
                # Check if this CONB cell is unavailable (used in netlist OR already placed in fabric)
                if cell_name in unavailable_cells:
                    continue
                
                # This CONB cell is available
                available_conb = cell_name
                break

        if available_conb:
            # One conb_1 cell provides BOTH HI and LO outputs
            # So both HI and LO nets come from the same physical cell
            tie_cells[tile_key] = {
                "HI": available_conb,   # Same cell, HI pin
                "LO": available_conb    # Same cell, LO pin
            }
            ties_claimed += 1
            print(f"  Tile {tile_key}: Claimed conb_1 cell {available_conb} (provides both HI and LO outputs)")
        else:
            print(f"  Warning: No available conb_1 in tile {tile_key} (all are used or none exist)")

    return tie_cells

    return tie_cells


# ===============================================================
# Modify Netlist - Add TIE Connections with Optimal Selection
# ===============================================================

def add_tie_connections(logical_db: Dict[str, Any],
                        fabric_db: Dict[str, Any],
                        leakage_db: Dict[str, Any],
                        unused_by_tile: Dict[str, List],
                        tie_cells: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """
    Modify logical_db to add conb_1 cells and tie unused inputs optimally.

    Enhanced version: Uses leakage_db (from parse_lib) to determine optimal HI/LO for each cell type.

    Returns:
        Updated logical_db with power savings statistics
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
    power_stats = {
        "total_cells_tied": 0,
        "total_pins_tied": 0,
        "tied_to_hi": 0,
        "tied_to_lo": 0,
        "tied_to_mixed": 0,
        "total_savings_pct": 0.0,
        "cells_by_tie": {"HI": [], "LO": [], "MIXED": []}
    }

    # Add conb_1 cells to logical netlist
    tie_nets = {}  # tile_key -> {"HI": net_id, "LO": net_id}
    
    for tile_key, tie_cell_dict in tie_cells.items():
        tile_nets = {}
        
        # Add the conb_1 cell once (it will have both HI and LO pins)
        tie_cell_name = tie_cell_dict.get("HI") or tie_cell_dict.get("LO")
        if tie_cell_name and tie_cell_name not in cells:
            cells[tie_cell_name] = {
                "type": "sky130_fd_sc_hd__conb_1",
                "pins": {}
            }
            cells_by_type.setdefault("sky130_fd_sc_hd__conb_1", []).append(tie_cell_name)
        
        # Create HI output net if HI cell available
        if "HI" in tie_cell_dict:
            hi_cell_name = tie_cell_dict["HI"]
            # Create output net for HI (logic 1)
            hi_net_id = get_new_net_id()
            if tie_cell_name in cells:  # Ensure cell exists
                cells[tie_cell_name]["pins"]["HI"] = hi_net_id
            nets[hi_net_id] = {
                "name": f"tie_hi_{tile_key}",
                "connections": [(tie_cell_name, "HI")]
            }
            tile_nets["HI"] = hi_net_id
            modifications.append(f"Added tie-HI output from {tie_cell_name} in tile {tile_key}")
        
        # Create LO output net if LO cell available
        # NOTE: LO cell is often the SAME as HI cell (same conb_1 has both pins)
        if "LO" in tie_cell_dict:
            lo_cell_name = tie_cell_dict["LO"]
            # Create output net for LO (logic 0)
            lo_net_id = get_new_net_id()
            if lo_cell_name not in cells:  # Only add cell if not already added
                cells[lo_cell_name] = {
                    "type": "sky130_fd_sc_hd__conb_1",
                    "pins": {}
                }
                cells_by_type.setdefault("sky130_fd_sc_hd__conb_1", []).append(lo_cell_name)
            
            if lo_cell_name in cells:  # Ensure cell exists
                cells[lo_cell_name]["pins"]["LO"] = lo_net_id
            nets[lo_net_id] = {
                "name": f"tie_lo_{tile_key}",
                "connections": [(lo_cell_name, "LO")]
            }
            tile_nets["LO"] = lo_net_id
            modifications.append(f"Added tie-LO output from {lo_cell_name} in tile {tile_key}")
        
        tie_nets[tile_key] = tile_nets

    # Tie unused cell inputs with optimal configuration
    for tile_key, unused_cells in unused_by_tile.items():
        if tile_key not in tie_cells:
            continue

        tile_tie_nets = tie_nets.get(tile_key, {})
        if not tile_tie_nets:
            continue

        # Track statistics for this tile
        cells_tied = 0
        pins_tied = 0
        hi_count = 0
        lo_count = 0

        for cell in unused_cells:
            cell_name = cell.get("name")
            cell_type = cell.get("cell_type", "")

            # Skip the tie cells themselves
            if cell_name in tie_cells[tile_key].values():
                continue

            # Double-check: skip macros and infrastructure
            if is_macro(cell_type) or is_infrastructure(cell_type):
                continue

            # Get actual input pins for this cell type
            input_pins = get_cell_input_pins(cell_type, fabric_db)

            if not input_pins:
                warnings.append(f"Skipped {cell_name} (type: {cell_type}) - no pins found")
                continue

            # Get per-input tie states from leakage database
            input_tie_states = get_input_tie_states(cell_type, leakage_db)
            savings_pct = get_power_savings(cell_type, leakage_db)

            # Add cell if not in logical netlist
            if cell_name not in cells:
                cells[cell_name] = {
                    "type": cell_type,
                    "pins": {}
                }
                cells_by_type.setdefault(cell_type, []).append(cell_name)

                # Tie each input to its optimal net (HI or LO)
                pins_tied_for_cell = 0
                cell_used_hi = False
                cell_used_lo = False
                for pin in input_pins:
                    # Determine which tie net to use for this specific pin
                    if pin in input_tie_states:
                        # Pin-specific tie state available
                        pin_tie = input_tie_states[pin]
                    elif "__summary__" in input_tie_states:
                        # Fallback to summary (all inputs same)
                        pin_tie = input_tie_states["__summary__"]
                    else:
                        # Ultimate fallback to LO
                        pin_tie = "LO"
                    
                    # Check if we have the required tie net
                    if pin_tie not in tile_tie_nets:
                        # Fallback to whatever is available
                        if "LO" in tile_tie_nets:
                            pin_tie = "LO"
                        elif "HI" in tile_tie_nets:
                            pin_tie = "HI"
                        else:
                            warnings.append(f"Skipped tying {cell_name}.{pin} - no tie cells available")
                            continue
                    
                    tie_net = tile_tie_nets[pin_tie]
                    cells[cell_name]["pins"][pin] = tie_net
                    nets[tie_net]["connections"].append((cell_name, pin))
                    
                    pins_tied_for_cell += 1
                    if pin_tie == "HI":
                        hi_count += 1
                        cell_used_hi = True
                    else:
                        lo_count += 1
                        cell_used_lo = True

                if pins_tied_for_cell > 0:
                    cells_tied += 1
                    pins_tied += pins_tied_for_cell

                    # Determine cell tie category
                    if cell_used_hi and cell_used_lo:
                        cell_tie_category = "MIXED"
                        power_stats["tied_to_mixed"] += 1
                    elif cell_used_hi:
                        cell_tie_category = "HI"
                    else:
                        cell_tie_category = "LO"

                    # Track power savings
                    power_stats["total_savings_pct"] += savings_pct
                    power_stats["cells_by_tie"][cell_tie_category].append({
                        "cell": cell_name,
                        "type": cell_type,
                        "savings_pct": savings_pct,
                        "input_ties": input_tie_states
                    })

        if cells_tied > 0:
            power_stats["total_cells_tied"] += cells_tied
            power_stats["total_pins_tied"] += pins_tied
            power_stats["tied_to_hi"] += hi_count
            power_stats["tied_to_lo"] += lo_count
            
            modifications.append(
                f"Tile {tile_key}: Tied {cells_tied} cells ({pins_tied} pins) - "
                f"{hi_count} to HI, {lo_count} to LO"
            )

    # Calculate average savings
    if power_stats["total_cells_tied"] > 0:
        power_stats["avg_savings_pct"] = power_stats["total_savings_pct"] / power_stats["total_cells_tied"]

    # Update logical_db
    logical_db["cells"] = cells
    logical_db["nets"] = nets
    logical_db["cells_by_type"] = cells_by_type
    logical_db["meta"]["eco_modifications"] = modifications
    logical_db["meta"]["eco_warnings"] = warnings
    logical_db["meta"]["power_stats"] = power_stats

    return logical_db


# ===============================================================
# Generate Statistics
# ===============================================================

def generate_eco_report(unused_by_tile: Dict[str, List],
                        tie_cells: Dict[str, Dict[str, str]],
                        logical_db: Dict[str, Any]) -> str:
    """Generate a human-readable ECO report with power savings."""
    report = []
    report.append("=" * 70)
    report.append("POWER-DOWN ECO REPORT (WITH OPTIMAL TIE SELECTION)")
    report.append("=" * 70)
    report.append("")

    # Summary
    total_unused = sum(len(cells) for cells in unused_by_tile.values())
    report.append(f"Total unused cells found: {total_unused}")
    report.append(f"Tiles with unused cells: {len(unused_by_tile)}")
    
    # Count tie cells
    total_hi = sum(1 for t in tie_cells.values() if "HI" in t)
    total_lo = sum(1 for t in tie_cells.values() if "LO" in t)
    report.append(f"Tie-HI cells claimed: {total_hi}")
    report.append(f"Tie-LO cells claimed: {total_lo}")
    report.append("")

    # Power savings summary
    power_stats = logical_db.get("meta", {}).get("power_stats", {})
    if power_stats:
        report.append("POWER SAVINGS SUMMARY:")
        report.append("-" * 70)
        report.append(f"  Total cells tied: {power_stats.get('total_cells_tied', 0)}")
        report.append(f"  Total pins tied: {power_stats.get('total_pins_tied', 0)}")
        report.append(f"  Cells tied to HI: {power_stats.get('tied_to_hi', 0)}")
        report.append(f"  Cells tied to LO: {power_stats.get('tied_to_lo', 0)}")
        avg_savings = power_stats.get('avg_savings_pct', 0.0)
        report.append(f"  Average power savings: {avg_savings:.2f}%")
        report.append("")

    # Per-tile breakdown
    report.append("PER-TILE BREAKDOWN:")
    report.append("-" * 70)
    for tile_key in sorted(unused_by_tile.keys()):
        unused_count = len(unused_by_tile[tile_key])
        tie_dict = tie_cells.get(tile_key, {})
        tie_hi = tie_dict.get("HI", "NONE")
        tie_lo = tie_dict.get("LO", "NONE")
        report.append(f"  {tile_key}: {unused_count} unused cells")
        report.append(f"    Tie-HI: {tie_hi}")
        report.append(f"    Tie-LO: {tie_lo}")

    report.append("")

    # Top power savers
    if power_stats and power_stats.get("cells_by_tie"):
        report.append("TOP POWER SAVERS:")
        report.append("-" * 70)
        
        all_tied = []
        for tie_type in ["HI", "LO"]:
            all_tied.extend(power_stats["cells_by_tie"].get(tie_type, []))
        
        # Sort by savings percentage
        all_tied.sort(key=lambda x: x.get("savings_pct", 0.0), reverse=True)
        
        for i, cell_info in enumerate(all_tied[:10], 1):  # Top 10
            report.append(f"  {i}. {cell_info['cell']}")
            report.append(f"     Type: {cell_info['type']}")
            report.append(f"     Savings: {cell_info['savings_pct']:.2f}%")
        
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
# Main ECO Flow - From Pre-built Databases
# ===============================================================

def run_power_down_eco(
        logical_db: Dict[str, Any],
        fabric_db: Dict[str, Any],
        leakage_db: Dict[str, Any],
        placement_map: Dict[str, str] = None,
        output_dir: str = "eco_output",
        verbose: bool = True
) -> Tuple[Dict[str, Any], str]:
    """
    Run Power-Down ECO flow using pre-built databases with optimal tie selection.

    This is the PRIMARY interface - takes pre-built logical_db, fabric_db, and leakage_db.

    Args:
        logical_db: Pre-built logical database (from parse_design_json)
        fabric_db: Pre-built fabric database (from build_fabric_db)
        leakage_db: Leakage database (from parse_liberty_leakage via parse_lib)
        placement_map: Dict mapping logical_instance -> fabric_cell_name
        output_dir: Output directory for results
        verbose: If True, print progress messages

    Returns:
        Tuple of (updated_logical_db, eco_report_text)
    """
    if verbose:
        print("=" * 70)
        print("POWER-DOWN ECO FLOW (WITH OPTIMAL TIE SELECTION)")
        print("=" * 70)
        print()

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Verify databases
    if verbose:
        print("Step 1: Verifying databases...")
        print(f"  Logical DB: {len(logical_db.get('cells', {}))} cells")
        print(f"  Fabric DB: {len(fabric_db.get('fabric', {}).get('cells_by_tile', {}))} tiles")
        print(f"  Leakage DB: {len(leakage_db)} cell type leakage entries")
        if placement_map:
            print(f"  Placement map: {len(placement_map)} placements")
        else:
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
        print("Step 3: Claiming tie cells (limited to 1 per tile to reduce routing congestion)...")
    # ROUTING CONGESTION FIX: Limit tie cells to 1 per tile instead of 2
    # This reduces the number of tie nets from ~2614 violations down to manageable levels
    tie_cells = claim_tie_cells(fabric_db, unused_by_tile, logical_db, placement_map, max_ties_per_tile=1)
    if verbose:
        print()

    # Step 4: Modify netlist with optimal tie selection
    if verbose:
        print("Step 4: Modifying netlist with optimal tie selection...")
    updated_logical_db = add_tie_connections(logical_db, fabric_db, leakage_db, unused_by_tile, tie_cells)
    if verbose:
        mods = len(updated_logical_db.get('meta', {}).get('eco_modifications', []))
        warns = len(updated_logical_db.get('meta', {}).get('eco_warnings', []))
        power_stats = updated_logical_db.get('meta', {}).get('power_stats', {})
        print(f"  Applied {mods} modifications")
        if warns > 0:
            print(f"  Generated {warns} warnings")
        if power_stats:
            print(f"  Tied {power_stats.get('total_cells_tied', 0)} cells")
            print(f"    - {power_stats.get('tied_to_hi', 0)} to HI")
            print(f"    - {power_stats.get('tied_to_lo', 0)} to LO")
            avg_savings = power_stats.get('avg_savings_pct', 0.0)
            print(f"  Average power savings: {avg_savings:.2f}%")
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

    # Write power savings details
    power_stats = updated_logical_db.get('meta', {}).get('power_stats', {})
    if power_stats:
        power_stats_path = os.path.join(output_dir, "power_savings.yaml")
        with open(power_stats_path, 'w') as f:
            yaml.dump(power_stats, f, default_flow_style=False)
        if verbose:
            print(f"  Written: {power_stats_path}")

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
        print(
            "Usage: python power_down_eco.py <design_mapped.json> <fabric_cells.yaml> <pins.yaml> <fabric.yaml> [options]")
        print()
        print("Arguments:")
        print("  design_mapped.json  - Yosys-generated netlist (input to parse_design.py)")
        print("  fabric_cells.yaml   - Fabric cell placement data")
        print("  pins.yaml          - Pin placement data")
        print("  fabric.yaml        - Fabric definition with cell dimensions")
        print()
        print("Options:")
        print("  --liberty <file>   - Liberty file for leakage analysis (default: tech/sky130_fd_sc_hd__tt_025C_1v80.lib)")
        print("  --placement <file> - Placement mapping file (.map, .json, or .yaml)")
        print("  --output <dir>     - Output directory (default: eco_output)")
        print()
        print("Example:")
        print("  python power_down_eco.py designs/6502_mapped.json \\")
        print("                           fabric/fabric_cells.yaml \\")
        print("                           fabric/pins.yaml \\")
        print("                           fabric/fabric.yaml \\")
        print("                           --liberty tech/sky130_fd_sc_hd__tt_025C_1v80.lib \\")
        print("                           --placement placement.map")
        print()
        print("=" * 70)
        print("For Python integration:")
        print("=" * 70)
        print("from power_down_eco import run_power_down_eco")
        print("from parse_lib import parse_liberty_leakage")
        print()
        print("leakage_db = parse_liberty_leakage('tech/sky130_fd_sc_hd__tt_025C_1v80.lib')")
        print("updated_db, report = run_power_down_eco(")
        print("    logical_db=logical_db,")
        print("    fabric_db=fabric_db,")
        print("    leakage_db=leakage_db,")
        print("    placement_map=placement_map")
        print(")")
        sys.exit(1)

    design_json = sys.argv[1]
    fabric_cells_yaml = sys.argv[2]
    pins_yaml = sys.argv[3]
    fabric_def_yaml = sys.argv[4]
    
    # Parse command-line options
    liberty_file = "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
    placement_map_file = None
    output_dir = "eco_output"
    
    i = 5
    while i < len(sys.argv):
        if sys.argv[i] == "--liberty" and i + 1 < len(sys.argv):
            liberty_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--placement" and i + 1 < len(sys.argv):
            placement_map_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_dir = sys.argv[i + 1]
            i += 2
        else:
            i += 1

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

    if not os.path.exists(liberty_file):
        print(f"Error: Liberty file not found: {liberty_file}")
        print(f"       (Tried: {liberty_file})")
        sys.exit(1)

    if placement_map_file and not os.path.exists(placement_map_file):
        print(f"Error: Placement map not found: {placement_map_file}")
        sys.exit(1)

    # Build databases separately in main
    print("Building databases...")
    logical_db, _ = parse_design_json(design_json)
    fabric_db = build_fabric_db(fabric_cells_yaml, pins_yaml, fabric_def_yaml)
    
    # Parse Liberty file directly using parse_lib
    print(f"Parsing Liberty file for leakage analysis: {liberty_file}")
    leakage_db = parse_liberty_leakage(liberty_file, verbose=False)
    
    placement_map = load_placement_mapping(placement_map_file)
    print()

    # Run ECO with pre-built databases
    run_power_down_eco(
        logical_db=logical_db,
        fabric_db=fabric_db,
        leakage_db=leakage_db,
        placement_map=placement_map,
        output_dir=output_dir
    )
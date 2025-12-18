#!/usr/bin/env python3
"""
liberty_leakage_parser.py
--------------------------
Parse Liberty timing library to extract leakage power data and determine
optimal tie states (HI/LO) for power-down ECO.

Analyzes leakage_power() entries to find minimum leakage input combination
for each cell type.
"""

import re
import os
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


def parse_liberty_leakage(liberty_file: str, verbose: bool = False) -> Dict[str, Dict]:
    """
    Parse Liberty file to extract leakage power states for each cell.
    
    Args:
        liberty_file: Path to .lib file (e.g., sky130_fd_sc_hd__tt_025C_1v80.lib)
        verbose: Print parsing progress
    
    Returns:
        {
            cell_type: {
                "leakage_states": {state_str: power_value},
                "min_state": state_str,
                "min_power": float,
                "optimal_tie": "HI" or "LO" or "MIXED",
                "input_ties": {signal_name: "HI" or "LO"}  # Per-input tie states
            }
        }
    
    Example:
        {
            "sky130_fd_sc_hd__and2_2": {
                "leakage_states": {
                    "!A&!B": 0.0036338,
                    "A&B": 0.0018727,
                    ...
                },
                "min_state": "A&B",
                "min_power": 0.0018727,
                "optimal_tie": "HI",
                "input_ties": {"A": "HI", "B": "HI"}
            }
        }
    """
    leakage_db = {}
    current_cell = None
    current_value = None
    cells_parsed = 0
    
    with open(liberty_file, 'r') as f:
        for line in f:
            line = line.strip()
            
            # Detect cell definition: cell ("sky130_fd_sc_hd__and2_2") {
            if line.startswith('cell ('):
                match = re.search(r'cell\s*\(\s*"([^"]+)"\s*\)', line)
                if match:
                    current_cell = match.group(1)
                    leakage_db[current_cell] = {"leakage_states": {}}
                    cells_parsed += 1
            
            # Parse leakage power value
            if 'value :' in line and current_cell:
                match = re.search(r'value\s*:\s*([\d.]+)', line)
                if match:
                    current_value = float(match.group(1))
            
            # Parse leakage power condition (when)
            if 'when :' in line and current_cell and current_value is not None:
                match = re.search(r'when\s*:\s*"([^"]+)"', line)
                if match:
                    when_condition = match.group(1)
                    leakage_db[current_cell]["leakage_states"][when_condition] = current_value
                    current_value = None
    
    if verbose:
        print(f"Parsed {cells_parsed} cells from Liberty file")
    
    # Analyze each cell to find optimal tie state
    for cell_type, data in leakage_db.items():
        states = data["leakage_states"]
        
        if not states:
            continue
        
        # Find minimum leakage state
        min_state = min(states.items(), key=lambda x: x[1])
        data["min_state"] = min_state[0]
        data["min_power"] = min_state[1]
        
        # Determine optimal tie for each input signal
        summary_tie, input_ties = determine_tie_from_state(min_state[0])
        data["optimal_tie"] = summary_tie
        data["input_ties"] = input_ties  # Dict: signal -> "HI" or "LO"
        
        # Calculate average and worst-case for comparison
        data["avg_power"] = sum(states.values()) / len(states)
        data["max_power"] = max(states.values())
    
    return leakage_db


def determine_tie_from_state(state_str: str) -> Tuple[str, Dict[str, str]]:
    """
    Determine optimal tie states for individual inputs based on minimum leakage condition.
    
    For each input signal, determines whether it should be tied HIGH or LOW to achieve
    the minimum leakage state represented by the state_str.
    
    Examples:
        "A&B&C"           → ("HI", {"A": "HI", "B": "HI", "C": "HI"})
        "!A&!B&!C"        → ("LO", {"A": "LO", "B": "LO", "C": "LO"})
        "A&!B&C"          → ("MIXED", {"A": "HI", "B": "LO", "C": "HI"})
        "!A1&!A2&!B1"     → ("LO", {"A1": "LO", "A2": "LO", "B1": "LO"})
    
    Args:
        state_str: Boolean expression from Liberty "when" condition
    
    Returns:
        Tuple of:
            - "HI", "LO", or "MIXED" (summary)
            - Dict mapping each input signal to its tie state ("HI" or "LO")
    """
    # Split on & to get individual terms
    terms = [t.strip() for t in state_str.split('&')]
    
    tie_states = {}  # {signal_name: "HI" or "LO"}
    
    # Parse each term to extract signal name and tie state
    for term in terms:
        if term.startswith('!'):
            # Negated: !A means A=LOW (because !A=1 requires A=0)
            signal = term[1:]  # Remove '!'
            tie_states[signal] = "LO"
        else:
            # Non-negated: A means A=HIGH (because A=1 requires A=1)
            tie_states[term] = "HI"
    
    # Determine overall tie strategy
    lo_count = sum(1 for v in tie_states.values() if v == "LO")
    total_signals = len(tie_states)
    
    if lo_count == total_signals:
        # All inputs tied LOW
        summary = "LO"
    elif lo_count == 0:
        # All inputs tied HIGH
        summary = "HI"
    else:
        # Mixed tie states
        summary = "MIXED"
    
    return summary, tie_states


def get_optimal_tie_for_cell(cell_type: str, leakage_db: Dict) -> str:
    """
    Get optimal tie state for a specific cell type.
    
    Args:
        cell_type: Full cell type name (e.g., "sky130_fd_sc_hd__and2_2")
        leakage_db: Database from parse_liberty_leakage()
    
    Returns:
        "HI" or "LO" (defaults to "LO" if unknown or MIXED)
    """
    if cell_type in leakage_db:
        optimal = leakage_db[cell_type].get("optimal_tie", "LO")
        if optimal == "MIXED":
            # For mixed states, use heuristic fallback
            return heuristic_tie_selection(cell_type)
        return optimal
    
    # Fallback to heuristic if cell not in database
    return heuristic_tie_selection(cell_type)


def heuristic_tie_selection(cell_type: str) -> str:
    """
    Heuristic-based tie selection when leakage data is unavailable.
    Based on typical CMOS gate behavior.
    """
    cell_lower = cell_type.lower()
    
    # AND/NAND gates
    if any(g in cell_lower for g in ["and2", "and3", "and4"]):
        return "HI"  # AND gates: min leakage at all HIGH
    if any(g in cell_lower for g in ["nand2", "nand3", "nand4"]):
        return "LO"  # NAND gates: min leakage at all LOW
    
    # OR/NOR gates
    if any(g in cell_lower for g in ["or2", "or3", "or4"]):
        return "LO"  # OR gates: min leakage at all LOW
    if any(g in cell_lower for g in ["nor2", "nor3", "nor4"]):
        return "HI"  # NOR gates: min leakage at all HIGH
    
    # Complex gates (AOI, OAI, etc.)
    if "aoi" in cell_lower or "o" in cell_lower and "a" in cell_lower:
        return "LO"  # AND-OR-INVERT: typically better with LOW
    if "oai" in cell_lower or "a" in cell_lower and "o" in cell_lower:
        return "HI"  # OR-AND-INVERT: typically better with HIGH
    
    # XOR/XNOR
    if "xor" in cell_lower or "xnor" in cell_lower:
        return "LO"
    
    # Inverters/Buffers
    if any(g in cell_lower for g in ["inv", "buf", "clkbuf"]):
        return "LO"
    
    # Default: LOW (conservative)
    return "LO"


def generate_leakage_report(leakage_db: Dict, 
                           cells_to_optimize: List[str] = None) -> str:
    """
    Generate human-readable report of leakage analysis with per-input tie states.
    
    Args:
        leakage_db: Database from parse_liberty_leakage()
        cells_to_optimize: Optional list of specific cells to report on
    """
    report = []
    report.append("=" * 100)
    report.append("LEAKAGE POWER ANALYSIS REPORT - DETAILED INPUT TIE STATES")
    report.append("=" * 100)
    report.append("")
    
    if cells_to_optimize:
        report.append(f"Analyzing {len(cells_to_optimize)} cell types from ECO...")
        cells_to_report = [c for c in cells_to_optimize if c in leakage_db]
    else:
        cells_to_report = sorted(leakage_db.keys())
    
    report.append("")
    report.append(f"{'Cell Type':<40} {'Tie':<8} {'Input States':<35}")
    report.append("-" * 100)
    
    total_savings = 0.0
    cells_with_savings = 0
    
    for cell_type in cells_to_report:
        data = leakage_db[cell_type]
        optimal_tie = data.get("optimal_tie", "?")
        input_ties = data.get("input_ties", {})
        min_power = data.get("min_power", 0)
        avg_power = data.get("avg_power", 0)
        
        # Format input ties as readable string
        if input_ties:
            # Sort by signal name for consistent output
            tie_strs = [f"{sig}={tie}" for sig, tie in sorted(input_ties.items())]
            input_str = ", ".join(tie_strs)
        else:
            input_str = ""
        
        if avg_power > 0:
            savings_pct = ((avg_power - min_power) / avg_power) * 100
            if savings_pct > 1.0:  # Only count meaningful savings
                total_savings += savings_pct
                cells_with_savings += 1
        else:
            savings_pct = 0
        
        # Format cell name (truncate if too long)
        cell_short = cell_type[-38:] if len(cell_type) > 40 else cell_type
        
        report.append(
            f"{cell_short:<40} {optimal_tie:<8} {input_str:<35}"
        )
    
    report.append("-" * 100)
    
    if cells_with_savings > 0:
        avg_savings = total_savings / cells_with_savings
        report.append(f"Average power savings from optimal tying: {avg_savings:.1f}%")
    
    report.append("")
    report.append("=" * 80)
    
    return "\n".join(report)


def export_tie_database(leakage_db: Dict, output_file: str):
    """
    Export simplified tie recommendation database for ECO tool.
    
    Format: YAML
    {
        cell_type: {
            "tie": "HI" or "LO",
            "min_leakage": float,
            "savings_pct": float
        }
    }
    """
    import yaml
    import os
    
    tie_db = {}
    for cell_type, data in leakage_db.items():
        if "optimal_tie" in data and data["optimal_tie"] != "MIXED":
            avg_power = data.get("avg_power", data.get("min_power", 0))
            min_power = data.get("min_power", 0)
            
            savings = 0
            if avg_power > 0:
                savings = ((avg_power - min_power) / avg_power) * 100
            
            tie_db[cell_type] = {
                "tie": data["optimal_tie"],
                "min_leakage_uw": min_power,
                "savings_pct": round(savings, 2)
            }
    
    with open(output_file, 'w') as f:
        yaml.dump(tie_db, f, default_flow_style=False, sort_keys=True)
    
    print(f"Exported tie database to: {output_file}")
    print(f"  {len(tie_db)} cell types with optimal tie recommendations")


# ============================================================================
# Integration with power_down_eco.py
# ============================================================================

def load_tie_database(tie_db_file: str) -> Dict[str, str]:
    """
    Load pre-computed tie database for fast lookup.
    
    Returns:
        {cell_type: "HI" or "LO"}
    """
    import yaml
    
    with open(tie_db_file, 'r') as f:
        tie_db = yaml.safe_load(f)
    
    # Simplify to just cell_type -> tie mapping
    return {cell: data["tie"] for cell, data in tie_db.items()}


# ============================================================================
# Command-line interface
# ============================================================================

if __name__ == "__main__":
    import sys
    
    # Hardcoded Liberty file path
    liberty_file = "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
    
    # Allow optional output file override
    if len(sys.argv) > 1:
        output_file = sys.argv[1]
    else:
        output_file = "tie_db.yaml"
    
    # Check if Liberty file exists
    if not os.path.exists(liberty_file):
        print(f"Error: Liberty file not found at: {liberty_file}")
        print()
        print("Expected location: tech/sky130_fd_sc_hd__tt_025C_1v80.lib")
        print()
        print("Please ensure the Liberty timing library is in the 'tech' folder.")
        sys.exit(1)
    
    print("Parsing Liberty file...")
    print(f"  Location: {liberty_file}")
    leakage_db = parse_liberty_leakage(liberty_file, verbose=True)
    
    print()
    print(generate_leakage_report(leakage_db))
    
    print()
    print("Exporting tie database...")
    export_tie_database(leakage_db, output_file)
    print()
    print("[OK] Successfully created tie database: " + output_file)
    print()
    print("=" * 80)
    print("USAGE IN ECO:")
    print("=" * 80)
    print("Option 1 - Direct Liberty integration:")
    print(f"  python power_down_eco.py design.json fabric_cells.yaml pins.yaml \\")
    print(f"         fabric.yaml placement.map --liberty {liberty_file}")
    print()
    print("Option 2 - Use pre-computed tie database:")
    print(f"  from liberty_leakage_parser import load_tie_database")
    print(f"  tie_db = load_tie_database('{output_file}')")
    print(f"  optimal_tie = tie_db.get(cell_type, 'LO')")
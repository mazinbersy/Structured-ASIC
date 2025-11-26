"""
power_down_eco.py
-----------------
Power-Down ECO: Ties unused logic cell inputs to low (GND) using a sky130_fd_sc_hd__conb_1 cell.

Process:
1. Parse placement.map to identify UNUSED cells
2. Claim one sky130_fd_sc_hd__conb_1 cell for tie-low generation
3. Modify design_mapped.json to connect all unused cell inputs to conb_1 LO output
4. Write updated netlist with power-down ECO applied

Input:
  - placement.map: Fabric placement mapping (fabric_cell -> logical_cell or UNUSED)
  - design_mapped.json: Original logical netlist

Output:
  - design_mapped_eco.json: Modified netlist with unused inputs tied low
  - eco_report.txt: Summary of ECO changes
"""

import json
import copy
from typing import Dict, List, Set, Tuple
from collections import defaultdict


# ===============================================================
# 1. Cell Pin Database (Sky130 Standard Cells)
# ===============================================================

CELL_PIN_INFO = {
    # Logic gates
    "sky130_fd_sc_hd__nand2_1": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand2_2": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand2_4": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand3_1": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand3_2": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand3_4": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand4_1": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand4_2": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nand4_4": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor2_1": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor2_2": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor2_4": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor3_1": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor3_2": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor3_4": {"inputs": ["A", "B", "C"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor4_1": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor4_2": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__nor4_4": {"inputs": ["A", "B", "C", "D"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__and2_1": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and2_2": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and2_4": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and3_1": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and3_2": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and3_4": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and4_1": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and4_2": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__and4_4": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or2_1": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or2_2": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or2_4": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or3_1": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or3_2": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or3_4": {"inputs": ["A", "B", "C"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or4_1": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or4_2": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__or4_4": {"inputs": ["A", "B", "C", "D"], "outputs": ["X"]},
    "sky130_fd_sc_hd__xor2_1": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__xor2_2": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__xor2_4": {"inputs": ["A", "B"], "outputs": ["X"]},
    "sky130_fd_sc_hd__xnor2_1": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__xnor2_2": {"inputs": ["A", "B"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__xnor2_4": {"inputs": ["A", "B"], "outputs": ["Y"]},
    
    # Inverters and buffers
    "sky130_fd_sc_hd__inv_1": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__inv_2": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__inv_4": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__inv_8": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__clkinv_1": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__clkinv_2": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__clkinv_4": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__clkinv_8": {"inputs": ["A"], "outputs": ["Y"]},
    "sky130_fd_sc_hd__buf_1": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__buf_2": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__buf_4": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__buf_8": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__clkbuf_1": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__clkbuf_2": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__clkbuf_4": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__clkbuf_8": {"inputs": ["A"], "outputs": ["X"]},
    "sky130_fd_sc_hd__clkbuf_16": {"inputs": ["A"], "outputs": ["X"]},
    
    # Multiplexers
    "sky130_fd_sc_hd__mux2_1": {"inputs": ["A0", "A1", "S"], "outputs": ["X"]},
    "sky130_fd_sc_hd__mux2_2": {"inputs": ["A0", "A1", "S"], "outputs": ["X"]},
    "sky130_fd_sc_hd__mux2_4": {"inputs": ["A0", "A1", "S"], "outputs": ["X"]},
    "sky130_fd_sc_hd__mux4_1": {"inputs": ["A0", "A1", "A2", "A3", "S0", "S1"], "outputs": ["X"]},
    "sky130_fd_sc_hd__mux4_2": {"inputs": ["A0", "A1", "A2", "A3", "S0", "S1"], "outputs": ["X"]},
    "sky130_fd_sc_hd__mux4_4": {"inputs": ["A0", "A1", "A2", "A3", "S0", "S1"], "outputs": ["X"]},
    
    # Flip-flops
    "sky130_fd_sc_hd__dfxtp_1": {"inputs": ["CLK", "D"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfxtp_2": {"inputs": ["CLK", "D"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfxtp_4": {"inputs": ["CLK", "D"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfrtp_1": {"inputs": ["CLK", "D", "RESET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfrtp_2": {"inputs": ["CLK", "D", "RESET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfrtp_4": {"inputs": ["CLK", "D", "RESET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfstp_1": {"inputs": ["CLK", "D", "SET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfstp_2": {"inputs": ["CLK", "D", "SET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfstp_4": {"inputs": ["CLK", "D", "SET_B"], "outputs": ["Q"]},
    "sky130_fd_sc_hd__dfbbp_1": {"inputs": ["CLK", "D", "RESET_B", "SET_B"], "outputs": ["Q", "Q_N"]},
    
    # Constant generator (tie cell)
    "sky130_fd_sc_hd__conb_1": {"inputs": [], "outputs": ["HI", "LO"]},
    
    # Infrastructure
    "sky130_fd_sc_hd__tapvpwrvgnd_1": {"inputs": [], "outputs": []},
    "sky130_fd_sc_hd__decap_4": {"inputs": [], "outputs": []},
    "sky130_fd_sc_hd__decap_8": {"inputs": [], "outputs": []},
    "sky130_fd_sc_hd__fill_1": {"inputs": [], "outputs": []},
    "sky130_fd_sc_hd__fill_2": {"inputs": [], "outputs": []},
}


def get_cell_inputs(cell_type: str) -> List[str]:
    """Get list of input pins for a cell type."""
    if cell_type in CELL_PIN_INFO:
        return CELL_PIN_INFO[cell_type]["inputs"]
    
    # Pattern matching for unknown cell types
    # Extract base cell name without drive strength suffix
    base_name = cell_type.rsplit('_', 1)[0] if '_' in cell_type else cell_type
    
    # Try to find a similar cell in the database
    for known_cell, info in CELL_PIN_INFO.items():
        if known_cell.startswith(base_name):
            print(f"Info: Using {known_cell} pin definition for {cell_type}")
            return info["inputs"]
    
    # Common patterns
    if "nand" in cell_type:
        # Extract number of inputs from name (e.g., nand3 -> 3 inputs)
        if "nand2" in cell_type:
            return ["A", "B"]
        elif "nand3" in cell_type:
            return ["A", "B", "C"]
        elif "nand4" in cell_type:
            return ["A", "B", "C", "D"]
    elif "nor" in cell_type:
        if "nor2" in cell_type:
            return ["A", "B"]
        elif "nor3" in cell_type:
            return ["A", "B", "C"]
        elif "nor4" in cell_type:
            return ["A", "B", "C", "D"]
    elif "and" in cell_type:
        if "and2" in cell_type:
            return ["A", "B"]
        elif "and3" in cell_type:
            return ["A", "B", "C"]
        elif "and4" in cell_type:
            return ["A", "B", "C", "D"]
    elif "or" in cell_type and "nor" not in cell_type and "xor" not in cell_type:
        if "or2" in cell_type:
            return ["A", "B"]
        elif "or3" in cell_type:
            return ["A", "B", "C"]
        elif "or4" in cell_type:
            return ["A", "B", "C", "D"]
    elif "inv" in cell_type or "clkinv" in cell_type:
        return ["A"]
    elif "buf" in cell_type or "clkbuf" in cell_type:
        return ["A"]
    elif "mux2" in cell_type:
        return ["A0", "A1", "S"]
    elif "mux4" in cell_type:
        return ["A0", "A1", "A2", "A3", "S0", "S1"]
    elif "dfxtp" in cell_type or "dffp" in cell_type:
        return ["CLK", "D"]
    elif "dfrtp" in cell_type:
        return ["CLK", "D", "RESET_B"]
    elif "dfstp" in cell_type:
        return ["CLK", "D", "SET_B"]
    elif "dfbbp" in cell_type:
        return ["CLK", "D", "RESET_B", "SET_B"]
    
    print(f"Warning: Unknown cell type '{cell_type}', assuming no inputs")
    return []


def get_cell_outputs(cell_type: str) -> List[str]:
    """Get list of output pins for a cell type."""
    if cell_type in CELL_PIN_INFO:
        return CELL_PIN_INFO[cell_type]["outputs"]
    
    # Pattern matching for unknown cell types
    base_name = cell_type.rsplit('_', 1)[0] if '_' in cell_type else cell_type
    
    # Try to find a similar cell in the database
    for known_cell, info in CELL_PIN_INFO.items():
        if known_cell.startswith(base_name):
            return info["outputs"]
    
    # Common patterns
    if any(x in cell_type for x in ["nand", "nor", "xnor", "inv", "clkinv"]):
        return ["Y"]
    elif any(x in cell_type for x in ["and", "or", "xor", "buf", "clkbuf", "mux"]):
        return ["X"]
    elif "df" in cell_type:  # Flip-flops
        if "dfbbp" in cell_type:
            return ["Q", "Q_N"]
        return ["Q"]
    
    print(f"Warning: Unknown cell type '{cell_type}', assuming no outputs")
    return []


# ===============================================================
# 2. Parse Placement Map
# ===============================================================

def parse_placement_map(filename: str) -> Tuple[Dict[str, Tuple[str, str]], List[Tuple[str, str]], List[str]]:
    """
    Parse placement.map file to extract:
    - placement_dict: {fabric_cell: (cell_type, logical_cell)}
    - unused_logic_fabric: List of (fabric_cell, cell_type) for UNUSED logic gates
    - conb_cells: List of sky130_fd_sc_hd__conb_1 fabric cells
    
    Returns:
        (placement_dict, unused_logic_fabric, conb_cells)
    """
    placement_dict = {}
    unused_logic_fabric = []
    conb_cells = []
    
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split()
            if len(parts) < 5:
                continue
            
            fabric_cell = parts[0]
            cell_type = parts[1]
            # x = parts[2]
            # y = parts[3]
            
            # Handle both formats: "-> UNUSED" or just "UNUSED"
            if "->" in line:
                logical_cell = parts[5] if len(parts) >= 6 else "UNUSED"
            else:
                logical_cell = parts[4] if len(parts) >= 5 else "UNUSED"
            
            placement_dict[fabric_cell] = (cell_type, logical_cell)
            
            # Track unused LOGIC cells (not infrastructure)
            if logical_cell == "UNUSED":
                # Check if this is a real logic gate (not TAP, DECAP, FILL)
                if ("tap" not in cell_type.lower() and 
                    "decap" not in cell_type.lower() and 
                    "fill" not in cell_type.lower() and
                    "conb" not in cell_type.lower()):
                    unused_logic_fabric.append((fabric_cell, cell_type))
            
            # Track conb_1 cells
            if cell_type == "sky130_fd_sc_hd__conb_1":
                conb_cells.append(fabric_cell)
    
    return placement_dict, unused_logic_fabric, conb_cells


# ===============================================================
# 3. Identify Unused Logic Cells
# ===============================================================

def identify_unused_fabric_cells_for_eco(placement_dict: Dict[str, Tuple[str, str]], 
                                          unused_fabric_cells: List[Tuple[str, str]],
                                          design: Dict) -> Dict[str, List[str]]:
    """
    Create synthetic logical cells for unused fabric cells that need power-down.
    
    For each unused fabric cell with inputs, we'll create a virtual logical cell
    in the design that we can then tie to ground.
    
    Returns:
        Dictionary with:
        - 'fabric_cells': List of (fabric_cell, cell_type) tuples to power down
        - 'synthetic_cells': List of synthetic logical cell names created
    """
    cells = design.get("cells", {})
    
    synthetic_cells = []
    fabric_cells_to_power_down = []
    
    # For each unused fabric cell that has inputs, create a synthetic logical cell
    for fabric_cell, cell_type in unused_fabric_cells:
        # Get input pins for this cell type
        input_pins = get_cell_inputs(cell_type)
        
        if not input_pins:
            # No inputs to tie, skip
            continue
        
        # Create a synthetic logical cell name
        synthetic_name = f"$fabric_eco${fabric_cell}"
        
        # Add this cell to the design
        cells[synthetic_name] = {
            "type": cell_type,
            "pins": {}
        }
        
        # Initialize all input pins to 0 (we'll tie them to GND later)
        for pin in input_pins:
            cells[synthetic_name]["pins"][pin] = 0
        
        # Initialize output pins to 0 as well
        output_pins = get_cell_outputs(cell_type)
        for pin in output_pins:
            cells[synthetic_name]["pins"][pin] = 0
        
        synthetic_cells.append(synthetic_name)
        fabric_cells_to_power_down.append((fabric_cell, cell_type))
        
        # Update placement to mark this fabric cell as used
        placement_dict[fabric_cell] = (cell_type, synthetic_name)
    
    print(f"  Created {len(synthetic_cells)} synthetic cells for unused fabric")
    
    return {
        'fabric_cells': fabric_cells_to_power_down,
        'synthetic_cells': synthetic_cells
    }


# ===============================================================
# 4. Claim CONB_1 Cell for Tie-Low
# ===============================================================

def claim_conb_cell(conb_cells: List[str], 
                   placement_dict: Dict[str, Tuple[str, str]],
                   design: Dict) -> Tuple[str, int]:
    """
    Claim one sky130_fd_sc_hd__conb_1 cell from UNUSED slots.
    Returns (fabric_cell_name, lo_net_id) or (None, None) if no conb available.
    
    The LO output will be used as the tie-low net.
    """
    cells = design.get("cells", {})
    
    # Find an unused conb_1 cell
    for fabric_cell in conb_cells:
        # FIX: placement_dict stores tuples (cell_type, logical_cell)
        cell_type, logical_cell = placement_dict.get(fabric_cell, ("", "UNUSED"))
        
        if logical_cell == "UNUSED":
            # This conb_1 is unused - claim it!
            # We need to add it to the design and create a net for LO output
            
            # Create new cell name
            conb_logical_name = f"$eco$tie_low_conb"
            
            # Find next available net ID
            max_net_id = 0
            for cell_name, cell_info in cells.items():
                for pin_name, net_id in cell_info.get("pins", {}).items():
                    if isinstance(net_id, int):
                        max_net_id = max(max_net_id, net_id)
            
            lo_net_id = max_net_id + 1
            hi_net_id = max_net_id + 2
            
            # Add conb_1 to design
            cells[conb_logical_name] = {
                "type": "sky130_fd_sc_hd__conb_1",
                "pins": {
                    "LO": lo_net_id,
                    "HI": hi_net_id
                }
            }
            
            # Update placement to mark this conb as used
            placement_dict[fabric_cell] = (cell_type, conb_logical_name)
            
            print(f"[ECO] Claimed {fabric_cell} as {conb_logical_name}")
            print(f"[ECO] LO net ID: {lo_net_id}, HI net ID: {hi_net_id}")
            
            return (conb_logical_name, lo_net_id)
    
    return (None, None)


# ===============================================================
# 5. Apply Power-Down ECO
# ===============================================================

def apply_power_down_eco(design: Dict, 
                        unused_logic_cells: List[str],
                        lo_net_id: int) -> Dict:
    """
    Modify the design to tie all inputs of unused logic cells to lo_net_id (GND).
    
    Returns:
        eco_changes: Dictionary summarizing changes made
    """
    cells = design.get("cells", {})
    
    eco_changes = {
        "cells_modified": [],
        "pins_tied_low": [],
        "total_pins_changed": 0
    }
    
    for cell_name in unused_logic_cells:
        if cell_name not in cells:
            continue
        
        cell_info = cells[cell_name]
        cell_type = cell_info.get("type", "")
        pins = cell_info.get("pins", {})
        
        # Get input pins for this cell type
        input_pins = get_cell_inputs(cell_type)
        
        if not input_pins:
            continue
        
        # Tie all input pins to lo_net_id
        pins_changed = []
        for pin_name in input_pins:
            if pin_name in pins:
                old_net = pins[pin_name]
                pins[pin_name] = lo_net_id
                pins_changed.append(f"{pin_name}: {old_net} -> {lo_net_id}")
                eco_changes["total_pins_changed"] += 1
        
        if pins_changed:
            eco_changes["cells_modified"].append({
                "cell": cell_name,
                "type": cell_type,
                "pins": pins_changed
            })
            eco_changes["pins_tied_low"].extend(pins_changed)
    
    return eco_changes


# ===============================================================
# 6. Write ECO Report
# ===============================================================

def write_eco_report(filename: str,
                    conb_cell: str,
                    lo_net_id: int,
                    unused_logic_cells: List[str],
                    eco_changes: Dict,
                    fabric_cells: List[Tuple[str, str]] = None):
    """Write a summary report of ECO changes."""
    with open(filename, 'w') as f:
        f.write("="*70 + "\n")
        f.write("POWER-DOWN ECO REPORT\n")
        f.write("="*70 + "\n\n")
        
        f.write(f"Tie-Low Cell:     {conb_cell}\n")
        f.write(f"Tie-Low Net ID:   {lo_net_id}\n")
        f.write(f"Fabric Cells:     {len(fabric_cells) if fabric_cells else 0}\n")
        f.write(f"Synthetic Cells:  {len(unused_logic_cells)}\n")
        f.write(f"Cells Modified:   {len(eco_changes['cells_modified'])}\n")
        f.write(f"Pins Tied Low:    {eco_changes['total_pins_changed']}\n\n")
        
        if fabric_cells:
            f.write("-"*70 + "\n")
            f.write("FABRIC CELLS POWERED DOWN (sample, first 100)\n")
            f.write("-"*70 + "\n")
            for fabric_cell, cell_type in fabric_cells[:100]:
                f.write(f"  {fabric_cell} ({cell_type})\n")
            if len(fabric_cells) > 100:
                f.write(f"  ... and {len(fabric_cells) - 100} more\n")
            f.write("\n")
        
        f.write("-"*70 + "\n")
        f.write("ECO CHANGES (Pin Modifications, sample first 50)\n")
        f.write("-"*70 + "\n")
        for change in eco_changes['cells_modified'][:50]:
            f.write(f"\nCell: {change['cell']} ({change['type']})\n")
            for pin_change in change['pins']:
                f.write(f"  {pin_change}\n")
        
        if len(eco_changes['cells_modified']) > 50:
            f.write(f"\n... and {len(eco_changes['cells_modified']) - 50} more cells\n")
        
        f.write("\n" + "="*70 + "\n")


# ===============================================================
# 7. Main ECO Flow
# ===============================================================

def main():
    """Main Power-Down ECO flow."""
    print("="*70)
    print("POWER-DOWN ECO: Tie Unused Logic Cells to GND")
    print("="*70)
    
    # Input files
    placement_file = "placement_sa_optimized.map"
    design_file = "designs/6502_mapped.json"
    
    # Output files
    output_design_file = "designs/6502_mapped_eco.json"
    report_file = "eco_report.txt"
    
    # Step 1: Parse placement map
    print("\n[1/5] Parsing placement map...")
    placement_dict, unused_fabric_cells, conb_cells = parse_placement_map(placement_file)
    print(f"  Found {len(unused_fabric_cells)} unused fabric cells")
    print(f"  Found {len(conb_cells)} conb_1 cells")
    
    # Step 2: Load design
    print("\n[2/5] Loading design netlist...")
    with open(design_file, 'r') as f:
        design = json.load(f)
    
    # Debug: Check design structure
    print(f"  Design keys: {list(design.keys())}")
    
    # Handle different JSON formats (Yosys vs custom)
    if "modules" in design:
        # Yosys JSON format
        module_name = list(design["modules"].keys())[0]
        print(f"  Found Yosys format, module: {module_name}")
        module_data = design["modules"][module_name]
        cells = module_data.get("cells", {})
        print(f"  Loaded {len(cells)} cells from module")
        # Flatten to top level for easier processing
        design["cells"] = cells
    else:
        cells = design.get("cells", {})
        print(f"  Loaded {len(cells)} cells")
    
    # Step 3: Identify unused logic cells
    print("\n[3/5] Identifying unused fabric cells for power-down...")
    eco_info = identify_unused_fabric_cells_for_eco(placement_dict, unused_fabric_cells, design)
    unused_logic_cells = eco_info['synthetic_cells']
    print(f"  Found {len(unused_logic_cells)} fabric cells to power down")
    
    if not unused_logic_cells:
        print("\n[SKIP] No fabric cells need power-down ECO!")
        return
    
    # Step 4: Claim conb_1 cell
    print("\n[4/5] Claiming conb_1 cell for tie-low...")
    conb_cell, lo_net_id = claim_conb_cell(conb_cells, placement_dict, design)
    
    if conb_cell is None:
        print("\n[ERROR] No available conb_1 cells found!")
        print("        Cannot perform ECO without a tie cell.")
        return
    
    # Step 5: Apply ECO
    print("\n[5/5] Applying power-down ECO...")
    eco_changes = apply_power_down_eco(design, unused_logic_cells, lo_net_id)
    print(f"  Modified {len(eco_changes['cells_modified'])} cells")
    print(f"  Tied {eco_changes['total_pins_changed']} pins to GND")
    
    # Write outputs
    print(f"\n[SAVE] Writing modified netlist to: {output_design_file}")
    with open(output_design_file, 'w') as f:
        json.dump(design, f, indent=2)
    
    print(f"[SAVE] Writing ECO report to: {report_file}")
    write_eco_report(report_file, conb_cell, lo_net_id, unused_logic_cells, eco_changes, eco_info['fabric_cells'])
    
    print("\n" + "="*70)
    print("POWER-DOWN ECO COMPLETE")
    print("="*70)
    print(f"Summary:")
    print(f"  - {len(eco_info['fabric_cells'])} unused fabric cells powered down")
    print(f"  - {eco_changes['total_pins_changed']} input pins tied to GND")
    print(f"  - Tie-low net: {lo_net_id} (from {conb_cell})")
    print(f"\nOutput files:")
    print(f"  - {output_design_file}")
    print(f"  - {report_file}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
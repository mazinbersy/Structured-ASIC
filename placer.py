"""
placer.py
----------
Initial placer for structured ASIC using:
  • fabric_db, logical_db, netlist_graph imported via build functions

Output:
- .map file: lists cell placement per line with format:
  slot_name  cell_type  x y  ->  logical_cell_name
"""

from math import sqrt

# --------------------------------------------------
# 1. Import build functions
# --------------------------------------------------

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json  # returns logical_db, netlist_graph

# --------------------------------------------------
# 2. Utility functions
# --------------------------------------------------

def available_tiles(fabric_db):
    """Return a list of tile IDs that have cells slots."""
    return list(fabric_db["fabric"]["cells_by_tile"].keys())

def get_tile_cells(fabric_db, tile_id):
    return fabric_db["fabric"]["cells_by_tile"][tile_id]["cells"]

def assign_cell_to_nearest_slot(fabric_db, cell_name, target_pos, logical_db):
    """
    Assign a cell to the nearest available slot to the target (x, y) position.
    Returns (slot_name, cell_type, x, y).
    """
    best_tile = None
    best_cell_slot = None
    best_dist = float('inf')

    # Get the required cell type from logical_db
    # FIXED: Access type correctly from logical_db
    cell_info = logical_db["cells"].get(cell_name, {})
    required_type = cell_info.get("type", "")

    # Debug: Log DFF placement attempts
    if "dfbbp" in required_type.lower() or "dff" in required_type.lower():
        print(f"[DFF_PLACE] Attempting to place DFF: {cell_name}")
        print(f"[DFF_PLACE]   Required type: {required_type}")
        print(f"[DFF_PLACE]   Target position: ({target_pos[0]}, {target_pos[1]})")

    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            if "placed" not in cell:
                # FIXED: Match cell_type from fabric with type from logical
                # Check if the fabric slot's cell_type matches the required type
                slot_cell_type = cell.get("cell_type", "")

                # Only consider slots that match the required cell type
                if slot_cell_type == required_type:
                    dx = cell["x"] - target_pos[0]
                    dy = cell["y"] - target_pos[1]
                    dist = sqrt(dx*dx + dy*dy)
                    if dist < best_dist:
                        best_dist = dist
                        best_tile = tile_id
                        best_cell_slot = cell

    if best_cell_slot is None:
        raise ValueError(f"No free slots available for cell '{cell_name}' of type '{required_type}'")

    best_cell_slot["placed"] = cell_name
    
    # Debug: Log successful DFF placement
    if "dfbbp" in required_type.lower() or "dff" in required_type.lower():
        print(f"[DFF_PLACE] ✓ Successfully placed: {cell_name}")
        print(f"[DFF_PLACE]   Slot: {best_cell_slot['name']} (tile: {best_tile})")
        print(f"[DFF_PLACE]   Position: ({best_cell_slot['x']}, {best_cell_slot['y']})")
        print(f"[DFF_PLACE]   Distance from target: {best_dist:.2f}")
    
    # FIXED: Return cell_type from fabric_db (not type)
    return (best_cell_slot["name"],
            best_cell_slot["cell_type"],
            best_cell_slot["x"],
            best_cell_slot["y"])

def barycenter_position(cell_name, netlist_graph, placement_dict):
    """Compute average position of already placed neighbors."""
    neighbors = list(netlist_graph.neighbors(cell_name))
    placed_neighbors = [n for n in neighbors if n in placement_dict]
    if not placed_neighbors:
        return None
    x = sum(placement_dict[n][2] for n in placed_neighbors) / len(placed_neighbors)
    y = sum(placement_dict[n][3] for n in placed_neighbors) / len(placed_neighbors)
    return x, y

def place_pins(fabric_db, logical_db):
    """Place I/O ports using fixed coordinates from pin_placement."""
    placement = {}
    pin_data = fabric_db["fabric"].get("pin_placement", {}).get("pins", [])
    pin_dict = {p["name"]: p for p in pin_data}

    # FIXED: Handle both input and output ports correctly
    inputs = logical_db.get("ports", {}).get("inputs", {})
    outputs = logical_db.get("ports", {}).get("outputs", {})

    # Combine inputs and outputs - handle both dict and list formats
    all_ports = []
    if isinstance(inputs, dict):
        all_ports.extend(inputs.keys())
    else:
        all_ports.extend(inputs)

    if isinstance(outputs, dict):
        all_ports.extend(outputs.keys())
    else:
        all_ports.extend(outputs)

    for port in all_ports:
        if port not in pin_dict:
            raise ValueError(f"Port {port} not found in pin_placement!")
        p = pin_dict[port]
        # Format: (slot_name, cell_type, x, y)
        placement[port] = (port, "PIN", p["x_um"], p["y_um"])
    return placement

# --------------------------------------------------
# 3. Initial placement algorithm
# --------------------------------------------------

def initial_placement(fabric_db, logical_db, netlist_graph):
    placement = {}

    # ----------- Stage 1: Fixed pin placement ------------
    placement.update(place_pins(fabric_db, logical_db))

    # ------------------------------------------------------
    # Stage 2: SEED — place all cells connected directly to pins
    # ------------------------------------------------------
    # Find cells with neighbors that are pins
    pin_nodes = set(placement.keys())   # in1, in2, out1, etc.
    seed_cells = []

    # FIXED: Iterate over cell names correctly
    for cell in logical_db["cells"].keys():
        neighbors = set(netlist_graph.neighbors(cell))
        if neighbors & pin_nodes:       # if any neighbor is a pin
            seed_cells.append(cell)

    # Place the seed cells first
    for cell in seed_cells:
        # barycenter will be just the pin position(s)
        pos = barycenter_position(cell, netlist_graph, placement)
        cell_type_from_logical = logical_db["cells"].get(cell, {}).get("type", "")
        
        # Debug: Log seed cell placement including DFFs
        if "dfbbp" in cell_type_from_logical.lower() or "dff" in cell_type_from_logical.lower():
            print(f"[SEED] Placing DFF in seed stage: {cell} (type: {cell_type_from_logical})")
        
        slot_name, cell_type, x, y = assign_cell_to_nearest_slot(fabric_db, cell, pos, logical_db)
        placement[cell] = (slot_name, cell_type, x, y)

    # Remaining cells
    remaining_cells = set(logical_db["cells"].keys()) - set(seed_cells)
    
    # Debug: Report how many cells and DFFs we have
    dff_count = sum(1 for c in logical_db["cells"].values() if "dfbbp" in c.get("type", "").lower() or "dff" in c.get("type", "").lower())
    print(f"[PLACEMENT] Total cells: {len(logical_db['cells'])}, DFFs: {dff_count}, Seed cells: {len(seed_cells)}, Remaining: {len(remaining_cells)}")

    # ------------------------------------------------------
    # Stage 3: GROW — repeatedly place most-connected cell
    # ------------------------------------------------------
    while remaining_cells:
        # For each unplaced cell, count how many placed neighbors it has
        ranked = []
        for cell in remaining_cells:
            neighbors = list(netlist_graph.neighbors(cell))
            placed_neighbors = [n for n in neighbors if n in placement]
            if placed_neighbors:
                ranked.append((len(placed_neighbors), cell))

        if ranked:
            # Pick the MOST CONNECTED unplaced cell
            ranked.sort(reverse=True)   # highest #placed neighbors first
            _, cell_to_place = ranked[0]

            # Debug: Log growth placement including DFFs
            cell_type_from_logical = logical_db["cells"].get(cell_to_place, {}).get("type", "")
            if "dfbbp" in cell_type_from_logical.lower() or "dff" in cell_type_from_logical.lower():
                placed_neighbors_count = len([n for n in netlist_graph.neighbors(cell_to_place) if n in placement])
                print(f"[GROW] Placing DFF in grow stage: {cell_to_place} (type: {cell_type_from_logical}, connected neighbors: {placed_neighbors_count})")

            # Place using barycenter
            pos = barycenter_position(cell_to_place, netlist_graph, placement)
            slot_name, cell_type, x, y = assign_cell_to_nearest_slot(fabric_db, cell_to_place, pos, logical_db)

            placement[cell_to_place] = (slot_name, cell_type, x, y)
            remaining_cells.remove(cell_to_place)
        else:
            # No neighbors placed, fallback (rare)
            cell_to_place = remaining_cells.pop()
            cell_type_from_logical = logical_db["cells"].get(cell_to_place, {}).get("type", "")
            
            # Debug: Log fallback placement including DFFs
            if "dfbbp" in cell_type_from_logical.lower() or "dff" in cell_type_from_logical.lower():
                print(f"[FALLBACK] Placing DFF with no placed neighbors: {cell_to_place} (type: {cell_type_from_logical})")
            
            slot_name, cell_type, x, y = assign_cell_to_nearest_slot(fabric_db, cell_to_place, (0,0), logical_db)
            placement[cell_to_place] = (slot_name, cell_type, x, y)

    return placement


# --------------------------------------------------
# 4. HPWL Calculation
# --------------------------------------------------

def calculate_hpwl(netlist_graph, placement_dict, logical_db):
    """Calculate Half-Perimeter Wire Length (HPWL) for the placement."""
    total_hpwl = 0.0

    for net_id, net_info in logical_db["nets"].items():
        connections = net_info.get("connections", [])

        # Get all nodes connected to this net
        nodes = [node_name for node_name, pin_name in connections]

        # Filter to only placed nodes
        placed_nodes = [n for n in nodes if n in placement_dict]

        if len(placed_nodes) < 2:
            continue

        # Get positions (x, y are at indices 2, 3)
        positions = [(placement_dict[n][2], placement_dict[n][3]) for n in placed_nodes]
        x_coords = [pos[0] for pos in positions]
        y_coords = [pos[1] for pos in positions]

        # HPWL = bounding box half-perimeter
        hpwl = (max(x_coords) - min(x_coords)) + (max(y_coords) - min(y_coords))
        total_hpwl += hpwl

    return total_hpwl


# --------------------------------------------------
# 5. Write .map file
# --------------------------------------------------

def write_map_file(placement_dict, fabric_db, filename="placement.map"):
    """
    Write a .map file with format:
    slot_name  cell_type  x y  ->  logical_cell_name

    For pins: pin_name  x y
    For cells: slot_name  cell_type  x y  ->  logical_cell_name
    For unused slots: slot_name  cell_type  x y  ->  UNUSED
    """
    with open(filename, "w") as f:
        # First write all pins (sorted for consistency)
        pin_entries = [(name, data) for name, data in placement_dict.items()
                      if data[1] == "PIN"]
        pin_entries.sort(key=lambda x: x[0])

        for name, (slot_name, cell_type, x, y) in pin_entries:
            f.write(f"{name}  {x:.2f}  {y:.2f}\n")

        # Then write all placed cells
        cell_entries = [(name, data) for name, data in placement_dict.items()
                       if data[1] != "PIN"]
        cell_entries.sort(key=lambda x: x[1][0])  # Sort by slot name

        for name, (slot_name, cell_type, x, y) in cell_entries:
            f.write(f"{slot_name}  {cell_type}  {x:.2f}  {y:.2f}  ->  {name}\n")

# --------------------------------------------------
# 6. Main runner
# --------------------------------------------------

if __name__ == "__main__":
    import sys
    
    # Parse command-line arguments
    design = sys.argv[1] if len(sys.argv) > 1 else "6502"
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"build/{design}/debug_placement.map"
    
    fabric_cells = sys.argv[3] if len(sys.argv) > 3 else "fabric/fabric_cells.yaml"
    fabric_pins = sys.argv[4] if len(sys.argv) > 4 else "fabric/pins.yaml"
    fabric_def = sys.argv[5] if len(sys.argv) > 5 else "fabric/fabric.yaml"
    
    if design in ['-h', '--help']:
        print("Usage: python placer.py [design] [output_file] [fabric_cells] [fabric_pins] [fabric_def]")
        print("\nDefaults:")
        print("  design:        6502")
        print("  output_file:   build/{design}/debug_placement.map")
        print("  fabric_cells:  fabric/fabric_cells.yaml")
        print("  fabric_pins:   fabric/pins.yaml")
        print("  fabric_def:    fabric/fabric.yaml")
        print("\nExample:")
        print("  python placer.py aes_128 build/aes_128/placement.map")
        sys.exit(0)
    
    print(f"Design: {design}")
    print(f"Output file: {output_file}")
    
    # Build data structures
    fabric_db = build_fabric_db(fabric_cells, fabric_pins, fabric_def)
    logical_db, netlist_graph = parse_design_json(f"designs/{design}_mapped.json")

    placement_dict = initial_placement(fabric_db, logical_db, netlist_graph)

    # Calculate and print HPWL
    hpwl = calculate_hpwl(netlist_graph, placement_dict, logical_db)
    print(f"\n{'='*50}")
    print(f"HPWL (Half-Perimeter Wire Length): {hpwl:.2f} µm")
    print(f"{'='*50}\n")

    # Write .map
    write_map_file(placement_dict, fabric_db, filename=output_file)
    print(f"[OK] Placement saved to: {output_file}")
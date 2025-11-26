"""
placer.py
----------
Initial placer for structured ASIC using:
  • fabric_db, logical_db, netlist_graph imported via build functions

Output:
- .map file: fabric-centric format listing all fabric cells
  Format: fabric_cell_name  fabric_type  x  y  ->  logical_cell_name or UNUSED
"""

from math import sqrt

# --------------------------------------------------
# 1. Import build functions
# --------------------------------------------------

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json  # returns logical_db, netlist_graph

# --------------------------------------------------
# 2. Global mapping
# --------------------------------------------------

# Maps fabric slot name -> logical cell name (or "UNUSED")
fabric_to_logical_map = {}

# --------------------------------------------------
# 3. Utility functions
# --------------------------------------------------

def available_tiles(fabric_db):
    """Return a list of tile IDs that have cells slots."""
    return list(fabric_db["fabric"]["cells_by_tile"].keys())

def get_tile_cells(fabric_db, tile_id):
    return fabric_db["fabric"]["cells_by_tile"][tile_id]["cells"]

def assign_cell_to_nearest_slot(fabric_db, cell_name, target_pos):
    """
    Assign a cell to the nearest available slot to the target (x, y) position.
    Returns (fabric_cell_name, x, y).
    """
    best_tile = None
    best_cell_slot = None
    best_dist = float('inf')

    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            if "placed" not in cell:
                dx = cell["x"] - target_pos[0]
                dy = cell["y"] - target_pos[1]
                dist = sqrt(dx*dx + dy*dy)
                if dist < best_dist:
                    best_dist = dist
                    best_tile = tile_id
                    best_cell_slot = cell

    if best_cell_slot is None:
        raise ValueError("No free slots available for cell placement")

    # Mark as placed
    best_cell_slot["placed"] = cell_name
    
    # Store in global mapping
    fabric_cell_name = best_cell_slot["name"]
    fabric_to_logical_map[fabric_cell_name] = cell_name
    
    return fabric_cell_name, best_cell_slot["x"], best_cell_slot["y"]

def barycenter_position(cell_name, netlist_graph, placement_dict):
    """Compute average position of already placed neighbors."""
    neighbors = list(netlist_graph.neighbors(cell_name))
    placed_neighbors = [n for n in neighbors if n in placement_dict]
    if not placed_neighbors:
        return None
    x = sum(placement_dict[n][0] for n in placed_neighbors) / len(placed_neighbors)
    y = sum(placement_dict[n][1] for n in placed_neighbors) / len(placed_neighbors)
    return x, y

def place_pins(fabric_db, logical_db):
    """Place I/O ports using fixed coordinates from pin_placement."""
    placement = {}
    pin_data = fabric_db["fabric"].get("pin_placement", {}).get("pins", [])
    pin_dict = {p["name"]: p for p in pin_data}

    for port in list(logical_db["ports"].get("inputs", {})) + \
                list(logical_db["ports"].get("outputs", {})):
        if port not in pin_dict:
            raise ValueError(f"Port {port} not found in pin_placement!")
        p = pin_dict[port]
        placement[port] = (p["x_um"], p["y_um"])
    return placement

# --------------------------------------------------
# 4. Build complete fabric mapping
# --------------------------------------------------

def build_complete_fabric_map(fabric_db):
    """
    After placement, iterate over ALL fabric cells and record their status.
    Updates fabric_to_logical_map with UNUSED entries.
    """
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for slot in tile_info["cells"]:
            fabric_cell_name = slot["name"]
            if "placed" in slot:
                # Already recorded during placement
                if fabric_cell_name not in fabric_to_logical_map:
                    fabric_to_logical_map[fabric_cell_name] = slot["placed"]
            else:
                # Mark as unused
                fabric_to_logical_map[fabric_cell_name] = "UNUSED"

# --------------------------------------------------
# 5. Initial placement algorithm
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

    for cell in logical_db["cells"]:
        neighbors = set(netlist_graph.neighbors(cell))
        if neighbors & pin_nodes:       # if any neighbor is a pin
            seed_cells.append(cell)

    # Place the seed cells first
    for cell in seed_cells:
        # barycenter will be just the pin position(s)
        pos = barycenter_position(cell, netlist_graph, placement)
        fabric_name, x, y = assign_cell_to_nearest_slot(fabric_db, cell, pos)
        placement[cell] = (x, y)

    # Remaining cells
    remaining_cells = set(logical_db["cells"]) - set(seed_cells)

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

            # Place using barycenter
            pos = barycenter_position(cell_to_place, netlist_graph, placement)
            fabric_name, x, y = assign_cell_to_nearest_slot(fabric_db, cell_to_place, pos)

            placement[cell_to_place] = (x, y)
            remaining_cells.remove(cell_to_place)
        else:
            # No neighbors placed, fallback (rare)
            cell_to_place = remaining_cells.pop()
            fabric_name, x, y = assign_cell_to_nearest_slot(fabric_db, cell_to_place, (0,0))
            placement[cell_to_place] = (x, y)

    return placement


# --------------------------------------------------
# 6. HPWL Calculation
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
        
        # Get positions
        positions = [placement_dict[n] for n in placed_nodes]
        x_coords = [pos[0] for pos in positions]
        y_coords = [pos[1] for pos in positions]
        
        # HPWL = bounding box half-perimeter
        hpwl = (max(x_coords) - min(x_coords)) + (max(y_coords) - min(y_coords))
        total_hpwl += hpwl
    
    return total_hpwl


# --------------------------------------------------
# 7. Write fabric-centric .map file
# --------------------------------------------------

def write_map_file(fabric_db, filename="placement.map"):
    """
    Write fabric-centric .map file:
    Format: fabric_cell_name  fabric_type  x  y  ->  logical_cell_name or UNUSED
    """
    with open(filename, "w") as f:
        for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
            for slot in tile_info["cells"]:
                fabric_name = slot["name"]
                fabric_type = slot["cell_type"]
                x = slot["x"]
                y = slot["y"]
                logical_name = fabric_to_logical_map.get(fabric_name, "UNUSED")
                
                f.write(f"{fabric_name}  {fabric_type}  {x:.2f}  {y:.2f}  ->  {logical_name}\n")
    
    print(f"[OK] Fabric-centric placement written to {filename}")

# --------------------------------------------------
# 8. Main runner
# --------------------------------------------------

if __name__ == "__main__":
    # Build data structures
    fabric_db = build_fabric_db(
        "fabric/fabric_cells.yaml",
        "fabric/pins.yaml",
        "fabric/fabric.yaml"
    )
    logical_db, netlist_graph = parse_design_json(
        "designs/6502_mapped.json"
    )

    # Clear global mapping
    fabric_to_logical_map.clear()
    
    # Run placement
    placement_dict = initial_placement(fabric_db, logical_db, netlist_graph)

    # Build complete fabric mapping (mark unused cells)
    build_complete_fabric_map(fabric_db)
    
    # Calculate and print HPWL
    hpwl = calculate_hpwl(netlist_graph, placement_dict, logical_db)
    print(f"\n{'='*50}")
    print(f"HPWL (Half-Perimeter Wire Length): {hpwl:.2f} µm")
    print(f"{'='*50}\n")

    # Write fabric-centric .map file
    write_map_file(fabric_db)
"""
simulated_annealing.py
----------------------
Simulated Annealing (SA) optimizer for structured ASIC placement.
Takes initial placement from greedy placer and optimizes HPWL.

Key Features:
  • Temperature schedule with geometric cooling
  • Two move types: REFINE (swap) and EXPLORE (shift)
  • Adaptive acceptance based on temperature
  • Range-limiting window (w_initial) for Explore moves
  • Tracks best solution found
  • Fabric-centric output format for CTS/ECO support
"""

import random
import math
import copy
from typing import Dict, Tuple, List

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
from placer import (
    initial_placement, 
    calculate_hpwl, 
    write_map_file,
    fabric_to_logical_map,
    build_complete_fabric_map
)


# ===============================================================
# 1. SA Configuration
# ===============================================================

class SAConfig:
    """Configuration parameters for Simulated Annealing."""
    def __init__(self):
        self.initial_temp = 100.0       # Starting temperature (reduced)
        self.final_temp = 0.01          # Stopping temperature
        self.cooling_rate = 0.97     # Slower cooling for better exploration
        self.moves_per_temp = 100       # More moves per temperature
        self.max_iterations = 15000     # Safety limit
        
        # Move type probabilities
        self.prob_refine = 0.5          # REFINE: Swap two cells (increased)
        self.prob_explore = 0.5         # EXPLORE: Move one cell to new location
        
        # Range-limiting window for Explore moves
        self.w_initial = 0.5          # Initial window size (50% of die width)


# ===============================================================
# 2. Utility Functions
# ===============================================================

def get_available_slots(fabric_db, placement_dict):
    """Returns list of (fabric_name, x, y) tuples that are unoccupied."""
    all_slots = []
    occupied = set(placement_dict.values())
    
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            pos = (cell["x"], cell["y"])
            if pos not in occupied:
                all_slots.append((cell["name"], cell["x"], cell["y"]))
    
    return all_slots


def get_placeable_cells(logical_db):
    """Return list of cell names (exclude ports)."""
    return list(logical_db["cells"].keys())


def is_port(node_name, logical_db):
    """Check if a node is a port (I/O pin)."""
    return (node_name in logical_db["ports"].get("inputs", {}) or 
            node_name in logical_db["ports"].get("outputs", {}))


def get_fabric_dimensions(fabric_db):
    """Calculate the width and height of the fabric die."""
    max_x = max_y = 0
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            max_x = max(max_x, cell["x"])
            max_y = max(max_y, cell["y"])
    return max_x, max_y


def get_fabric_slot_for_cell(fabric_db, cell_name):
    """Find which fabric slot a logical cell is occupying."""
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for slot in tile_info["cells"]:
            if slot.get("placed") == cell_name:
                return slot
    return None


def get_fabric_slot_at_position(fabric_db, x, y):
    """Find the fabric slot at a specific (x, y) position."""
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for slot in tile_info["cells"]:
            if slot["x"] == x and slot["y"] == y:
                return slot
    return None


# ===============================================================
# 3. Move Generation Functions
# ===============================================================

def refine_move(placement_dict, logical_db, fabric_db):
    """
    REFINE: Swap two randomly selected cells.
    Returns (cell1, cell2, slot1, slot2, old_pos1, old_pos2) or None if invalid.
    """
    cells = get_placeable_cells(logical_db)
    if len(cells) < 2:
        return None
    
    cell1, cell2 = random.sample(cells, 2)
    pos1 = placement_dict[cell1]
    pos2 = placement_dict[cell2]
    
    # Find fabric slots for both cells
    slot1 = get_fabric_slot_at_position(fabric_db, pos1[0], pos1[1])
    slot2 = get_fabric_slot_at_position(fabric_db, pos2[0], pos2[1])
    
    if slot1 is None or slot2 is None:
        return None
    
    return (cell1, cell2, slot1, slot2, pos1, pos2)


def explore_move(placement_dict, fabric_db, logical_db, netlist_graph, window_size=None):
    """
    EXPLORE: Move one cell to a nearby available slot (guided by neighbors).
    Returns (cell, old_slot, new_slot, old_pos, new_pos) or None if no slots available.
    
    Args:
        window_size: Maximum distance (in die units) from current position. 
                    If None, no range limiting is applied.
    """
    available = get_available_slots(fabric_db, placement_dict)
    if not available:
        return None
    
    cells = get_placeable_cells(logical_db)
    if not cells:
        return None
    
    cell = random.choice(cells)
    old_pos = placement_dict[cell]
    
    # Get old fabric slot
    old_slot = get_fabric_slot_at_position(fabric_db, old_pos[0], old_pos[1])
    if old_slot is None:
        return None
    
    # Apply range-limiting window if specified
    if window_size is not None:
        # Filter available slots to those within window_size of current position
        available = [
            (name, x, y) for name, x, y in available
            if abs(x - old_pos[0]) <= window_size and abs(y - old_pos[1]) <= window_size
        ]
        
        # If no slots within window, fall back to all available slots
        if not available:
            available = get_available_slots(fabric_db, placement_dict)
    
    # Try to find a slot near this cell's neighbors
    neighbors = list(netlist_graph.neighbors(cell))
    placed_neighbors = [n for n in neighbors if n in placement_dict and not is_port(n, logical_db)]
    
    if placed_neighbors:
        # Calculate center of neighbors
        avg_x = sum(placement_dict[n][0] for n in placed_neighbors) / len(placed_neighbors)
        avg_y = sum(placement_dict[n][1] for n in placed_neighbors) / len(placed_neighbors)
        
        # Find closest available slot to neighbor center
        def distance(slot_info):
            name, x, y = slot_info
            return (x - avg_x)**2 + (y - avg_y)**2
        
        # Pick from top 5 closest slots (some randomness)
        available_sorted = sorted(available, key=distance)
        candidates = available_sorted[:min(5, len(available_sorted))]
        new_fabric_name, new_x, new_y = random.choice(candidates)
    else:
        # No neighbors, pick randomly but close to current position
        def distance(slot_info):
            name, x, y = slot_info
            return (x - old_pos[0])**2 + (y - old_pos[1])**2
        
        available_sorted = sorted(available, key=distance)
        candidates = available_sorted[:min(5, len(available_sorted))]
        new_fabric_name, new_x, new_y = random.choice(candidates)
    
    # Get new fabric slot
    new_slot = get_fabric_slot_at_position(fabric_db, new_x, new_y)
    if new_slot is None:
        return None
    
    new_pos = (new_x, new_y)
    
    return (cell, old_slot, new_slot, old_pos, new_pos)


def generate_move(placement_dict, fabric_db, logical_db, netlist_graph, config, window_size=None):
    """
    Generate a random move based on configured probabilities.
    Returns (move_type, move_data) or (None, None).
    
    Args:
        window_size: Range-limiting window size for Explore moves.
    """
    rand_val = random.random()
    
    if rand_val < config.prob_refine:
        # REFINE: Swap two cells
        move_data = refine_move(placement_dict, logical_db, fabric_db)
        if move_data:
            return ("refine", move_data)
    else:
        # EXPLORE: Shift one cell (with optional window size)
        move_data = explore_move(placement_dict, fabric_db, logical_db, netlist_graph, window_size)
        if move_data:
            return ("explore", move_data)
    
    return (None, None)


# ===============================================================
# 4. Move Application and Reversal (with Fabric Tracking)
# ===============================================================

def apply_move(placement_dict, move_type, move_data):
    """Apply a move to the placement dictionary and fabric slots (in-place)."""
    if move_type == "refine":
        cell1, cell2, slot1, slot2, pos1, pos2 = move_data
        
        # Swap positions in placement_dict
        placement_dict[cell1] = pos2
        placement_dict[cell2] = pos1
        
        # Update fabric slots
        slot1["placed"] = cell2
        slot2["placed"] = cell1
        
        # Update global fabric_to_logical_map
        fabric_to_logical_map[slot1["name"]] = cell2
        fabric_to_logical_map[slot2["name"]] = cell1
        
    elif move_type == "explore":
        cell, old_slot, new_slot, old_pos, new_pos = move_data
        
        # Move cell to new position
        placement_dict[cell] = new_pos
        
        # Update fabric slots
        if "placed" in old_slot:
            del old_slot["placed"]
        new_slot["placed"] = cell
        
        # Update global fabric_to_logical_map
        fabric_to_logical_map[old_slot["name"]] = "UNUSED"
        fabric_to_logical_map[new_slot["name"]] = cell


def revert_move(placement_dict, move_type, move_data):
    """Revert a move from the placement dictionary and fabric slots (in-place)."""
    if move_type == "refine":
        # Swap back
        cell1, cell2, slot1, slot2, pos1, pos2 = move_data
        
        placement_dict[cell1] = pos1
        placement_dict[cell2] = pos2
        
        # Revert fabric slots
        slot1["placed"] = cell1
        slot2["placed"] = cell2
        
        # Revert global fabric_to_logical_map
        fabric_to_logical_map[slot1["name"]] = cell1
        fabric_to_logical_map[slot2["name"]] = cell2
        
    elif move_type == "explore":
        # Move back to old position
        cell, old_slot, new_slot, old_pos, new_pos = move_data
        
        placement_dict[cell] = old_pos
        
        # Revert fabric slots
        if "placed" in new_slot:
            del new_slot["placed"]
        old_slot["placed"] = cell
        
        # Revert global fabric_to_logical_map
        fabric_to_logical_map[old_slot["name"]] = cell
        fabric_to_logical_map[new_slot["name"]] = "UNUSED"


# ===============================================================
# 5. Acceptance Criterion
# ===============================================================

def accept_move(delta_cost, temperature):
    """
    Metropolis acceptance criterion.
    Always accept if cost improves (delta < 0).
    Accept worse solutions with probability exp(-delta/T).
    """
    if delta_cost < 0:
        return True
    
    if temperature <= 0:
        return False
    
    probability = math.exp(-delta_cost / temperature)
    return random.random() < probability


# ===============================================================
# 6. Main Simulated Annealing Algorithm
# ===============================================================

def simulated_annealing(fabric_db, logical_db, netlist_graph, initial_placement_dict, config=None):
    """
    Optimize placement using Simulated Annealing.
    
    Args:
        fabric_db: Fabric database
        logical_db: Logical netlist database
        netlist_graph: NetworkX graph of connectivity
        initial_placement_dict: Starting placement from greedy placer
        config: SAConfig object (optional)
    
    Returns:
        best_placement_dict: Optimized placement
        stats: Dictionary of optimization statistics
    """
    if config is None:
        config = SAConfig()
    
    # Initialize with greedy placement
    current_placement = copy.deepcopy(initial_placement_dict)
    best_placement = copy.deepcopy(initial_placement_dict)
    
    # Calculate initial cost
    current_cost = calculate_hpwl(netlist_graph, current_placement, logical_db)
    best_cost = current_cost
    
    # Get fabric dimensions for window sizing
    die_width, die_height = get_fabric_dimensions(fabric_db)
    initial_window = config.w_initial * max(die_width, die_height)
    
    # Statistics tracking
    stats = {
        "iterations": 0,
        "accepted_moves": 0,
        "rejected_moves": 0,
        "refine_moves": 0,
        "explore_moves": 0,
        "improvements": 0,
        "initial_cost": current_cost,
        "best_cost": best_cost,
        "temperature_history": [],
        "cost_history": []
    }
    
    # Simulated Annealing loop
    temperature = config.initial_temp
    iteration = 0
    no_improvement_count = 0
    last_best_cost = best_cost
    
    while temperature > config.final_temp and iteration < config.max_iterations:
        accepted_at_temp = 0
        
        # Calculate current window size based on temperature
        # Window shrinks as temperature decreases (from w_initial to 0)
        temp_ratio = (temperature - config.final_temp) / (config.initial_temp - config.final_temp)
        current_window = initial_window * temp_ratio
        
        for _ in range(config.moves_per_temp):
            iteration += 1
            
            # Generate a move with current window size
            move_type, move_data = generate_move(
                current_placement, fabric_db, logical_db, netlist_graph, config, current_window
            )
            
            if move_type is None:
                stats["rejected_moves"] += 1
                continue
            
            # Track move type
            if move_type == "refine":
                stats["refine_moves"] += 1
            elif move_type == "explore":
                stats["explore_moves"] += 1
            
            # Apply the move
            apply_move(current_placement, move_type, move_data)
            
            # Calculate new cost
            new_cost = calculate_hpwl(netlist_graph, current_placement, logical_db)
            delta_cost = new_cost - current_cost
            
            # Decide whether to accept
            if accept_move(delta_cost, temperature):
                # Accept the move
                current_cost = new_cost
                stats["accepted_moves"] += 1
                accepted_at_temp += 1
                
                # Track improvements
                if delta_cost < 0:
                    stats["improvements"] += 1
                
                # Update best solution if improved
                if new_cost < best_cost:
                    best_cost = new_cost
                    best_placement = copy.deepcopy(current_placement)
            else:
                # Reject the move - revert
                revert_move(current_placement, move_type, move_data)
                stats["rejected_moves"] += 1
        
        # Record statistics
        stats["temperature_history"].append(temperature)
        stats["cost_history"].append(current_cost)
        stats["iterations"] = iteration
        
        # Check for improvement
        if best_cost < last_best_cost:
            no_improvement_count = 0
            last_best_cost = best_cost
        else:
            no_improvement_count += 1
        
        # If stuck at bad solution, reheat
        if current_cost > best_cost * 1.5 and no_improvement_count > 20:
            print(f"  [REHEAT] Current solution degraded too much, returning to best and reheating")
            current_placement = copy.deepcopy(best_placement)
            current_cost = best_cost
            temperature = min(temperature * 5.0, config.initial_temp * 0.5)
            no_improvement_count = 0
        
        # Progress update every 10 temperature steps
        if len(stats["temperature_history"]) % 10 == 0:
            acceptance_rate = accepted_at_temp / config.moves_per_temp * 100
            print(f"  T={temperature:7.2f} | Window: {current_window:6.1f} | Current HPWL: {current_cost:8.2f} | Best: {best_cost:8.2f} | Accept: {acceptance_rate:5.1f}%")
        
        # Cool down
        temperature *= config.cooling_rate
    
    # Final statistics
    stats["best_cost"] = best_cost
    improvement = ((stats["initial_cost"] - best_cost) / stats["initial_cost"]) * 100
    
    print(f"\n{'='*60}")
    print(f"OPTIMIZATION COMPLETE")
    print(f"{'='*60}")
    print(f"Initial HPWL:     {stats['initial_cost']:.2f} µm")
    print(f"Final HPWL:       {best_cost:.2f} µm")
    print(f"Improvement:      {improvement:.2f}%")
    print(f"Total Iterations: {iteration}")
    print(f"Accepted Moves:   {stats['accepted_moves']} ({stats['accepted_moves']/iteration*100:.1f}%)")
    print(f"Rejected Moves:   {stats['rejected_moves']} ({stats['rejected_moves']/iteration*100:.1f}%)")
    print(f"REFINE moves:     {stats['refine_moves']}")
    print(f"EXPLORE moves:    {stats['explore_moves']}")
    print(f"Improvements:     {stats['improvements']}")
    print(f"{'='*60}\n")
    
    return best_placement, stats


# ===============================================================
# 7. Main Runner
# ===============================================================

if __name__ == "__main__":
    print("Loading fabric and design data...")
    
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
    
    print("Running initial greedy placement...")
    initial_placement_dict = initial_placement(fabric_db, logical_db, netlist_graph)
    
    # Build complete fabric mapping for initial placement
    build_complete_fabric_map(fabric_db)
    
    # Calculate initial HPWL
    initial_hpwl = calculate_hpwl(netlist_graph, initial_placement_dict, logical_db)
    print(f"Greedy Placement HPWL: {initial_hpwl:.2f} µm")
    
    # Write initial placement BEFORE optimization (while fabric_db is still clean)
    write_map_file(fabric_db, filename="placement_greedy_initial.map")
    print(f"[OK] Greedy placement saved to: placement_greedy_initial.map")
    
    # Configure SA (uses defaults from SAConfig class)
    config = SAConfig()
    
    # Run Simulated Annealing
    optimized_placement, stats = simulated_annealing(
        fabric_db, 
        logical_db, 
        netlist_graph, 
        initial_placement_dict,
        config
    )
    
    # Rebuild complete fabric mapping for optimized placement
    build_complete_fabric_map(fabric_db)
    
    # Write optimized placement (fabric-centric format)
    write_map_file(fabric_db, filename="placement_sa_optimized.map")
    print(f"[OK] SA-optimized placement saved to: placement_sa_optimized.map")
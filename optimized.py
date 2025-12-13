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
  • Follows placer.py format conventions
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
    write_map_file
)


# ===============================================================
# 1. SA Configuration
# ===============================================================

class SAConfig:
    """Configuration parameters for Simulated Annealing."""
    def __init__(self):
        self.initial_temp = 1000.0       # Starting temperature
        self.final_temp = 0.01          # Stopping temperature
        self.cooling_rate = 0.97        # Slower cooling for better exploration
        self.moves_per_temp = 4000       # More moves per temperature
        self.max_iterations = 15000     # Safety limit
        
        # Move type probabilities
        self.prob_refine = 0.5          # REFINE: Swap two cells
        self.prob_explore = 0.5         # EXPLORE: Move one cell to new location
        
        # Range-limiting window for Explore moves
        self.w_initial = 0.5            # Initial window size (50% of die width)


# ===============================================================
# 2. Utility Functions
# ===============================================================

def get_available_slots(fabric_db, placement_dict):
    """
    Returns list of (slot_name, cell_type, x, y) tuples that are unoccupied.
    
    Args:
        fabric_db: Fabric database
        placement_dict: Current placement {cell_name: (slot_name, cell_type, x, y)}
    """
    all_slots = []
    # Get all occupied positions
    occupied_positions = set((data[2], data[3]) for data in placement_dict.values())
    
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            pos = (cell["x"], cell["y"])
            if pos not in occupied_positions:
                all_slots.append((cell["name"], cell["cell_type"], cell["x"], cell["y"]))
    
    return all_slots


def get_placeable_cells(logical_db, placement_dict):
    """Return list of cell names (exclude ports)."""
    cells = []
    for cell_name in logical_db["cells"].keys():
        # Check if it's not a port (ports have cell_type "PIN")
        if placement_dict[cell_name][1] != "PIN":
            cells.append(cell_name)
    return cells


def is_port(node_name, placement_dict):
    """Check if a node is a port (I/O pin)."""
    if node_name not in placement_dict:
        return False
    return placement_dict[node_name][1] == "PIN"


def get_fabric_dimensions(fabric_db):
    """Calculate the width and height of the fabric die."""
    max_x = max_y = 0
    for tile_id, tile_info in fabric_db["fabric"]["cells_by_tile"].items():
        for cell in tile_info["cells"]:
            max_x = max(max_x, cell["x"])
            max_y = max(max_y, cell["y"])
    return max_x, max_y


# ===============================================================
# 3. Move Generation Functions
# ===============================================================

def refine_move(placement_dict, logical_db):
    """
    REFINE: Swap two randomly selected cells.
    Returns (cell1, cell2, pos1, pos2) or None if invalid.
    
    Format: placement_dict[cell_name] = (slot_name, cell_type, x, y)
    """
    cells = get_placeable_cells(logical_db, placement_dict)
    if len(cells) < 2:
        return None
    
    cell1, cell2 = random.sample(cells, 2)
    pos1 = placement_dict[cell1]  # (slot_name, cell_type, x, y)
    pos2 = placement_dict[cell2]
    
    # Verify both cells have the same type (can only swap compatible types)
    if pos1[1] != pos2[1]:
        return None
    
    return (cell1, cell2, pos1, pos2)


def explore_move(placement_dict, fabric_db, logical_db, netlist_graph, window_size=None):
    """
    EXPLORE: Move one cell to a nearby available slot (guided by neighbors).
    Returns (cell, old_pos, new_pos) or None if no slots available.
    
    Args:
        window_size: Maximum distance (in die units) from current position. 
                    If None, no range limiting is applied.
    
    Format: placement_dict[cell_name] = (slot_name, cell_type, x, y)
    """
    available = get_available_slots(fabric_db, placement_dict)
    if not available:
        return None
    
    cells = get_placeable_cells(logical_db, placement_dict)
    if not cells:
        return None
    
    cell = random.choice(cells)
    old_pos = placement_dict[cell]  # (slot_name, cell_type, x, y)
    old_x, old_y = old_pos[2], old_pos[3]
    required_type = old_pos[1]
    
    # Filter available slots to matching cell type
    available = [(name, ctype, x, y) for name, ctype, x, y in available if ctype == required_type]
    
    if not available:
        return None
    
    # Apply range-limiting window if specified
    if window_size is not None:
        available = [
            (name, ctype, x, y) for name, ctype, x, y in available
            if abs(x - old_x) <= window_size and abs(y - old_y) <= window_size
        ]
        
        # If no slots within window, fall back to all available slots of correct type
        if not available:
            available = [(name, ctype, x, y) for name, ctype, x, y in 
                        get_available_slots(fabric_db, placement_dict) if ctype == required_type]
    
    # Try to find a slot near this cell's neighbors
    neighbors = list(netlist_graph.neighbors(cell))
    placed_neighbors = [n for n in neighbors if n in placement_dict and not is_port(n, placement_dict)]
    
    if placed_neighbors:
        # Calculate center of neighbors
        avg_x = sum(placement_dict[n][2] for n in placed_neighbors) / len(placed_neighbors)
        avg_y = sum(placement_dict[n][3] for n in placed_neighbors) / len(placed_neighbors)
        
        # Find closest available slot to neighbor center
        def distance(slot_info):
            name, ctype, x, y = slot_info
            return (x - avg_x)**2 + (y - avg_y)**2
        
        # Pick from top 5 closest slots (some randomness)
        available_sorted = sorted(available, key=distance)
        candidates = available_sorted[:min(5, len(available_sorted))]
        new_slot_name, new_cell_type, new_x, new_y = random.choice(candidates)
    else:
        # No neighbors, pick randomly but close to current position
        def distance(slot_info):
            name, ctype, x, y = slot_info
            return (x - old_x)**2 + (y - old_y)**2
        
        available_sorted = sorted(available, key=distance)
        candidates = available_sorted[:min(5, len(available_sorted))]
        new_slot_name, new_cell_type, new_x, new_y = random.choice(candidates)
    
    new_pos = (new_slot_name, new_cell_type, new_x, new_y)
    
    return (cell, old_pos, new_pos)


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
        move_data = refine_move(placement_dict, logical_db)
        if move_data:
            return ("refine", move_data)
    else:
        # EXPLORE: Shift one cell (with optional window size)
        move_data = explore_move(placement_dict, fabric_db, logical_db, netlist_graph, window_size)
        if move_data:
            return ("explore", move_data)
    
    return (None, None)


# ===============================================================
# 4. Move Application and Reversal
# ===============================================================

def apply_move(placement_dict, move_type, move_data):
    """
    Apply a move to the placement dictionary (in-place).
    
    Format: placement_dict[cell_name] = (slot_name, cell_type, x, y)
    """
    if move_type == "refine":
        cell1, cell2, pos1, pos2 = move_data
        # Swap positions
        placement_dict[cell1] = pos2
        placement_dict[cell2] = pos1
        
    elif move_type == "explore":
        cell, old_pos, new_pos = move_data
        # Move cell to new position
        placement_dict[cell] = new_pos


def revert_move(placement_dict, move_type, move_data):
    """
    Revert a move from the placement dictionary (in-place).
    
    Format: placement_dict[cell_name] = (slot_name, cell_type, x, y)
    """
    if move_type == "refine":
        # Swap back
        cell1, cell2, pos1, pos2 = move_data
        placement_dict[cell1] = pos1
        placement_dict[cell2] = pos2
        
    elif move_type == "explore":
        # Move back to old position
        cell, old_pos, new_pos = move_data
        placement_dict[cell] = old_pos


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
    
    print(f"\n{'='*60}")
    print(f"STARTING SIMULATED ANNEALING OPTIMIZATION")
    print(f"{'='*60}")
    print(f"Initial HPWL: {current_cost:.2f} µm")
    print(f"Temperature: {config.initial_temp:.2f} → {config.final_temp:.2f}")
    print(f"Cooling Rate: {config.cooling_rate}")
    print(f"Moves per Temperature: {config.moves_per_temp}")
    print(f"{'='*60}\n")
    
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
            print(f"  [REHEAT] Current solution degraded, returning to best and reheating")
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
    
    print("Running initial greedy placement...")
    initial_placement_dict = initial_placement(fabric_db, logical_db, netlist_graph)
    
    # Calculate initial HPWL
    initial_hpwl = calculate_hpwl(netlist_graph, initial_placement_dict, logical_db)
    print(f"\nGreedy Placement HPWL: {initial_hpwl:.2f} µm")
    
    # Write initial placement
    write_map_file(initial_placement_dict, fabric_db, filename="placement_greedy_initial.map")
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
    
    # Write optimized placement
    write_map_file(optimized_placement, fabric_db, filename="placement_sa_optimized.map")
    print(f"[OK] SA-optimized placement saved to: placement_sa_optimized.map")
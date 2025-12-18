#!/usr/bin/env python3
"""
rename_verilog_cells.py

Rename cell instances in a Verilog netlist from logical names to fabric placement names.

Usage:
    python rename_verilog_cells.py --verilog design_final.v --placement placement.map --output design_final_renamed.v

This script reads:
  1. A Verilog netlist with logical cell instance names
  2. A placement map file mapping logical instances to fabric cells
  
And produces:
  3. A new Verilog netlist with fabric cell names
"""

import argparse
import os
import re
from typing import Dict, Tuple


def load_placement_map(placement_file: str) -> Dict[str, str]:
    """
    Load placement mapping: logical_instance -> fabric_slot.
    
    Parses .map format:
    fabric_slot  cell_type  x  y  ->  logical_instance
    
    Args:
        placement_file: Path to .map file
    
    Returns:
        Dict[logical_instance, fabric_slot]
    """
    placement_map = {}
    
    if not os.path.exists(placement_file):
        print(f"Error: Placement file not found: {placement_file}")
        return placement_map
    
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
                placement_map[logical_inst] = fabric_cell
    
    return placement_map


def rename_cells_in_verilog(verilog_content: str, placement_map: Dict[str, str]) -> Tuple[str, Dict[str, int]]:
    """
    Rename cell instances in Verilog from logical names to fabric names.
    
    Args:
        verilog_content: String containing Verilog netlist
        placement_map: Dict mapping logical_instance -> fabric_slot
    
    Returns:
        Tuple of (renamed_verilog, rename_stats)
    """
    lines = verilog_content.split('\n')
    renamed_lines = []
    total_cells = 0
    renamed_count = 0
    unmapped_count = 0
    
    for line in lines:
        match = re.match(r'^(\s+)(\w+(?:::\w+)*)\s+(\S+)\s+\(', line)
        if match:
            total_cells += 1
            whitespace = match.group(1)
            cell_type = match.group(2)
            instance_name = match.group(3)
            rest_of_line = line[match.end() - 1:]
            if instance_name in placement_map:
                fabric_name = placement_map[instance_name]
                renamed_line = f"{whitespace}{cell_type} {fabric_name} {rest_of_line}"
                renamed_lines.append(renamed_line)
                renamed_count += 1
            else:
                renamed_lines.append(line)
                unmapped_count += 1
        else:
            renamed_lines.append(line)
    
    renamed_verilog = '\n'.join(renamed_lines)
    stats = {
        'total_cells': total_cells,
        'renamed_cells': renamed_count,
        'unmapped_cells': unmapped_count
    }
    return renamed_verilog, stats


def main():
    parser = argparse.ArgumentParser(description="Rename cell instances in Verilog from logical names to fabric placement names")
    parser.add_argument("--verilog", required=True, help="Path to input Verilog file")
    parser.add_argument("--placement", required=True, help="Path to placement .map file")
    parser.add_argument("--output", default=None, help="Output Verilog file (default: input_renamed.v)")
    parser.add_argument("--verbose", action="store_true", help="Print detailed progress")
    args = parser.parse_args()

    if not os.path.exists(args.verilog):
        print(f"Error: Verilog file not found: {args.verilog}")
        return 1
    if not os.path.exists(args.placement):
        print(f"Error: Placement file not found: {args.placement}")
        return 1

    if args.verbose:
        print(f"Loading placement map: {args.placement}")
    placement_map = load_placement_map(args.placement)
    if args.verbose:
        print(f"  Loaded {len(placement_map)} cell mappings")

    if args.verbose:
        print(f"Reading Verilog: {args.verilog}")
    with open(args.verilog, 'r') as f:
        verilog_content = f.read()

    if args.verbose:
        print("Renaming cell instances...")
    renamed_verilog, stats = rename_cells_in_verilog(verilog_content, placement_map)
    if args.verbose:
        print(f"  Total cell instantiations: {stats['total_cells']}")
        print(f"  Renamed: {stats['renamed_cells']}")
        print(f"  Unmapped: {stats['unmapped_cells']}")

    if args.output is None:
        base, ext = os.path.splitext(args.verilog)
        args.output = f"{base}_renamed{ext}"

    if args.verbose:
        print(f"Writing output: {args.output}")
    with open(args.output, 'w') as f:
        f.write(renamed_verilog)

    print(f"✓ Successfully renamed {stats['renamed_cells']} cells")
    print(f"  Output: {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())

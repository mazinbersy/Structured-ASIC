#!/usr/bin/env python3
"""
cts_htree.py
------------
Implements H-Tree Clock Tree Synthesis (CTS) algorithm.

Finds all placed DFFs (sinks) and unused buffer/inverter cells (resources).
Recursively finds geometric center of sinks, claims nearest available buffer,
and updates placement.map.

Usage:
    python cts_htree.py placement.map [clock_net_name]
"""

import sys
import json
import math
from typing import List, Dict, Tuple, Set


class HTreeCTS:
    def __init__(self, placement_file: str):
        """Initialize CTS with placement data."""
        self.placement_file = placement_file

        print(f"Loading placement: {placement_file}")
        self.io_ports = {}  # I/O port positions
        self.fabric_cells = {}  # Fabric cell info
        self._parse_placement()

        self.sinks = []  # DFF locations (clock sinks)
        self.resources = []  # Available buffers/inverters
        self.clock_tree = {}  # H-Tree structure
        self.clock_net = None  # Clock net name

    def _parse_placement(self):
        """Parse placement.map file."""
        with open(self.placement_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # Check if line contains '->' (fabric cell mapping)
                if '->' in line:
                    parts = line.split('->')
                    left_parts = parts[0].strip().split()

                    if len(left_parts) < 4:
                        continue

                    fabric_cell = left_parts[0]
                    cell_type = left_parts[1]

                    try:
                        x = float(left_parts[2])
                        y = float(left_parts[3])
                    except (ValueError, IndexError):
                        continue

                    mapped_cell = parts[1].strip()

                    self.fabric_cells[fabric_cell] = {
                        'type': cell_type,
                        'x': x,
                        'y': y,
                        'mapped': mapped_cell,
                        'is_unused': (mapped_cell == 'UNUSED')
                    }
                else:
                    # I/O port line: port_name x y
                    parts = line.split()
                    if len(parts) >= 3:
                        port_name = parts[0]
                        try:
                            x = float(parts[1])
                            y = float(parts[2])
                            self.io_ports[port_name] = (x, y)
                        except ValueError:
                            continue

        print(f"Loaded {len(self.io_ports)} I/O ports")
        print(f"Loaded {len(self.fabric_cells)} fabric cells")

    def find_clock_net(self, clock_name: str = None) -> str:
        """Identify the clock net (default to 'clk' if not specified)."""
        if clock_name:
            if clock_name in self.io_ports:
                self.clock_net = clock_name
                print(f"Using specified clock net: {clock_name}")
                return clock_name
            else:
                print(f"Warning: Clock net '{clock_name}' not found in I/O ports")

        # Look for 'clk' port
        for port_name in self.io_ports:
            if 'clk' in port_name.lower():
                self.clock_net = port_name
                print(f"Found clock net: {port_name}")
                return port_name

        print("Warning: No clock net found")
        return None

    def find_sinks(self) -> List[Dict]:
        """Find all DFF cells (clock sinks) from placement."""
        self.sinks = []

        for fabric_cell, info in self.fabric_cells.items():
            cell_type = info['type']

            # Check if it's a DFF
            is_dff = 'dfbbp' in cell_type.lower()

            if is_dff and not info['is_unused']:
                sink_info = {
                    'id': fabric_cell,
                    'type': cell_type,
                    'x': info['x'],
                    'y': info['y'],
                    'mapped': info['mapped']
                }
                self.sinks.append(sink_info)

        print(f"Found {len(self.sinks)} DFF sinks")
        return self.sinks

    def find_resources(self) -> List[Dict]:
        """Find all unused buffer/inverter cells."""
        self.resources = []

        for fabric_cell, info in self.fabric_cells.items():
            if not info['is_unused']:
                continue

            cell_type = info['type']

            # Check if it's a buffer or inverter
            is_buffer = any(pattern in cell_type.lower() for pattern in
                            ['buf', 'clkbuf'])
            is_inverter = any(pattern in cell_type.lower() for pattern in
                              ['inv', 'clkinv'])

            if is_buffer or is_inverter:
                self.resources.append({
                    'name': fabric_cell,
                    'type': cell_type,
                    'x': info['x'],
                    'y': info['y'],
                    'claimed': False,
                    'is_buffer': is_buffer
                })

        print(f"Found {len(self.resources)} available buffer/inverter resources")
        print(f"  Buffers: {sum(1 for r in self.resources if r['is_buffer'])}")
        print(f"  Inverters: {sum(1 for r in self.resources if not r['is_buffer'])}")
        return self.resources

    def compute_centroid(self, sinks: List[Dict]) -> Tuple[float, float]:
        """Calculate geometric center of a list of sinks."""
        if not sinks:
            return (0, 0)

        sum_x = sum(sink['x'] for sink in sinks)
        sum_y = sum(sink['y'] for sink in sinks)

        return (sum_x / len(sinks), sum_y / len(sinks))

    def find_nearest_resource(self, x: float, y: float,
                              prefer_buffer: bool = True) -> Dict:
        """Find the nearest unclaimed buffer/inverter to given coordinates."""
        min_dist = float('inf')
        nearest = None

        for resource in self.resources:
            if resource['claimed']:
                continue

            # Prefer buffers over inverters if specified
            if prefer_buffer and not resource['is_buffer']:
                continue

            dx = resource['x'] - x
            dy = resource['y'] - y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist < min_dist:
                min_dist = dist
                nearest = resource

        # If no buffer found and we were preferring buffers, try inverters
        if nearest is None and prefer_buffer:
            return self.find_nearest_resource(x, y, prefer_buffer=False)

        return nearest

    def partition_sinks(self, sinks: List[Dict], cx: float, cy: float) -> List[List[Dict]]:
        """Partition sinks into quadrants around centroid (cx, cy)."""
        quadrants = [[], [], [], []]  # NE, NW, SW, SE

        for sink in sinks:
            dx = sink['x'] - cx
            dy = sink['y'] - cy

            if dx >= 0 and dy >= 0:
                quadrants[0].append(sink)  # NE
            elif dx < 0 and dy >= 0:
                quadrants[1].append(sink)  # NW
            elif dx < 0 and dy < 0:
                quadrants[2].append(sink)  # SW
            else:
                quadrants[3].append(sink)  # SE

        # Filter out empty quadrants
        return [q for q in quadrants if q]

    def build_htree_recursive(self, sinks: List[Dict], level: int = 0,
                              parent_buffer: str = "clk_root") -> Dict:
        """
        Recursively build H-Tree structure.

        At each level:
        1. Find centroid of sinks
        2. Claim nearest buffer at centroid
        3. Split sinks into quadrants (X-pattern)
        4. Recurse on each quadrant
        """
        if not sinks:
            return None

        indent = "  " * level

        # Base case: few enough sinks to connect directly
        if len(sinks) <= 4 or level > 8:  # Prevent infinite recursion
            cx, cy = self.compute_centroid(sinks)
            buffer = self.find_nearest_resource(cx, cy)

            tree_node = {
                'level': level,
                'parent': parent_buffer,
                'sinks': [s['id'] for s in sinks],
                'centroid': (cx, cy),
                'buffer': None
            }

            if buffer:
                buffer['claimed'] = True
                tree_node['buffer'] = buffer['name']
                tree_node['buffer_pos'] = (buffer['x'], buffer['y'])
                tree_node['buffer_type'] = buffer['type']

                print(f"{indent}Level {level}: Claimed {buffer['type']} '{buffer['name']}' "
                      f"at ({buffer['x']:.2f}, {buffer['y']:.2f}) for {len(sinks)} sinks")
            else:
                print(f"{indent}Level {level}: WARNING - No buffer available for {len(sinks)} sinks")

            return tree_node

        # Recursive case: partition and recurse
        cx, cy = self.compute_centroid(sinks)
        buffer = self.find_nearest_resource(cx, cy)

        tree_node = {
            'level': level,
            'parent': parent_buffer,
            'centroid': (cx, cy),
            'buffer': None,
            'children': []
        }

        if buffer:
            buffer['claimed'] = True
            buffer_name = buffer['name']
            tree_node['buffer'] = buffer_name
            tree_node['buffer_pos'] = (buffer['x'], buffer['y'])
            tree_node['buffer_type'] = buffer['type']

            print(f"{indent}Level {level}: Claimed {buffer['type']} '{buffer_name}' "
                  f"at ({buffer['x']:.2f}, {buffer['y']:.2f}) - partitioning {len(sinks)} sinks")
        else:
            buffer_name = f"virtual_buf_L{level}"
            print(f"{indent}Level {level}: WARNING - No buffer, using virtual node")

        # Partition into quadrants
        quadrants = self.partition_sinks(sinks, cx, cy)

        print(f"{indent}  Quadrants: {[len(q) for q in quadrants]}")

        # Recurse on each quadrant
        for i, quad_sinks in enumerate(quadrants):
            child_node = self.build_htree_recursive(quad_sinks, level + 1, buffer_name)
            if child_node:
                tree_node['children'].append(child_node)

        return tree_node

    def build_clock_tree(self) -> Dict:
        """Build the H-Tree clock tree structure."""
        if not self.sinks:
            print("ERROR: No sinks found. Run find_sinks() first.")
            return None

        if not self.resources:
            print("ERROR: No resources found. Run find_resources() first.")
            return None

        print(f"\nBuilding H-Tree for {len(self.sinks)} sinks with {len(self.resources)} resources...")
        print("=" * 70)

        self.clock_tree = self.build_htree_recursive(self.sinks)

        print("=" * 70)
        print("Clock tree construction complete")

        return self.clock_tree

    def write_placement(self, output_file: str):
        """Write updated placement.map with newly placed buffers."""
        print(f"\nWriting updated placement to: {output_file}")

        with open(output_file, 'w') as f:
            # Write I/O ports first
            for port_name in sorted(self.io_ports.keys()):
                x, y = self.io_ports[port_name]
                f.write(f"{port_name} {x:.2f} {y:.2f}\n")

            # Write fabric cells
            for fabric_cell in sorted(self.fabric_cells.keys()):
                info = self.fabric_cells[fabric_cell]

                # Check if this cell was claimed by CTS
                mapped = info['mapped']
                for resource in self.resources:
                    if resource['name'] == fabric_cell and resource['claimed']:
                        mapped = fabric_cell + "_CLK"
                        break

                f.write(f"{fabric_cell}  {info['type']}  {info['x']:.2f}  {info['y']:.2f}  ->  {mapped}\n")

        print(f"Wrote placement map")

    def write_clock_tree(self, output_file: str):
        """Write clock tree structure to JSON."""
        print(f"Writing clock tree structure to: {output_file}")

        with open(output_file, 'w') as f:
            json.dump(self.clock_tree, f, indent=2)

    def print_summary(self):
        """Print summary statistics."""
        print("\n" + "=" * 70)
        print("CTS SUMMARY")
        print("=" * 70)
        print(f"Total DFF sinks:          {len(self.sinks)}")
        print(f"Total resources:          {len(self.resources)}")

        claimed = sum(1 for r in self.resources if r['claimed'])
        print(f"Buffers claimed:          {claimed}")
        print(f"Buffers remaining:        {len(self.resources) - claimed}")

        if self.clock_tree:
            def count_levels(node, max_level=0):
                if not node:
                    return max_level
                current = node.get('level', 0)
                max_level = max(max_level, current)
                for child in node.get('children', []):
                    max_level = max(max_level, count_levels(child, max_level))
                return max_level

            max_depth = count_levels(self.clock_tree)
            print(f"Tree depth:               {max_depth}")

        print("=" * 70)


def main():
    if len(sys.argv) < 2:
        print("Usage: python cts_htree.py <placement.map> [clock_net_name]")
        print("\nExample:")
        print("  python cts_htree.py placement.map")
        print("  python cts_htree.py placement.map clk")
        sys.exit(1)

    placement_file = sys.argv[1]
    clock_name = sys.argv[2] if len(sys.argv) > 2 else None

    # Initialize CTS
    cts = HTreeCTS(placement_file)

    # Run CTS flow
    cts.find_clock_net(clock_name)
    cts.find_sinks()
    cts.find_resources()
    cts.build_clock_tree()

    # Write outputs
    cts.write_placement("placement_cts.map")
    cts.write_clock_tree("clock_tree.json")

    # Print summary
    cts.print_summary()

    print("\nCTS Complete!")
    print("Outputs:")
    print("  - placement_cts.map   : Updated placement with buffers")
    print("  - clock_tree.json     : Clock tree structure")


if __name__ == "__main__":
    main()
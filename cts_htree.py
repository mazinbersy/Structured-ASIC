#!/usr/bin/env python3
"""
cts_htree.py
------------
Implements H-Tree Clock Tree Synthesis (CTS) algorithm.

Finds all placed DFFs (sinks) and unused buffer/inverter cells (resources).
Recursively finds geometric center of sinks, claims nearest available buffer,
and updates placement.map, logical_db, and netlist_graph.

Usage:
    python cts_htree.py [placement.map] [design_json] [clock_net_name]
"""

import sys
import json
import math
from typing import List, Dict, Tuple, Set
from collections import defaultdict
import networkx as nx
from networkx.readwrite import json_graph

from build_fabric_db import build_fabric_db
from parse_design import parse_design_json


def parse_placement_map(placement_file: str) -> Tuple[Dict, Dict]:
    """
    Parse placement.map file and return I/O ports and fabric cells.

    Returns:
        Tuple of (io_ports, fabric_cells)
    """
    io_ports = {}  # I/O port positions
    fabric_cells = {}  # Fabric cell info

    print(f"Loading placement: {placement_file}")

    with open(placement_file, 'r') as f:
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

                fabric_cells[fabric_cell] = {
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
                        io_ports[port_name] = (x, y)
                    except ValueError:
                        continue

    print(f"Loaded {len(io_ports)} I/O ports")
    print(f"Loaded {len(fabric_cells)} fabric cells from placement")

    return io_ports, fabric_cells


class HTreeCTS:
    def __init__(self, io_ports: Dict, fabric_cells: Dict,
                 fabric_db: Dict, logical_db: Dict, netlist_graph: nx.Graph):
        """Initialize CTS with placement data, fabric database, and logical netlist."""
        self.io_ports = io_ports
        self.fabric_cells = fabric_cells
        self.fabric_db = fabric_db
        self.logical_db = logical_db
        self.netlist_graph = netlist_graph

        print(f"Initialized with logical_db containing {len(self.logical_db['cells'])} cells")
        print(f"Initialized with netlist_graph containing {len(self.netlist_graph.nodes())} nodes")

        self.sinks = []  # DFF locations (clock sinks)
        self.resources = []  # Available buffers/inverters
        self.clock_tree = {}  # H-Tree structure
        self.clock_net = None  # Clock net name
        self.clock_net_id = None  # Clock net ID from logical_db
        self.buffer_counter = 0  # Counter for naming buffers

    def find_clock_net(self, clock_name: str = None) -> str:
        """Identify the clock net (default to 'clk' if not specified)."""
        if clock_name:
            if clock_name in self.io_ports:
                self.clock_net = clock_name
                # Find the net ID in logical_db
                if clock_name in self.logical_db['ports']['inputs']:
                    self.clock_net_id = self.logical_db['ports']['inputs'][clock_name]
                print(f"Using specified clock net: {clock_name} (net_id: {self.clock_net_id})")
                return clock_name
            else:
                print(f"Warning: Clock net '{clock_name}' not found in I/O ports")

        # Look for 'clk' port
        for port_name in self.io_ports:
            if 'clk' in port_name.lower():
                self.clock_net = port_name
                if port_name in self.logical_db['ports']['inputs']:
                    self.clock_net_id = self.logical_db['ports']['inputs'][port_name]
                print(f"Found clock net: {port_name} (net_id: {self.clock_net_id})")
                return port_name

        print("Warning: No clock net found")
        return None

    def find_sinks(self) -> List[Dict]:
        """Find all DFF cells (clock sinks) from placement."""
        self.sinks = []

        for fabric_cell, info in self.fabric_cells.items():
            cell_type = info['type']

            # Check if it's a DFF
            is_dff = 'dfbbp' in cell_type.lower() or 'dff' in cell_type.lower()

            if is_dff and not info['is_unused']:
                # Find corresponding logical cell
                logical_cell = info['mapped']

                sink_info = {
                    'id': fabric_cell,
                    'type': cell_type,
                    'x': info['x'],
                    'y': info['y'],
                    'mapped': logical_cell
                }
                self.sinks.append(sink_info)

        print(f"Found {len(self.sinks)} DFF sinks")
        return self.sinks

    def find_resources(self) -> List[Dict]:
        """Find all unused buffer/inverter cells from fabric_db."""
        self.resources = []
        used_cells = set(info['mapped'] for info in self.fabric_cells.values()
                         if not info['is_unused'])

        # Get cells_by_tile from fabric database
        cells_by_tile = self.fabric_db.get('fabric', {}).get('cells_by_tile', {})

        for tile_name, tile_data in cells_by_tile.items():
            cells = tile_data.get('cells', [])

            for cell in cells:
                cell_name = cell.get('name')
                cell_type = cell.get('cell_type', '').lower()
                x = cell.get('x', 0)
                y = cell.get('y', 0)

                if not cell_name:
                    continue

                # Skip if this cell is already used in placement
                if cell_name in used_cells:
                    continue

                # Check if this fabric cell is in placement and marked as used
                if cell_name in self.fabric_cells and not self.fabric_cells[cell_name]['is_unused']:
                    continue

                # Check if it's a buffer or inverter using BUF or INV keywords
                is_buffer = 'buf' in cell_type
                is_inverter = 'inv' in cell_type

                if is_buffer or is_inverter:
                    self.resources.append({
                        'name': cell_name,
                        'type': cell.get('cell_type'),
                        'x': x,
                        'y': y,
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
                'sink_logical_names': [s['mapped'] for s in sinks],
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

    def remove_old_clock_connections(self):
        """Remove old clock net connections from logical_db and netlist_graph."""
        if not self.clock_net_id:
            print("Warning: No clock net ID found, skipping removal of old connections")
            return

        print(f"\nRemoving old clock connections for net {self.clock_net_id}...")

        # Get the old clock net
        if self.clock_net_id not in self.logical_db['nets']:
            print(f"Warning: Clock net {self.clock_net_id} not found in logical_db")
            return

        old_net = self.logical_db['nets'][self.clock_net_id]
        old_connections = old_net.get('connections', [])

        # Remove clock connections from cells (except the clock port itself)
        for inst_name, pin_name in old_connections:
            if inst_name == self.clock_net:  # Keep the port connection
                continue

            if inst_name in self.logical_db['cells']:
                cell = self.logical_db['cells'][inst_name]
                if pin_name in cell['pins'] and cell['pins'][pin_name] == self.clock_net_id:
                    del cell['pins'][pin_name]

        # Update the net to only have the port connection
        old_net['connections'] = [(self.clock_net, 'PORT')]

        # Remove clock edges from netlist_graph
        edges_to_remove = []
        for u, v, data in self.netlist_graph.edges(data=True):
            if data.get('net_id') == self.clock_net_id:
                edges_to_remove.append((u, v))

        for u, v in edges_to_remove:
            self.netlist_graph.remove_edge(u, v)

        print(f"Removed {len(old_connections) - 1} old clock connections")

    def update_logical_db_and_graph(self):
        """Update logical_db and netlist_graph with new clock tree."""
        if not self.clock_tree:
            print("ERROR: No clock tree built. Run build_clock_tree() first.")
            return

        print("\nUpdating logical_db and netlist_graph with clock tree...")

        # First, remove old clock connections
        self.remove_old_clock_connections()

        # Counter for creating new net IDs
        max_net_id = max(self.logical_db['nets'].keys()) if self.logical_db['nets'] else 0
        net_counter = max_net_id + 1

        # Track all buffers to add
        buffers_to_add = []
        connections_to_add = []  # (buffer_name, pin_name, net_id)

        def traverse_tree(node, parent_net_id):
            """Traverse clock tree and build connection list."""
            nonlocal net_counter

            if not node:
                return

            buffer_name = node.get('buffer')
            buffer_type = node.get('buffer_type', 'BUF')

            # If this node has a buffer, add it
            if buffer_name:
                # Create input net (from parent)
                in_net_id = parent_net_id

                # Create output net (to children/sinks)
                out_net_id = net_counter
                net_counter += 1

                # Add buffer to list
                buffers_to_add.append({
                    'name': buffer_name,
                    'type': buffer_type,
                    'in_net': in_net_id,
                    'out_net': out_net_id
                })

                # Connect buffer input to parent net
                connections_to_add.append((buffer_name, 'A', in_net_id))

                # Connect buffer output to output net
                connections_to_add.append((buffer_name, 'Y', out_net_id))

                # Process children with the output net
                for child in node.get('children', []):
                    traverse_tree(child, out_net_id)

                # Connect sinks if this is a leaf
                if 'sink_logical_names' in node:
                    for sink_name in node['sink_logical_names']:
                        if sink_name in self.logical_db['cells']:
                            # Find the clock pin (typically 'C' or 'CLK')
                            cell = self.logical_db['cells'][sink_name]
                            clock_pin = 'C' if 'C' in cell.get('pins', {}) else 'CLK'
                            connections_to_add.append((sink_name, clock_pin, out_net_id))
            else:
                # Virtual node - pass through parent net
                for child in node.get('children', []):
                    traverse_tree(child, parent_net_id)

                # Connect sinks directly if this is a leaf
                if 'sink_logical_names' in node:
                    for sink_name in node['sink_logical_names']:
                        if sink_name in self.logical_db['cells']:
                            cell = self.logical_db['cells'][sink_name]
                            clock_pin = 'C' if 'C' in cell.get('pins', {}) else 'CLK'
                            connections_to_add.append((sink_name, clock_pin, parent_net_id))

        # Start traversal from root with clock net
        traverse_tree(self.clock_tree, self.clock_net_id)

        # Add buffers to logical_db
        for buf in buffers_to_add:
            self.logical_db['cells'][buf['name']] = {
                'type': buf['type'],
                'pins': {
                    'A': buf['in_net'],
                    'Y': buf['out_net']
                }
            }

            # Add to cells_by_type
            if buf['type'] not in self.logical_db['cells_by_type']:
                self.logical_db['cells_by_type'][buf['type']] = []
            self.logical_db['cells_by_type'][buf['type']].append(buf['name'])

            # Add buffer node to netlist_graph
            self.netlist_graph.add_node(buf['name'], type=buf['type'], node_type='cell')

        print(f"Added {len(buffers_to_add)} buffers to logical_db")

        # Create new nets and add connections
        net_connections = defaultdict(list)
        for inst_name, pin_name, net_id in connections_to_add:
            net_connections[net_id].append((inst_name, pin_name))

            # Update cell pins
            if inst_name in self.logical_db['cells']:
                self.logical_db['cells'][inst_name]['pins'][pin_name] = net_id

        # Add nets to logical_db
        for net_id, connections in net_connections.items():
            if net_id not in self.logical_db['nets']:
                self.logical_db['nets'][net_id] = {
                    'name': f'clk_net_{net_id}',
                    'connections': []
                }
            self.logical_db['nets'][net_id]['connections'].extend(connections)

        # Add edges to netlist_graph
        edges_added = 0
        for net_id, connections in net_connections.items():
            node_list = [n for (n, _) in connections]
            if len(node_list) <= 1:
                continue

            net_name = self.logical_db['nets'][net_id]['name']

            for i in range(len(node_list)):
                for j in range(i + 1, len(node_list)):
                    u, v = node_list[i], node_list[j]
                    if not self.netlist_graph.has_edge(u, v):
                        self.netlist_graph.add_edge(u, v, nets=[net_name], net_id=net_id)
                        edges_added += 1
                    else:
                        self.netlist_graph[u][v]['nets'].append(net_name)

        print(f"Added {len(net_connections)} new nets")
        print(f"Added {edges_added} edges to netlist_graph")
        print(f"Updated netlist_graph now has {len(self.netlist_graph.nodes())} nodes, "
              f"{len(self.netlist_graph.edges())} edges")
        return self.logical_db, self.netlist_graph

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
                        mapped = fabric_cell  # Use the fabric cell name itself
                        break

                f.write(f"{fabric_cell}  {info['type']}  {info['x']:.2f}  {info['y']:.2f}  ->  {mapped}\n")

        print(f"Wrote placement map")

    def write_clock_tree(self, output_file: str):
        """Write clock tree structure to JSON."""
        print(f"Writing clock tree structure to: {output_file}")

        with open(output_file, 'w') as f:
            json.dump(self.clock_tree, f, indent=2)

    def write_logical_db(self, output_file: str = "logical_db_cts.json"):
        """Write updated logical_db to JSON."""
        print(f"Writing updated logical_db to: {output_file}")

        with open(output_file, 'w') as f:
            json.dump(self.logical_db, f, indent=2)

    def write_netlist_graph(self, output_file: str = "netlist_graph_cts.json"):
        """Write updated netlist_graph to JSON."""
        print(f"Writing updated netlist_graph to: {output_file}")

        graph_data = json_graph.node_link_data(self.netlist_graph)
        with open(output_file, 'w') as f:
            json.dump(graph_data, f, indent=2)

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

        print(f"\nLogical DB:")
        print(f"  Total cells:            {len(self.logical_db['cells'])}")
        print(f"  Total nets:             {len(self.logical_db['nets'])}")
        print(f"\nNetlist Graph:")
        print(f"  Total nodes:            {len(self.netlist_graph.nodes())}")
        print(f"  Total edges:            {len(self.netlist_graph.edges())}")
        print("=" * 70)


def main():
    # Set default parameters
    placement_file = "placement.map"
    design_json = "designs/6502_mapped.json"
    clock_name = None

    # Parse command line arguments
    if len(sys.argv) >= 2:
        placement_file = sys.argv[1]
    if len(sys.argv) >= 3:
        design_json = sys.argv[2]
    if len(sys.argv) >= 4:
        clock_name = sys.argv[3]

    print("Usage: python cts_htree.py [placement.map] [design_json] [clock_net_name]")
    print(f"\nUsing:")
    print(f"  Placement file: {placement_file}")
    print(f"  Design JSON:    {design_json}")
    print(f"  Clock net:      {clock_name if clock_name else 'auto-detect'}")
    print()

    # Build fabric database
    print(f"Building fabric database from YAML files...")
    fabric_db = build_fabric_db(
        'fabric/fabric_cells.yaml',
        'fabric/pins.yaml',
        'fabric/fabric.yaml'
    )

    # Parse design netlist
    print(f"Loading design netlist: {design_json}")
    logical_db, netlist_graph = parse_design_json(design_json)
    print(f"Loaded logical_db with {len(logical_db['cells'])} cells")
    print(f"Loaded netlist_graph with {len(netlist_graph.nodes())} nodes")

    # Parse placement map
    io_ports, fabric_cells = parse_placement_map(placement_file)

    # Initialize CTS with pre-built databases and placement data
    cts = HTreeCTS(io_ports, fabric_cells, fabric_db, logical_db, netlist_graph)

    # Run CTS flow
    cts.find_clock_net(clock_name)
    cts.find_sinks()
    cts.find_resources()
    cts.build_clock_tree()

    # Update logical database and netlist graph
    cts.update_logical_db_and_graph()

    # Write outputs
    cts.write_placement("placement_cts.map")
    cts.write_clock_tree("clock_tree.json")
    cts.write_logical_db("logical_db_cts.json")
    cts.write_netlist_graph("netlist_graph_cts.json")

    # Print summary
    cts.print_summary()

    print("\nCTS Complete!")
    print("Outputs:")
    print("  - placement_cts.map       : Updated placement with buffers")
    print("  - clock_tree.json         : Clock tree structure")
    print("  - logical_db_cts.json     : Updated logical database")
    print("  - netlist_graph_cts.json  : Updated netlist graph")


if __name__ == "__main__":
    main()
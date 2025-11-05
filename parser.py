# parse_design.py
import json
from collections import defaultdict

"""
Design Parser for Yosys-generated JSON netlists.

OUTPUT DATA STRUCTURE:
======================

result = {
    'instances': {
        instance_name: {
            'type': cell_type,
            'pins': {pin_name: net_id, ...}
        },
        ...
    },
    
    'instances_by_type': {
        cell_type: [instance_name, ...],
        ...
    },
    
    'nets': {
        net_id: {
            'name': net_name,
            'connections': [(instance_name, pin_name), ...]
        },
        ...
    },
    
    'ports': {
        'inputs': {port_name: net_id, ...},
        'outputs': {port_name: net_id, ...}
    }
}
"""

def parse_design_json(json_file):
    """
    Parse *_mapped.json file.
    
    Args:
        json_file: Path to the JSON file
    
    Returns:
        Dictionary containing instances, nets, and ports
    """
    
    with open(json_file, 'r') as f:
        data = json.load(f)
    
    modules = data.get('modules', {})
    if not modules:
        raise ValueError(f"No modules found in {json_file}")
    
    # Find top module
    top_module = None
    for module_name, module_data in modules.items():
        if module_data.get('attributes', {}).get('top'):
            top_module = module_data
            break
    
    if top_module is None:
        top_module = list(modules.values())[0]
    
    # Initialize output structure
    result = {
        'instances': {},
        'instances_by_type': defaultdict(list),
        'nets': {},
        'ports': {
            'inputs': {},
            'outputs': {}
        }
    }
    
    # Parse ports
    ports = top_module.get('ports', {})
    for port_name, port_info in ports.items():
        direction = port_info.get('direction')
        net_id = port_info['bits'][0]
        
        if direction == 'input':
            result['ports']['inputs'][port_name] = net_id
        elif direction == 'output':
            result['ports']['outputs'][port_name] = net_id
        
        if net_id not in result['nets']:
            result['nets'][net_id] = {
                'name': port_name,
                'connections': []
            }
    
    # Parse cells
    cells = top_module.get('cells', {})
    for inst_name, cell_info in cells.items():
        cell_type = cell_info.get('type')
        
        if not cell_type:
            continue
        
        result['instances'][inst_name] = {
            'type': cell_type,
            'pins': {}
        }
        
        result['instances_by_type'][cell_type].append(inst_name)
        
        connections = cell_info.get('connections', {})
        for pin_name, net_bits in connections.items():
            net_id = net_bits[0]
            
            result['instances'][inst_name]['pins'][pin_name] = net_id
            
            if net_id not in result['nets']:
                result['nets'][net_id] = {
                    'name': f"net_{net_id}",
                    'connections': []
                }
            
            result['nets'][net_id]['connections'].append((inst_name, pin_name))
    
    # Debug print
    print(f"\nLogical Cell Type Counts:")
    for cell_type, instances in sorted(result['instances_by_type'].items()):
        print(f"  {cell_type}: {len(instances)}")
    print(f"\nTotal Logical Cell Types: {len(result['instances_by_type'])}")
    
    return result


if __name__ == "__main__":
    design_file = "designs/6502_mapped.json"
    design_data = parse_design_json(design_file)
    
    print(f"\n{'='*60}")
    print(f"Total Instances: {len(design_data['instances'])}")
    print(f"Total Nets: {len(design_data['nets'])}")
    print(f"Input Ports: {len(design_data['ports']['inputs'])}")
    print(f"Output Ports: {len(design_data['ports']['outputs'])}")
    print(f"{'='*60}")
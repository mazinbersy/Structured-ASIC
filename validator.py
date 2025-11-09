import sys
sys.dont_write_bytecode = True

from parse_design import parse_design_json
from build_fabric_db import build_fabric_db
from collections import defaultdict
import re


def normalize_cell_type(cell_name):
    """
    Extract the base cell type from a full cell name.
    
        sky130_fd_sc_hd__clkbuf_4 -> BUF
        sky130_fd_sc_hd__clkinv_2 -> INV
        sky130_fd_sc_hd__conb_1 -> CONB
        sky130_fd_sc_hd__dfbbp_1 -> DFBBP
        sky130_fd_sc_hd__nand2_2 -> NAND
        sky130_fd_sc_hd__or2_2 -> OR
    """
    # Pattern to extract cell type from sky130 standard cells
    # Matches: sky130_fd_sc_hd__<type>_<drive_strength>
    match = re.match(r'sky130_fd_sc_hd__([a-z0-9]+)_\d+', cell_name)
    if match:
        base_type = match.group(1)
        
        # Map common variations to fabric types
        type_map = {
            'clkbuf': 'BUF',
            'clkinv': 'INV',
            'conb': 'CONB',
            'dfbbp': 'DFBBP',
            'nand2': 'NAND',
            'nand3': 'NAND',
            'nand4': 'NAND',
            'or2': 'OR',
            'or3': 'OR',
            'or4': 'OR',
            'buf': 'BUF',
            'inv': 'INV',
        }
        
        return type_map.get(base_type, base_type.upper())
    
    return cell_name  # Return as-is if no match


def count_fabric_slots(fabric_db):
    """Counts how many cells of each type exist in the fabric_db (using a normal dict)."""
    cells_by_tile = fabric_db.get("fabric", {}).get("cells_by_tile", {})
    type_counts = {}

    # Regex to match pattern like "__R<number>_<TYPE>_"
    type_pattern = re.compile(r"__R\d+_([A-Za-z0-9]+)_")

    for tile_name, tile_data in cells_by_tile.items():
        cells = tile_data.get("cells", [])
        for cell in cells:
            cell_name = cell.get("name", "")
            match = type_pattern.search(cell_name)
            if match:
                cell_type = match.group(1)
            else:
                cell_type = "UNKNOWN"

            # Increment the count
            if cell_type in type_counts:
                type_counts[cell_type] += 1
            else:
                type_counts[cell_type] = 1

    return type_counts


def validate_design(logical_db, fabric_db):
    """
    Validate that the logical design fits within fabric constraints.
    
    Args:
        logical_db: Parsed design database
        fabric_db: Fabric database with available resources
    
    Returns:
        tuple: (is_valid, validation_results)
    """
    # Count required cells from design, normalizing cell types
    required_cells = defaultdict(list)
    for cell_type, cells in logical_db["cells_by_type"].items():
        normalized_type = normalize_cell_type(cell_type)
        required_cells[normalized_type].extend(cells)
    
    # Count available slots in fabric
    available_slots = count_fabric_slots(fabric_db)
    
    # Validation results
    validation_results = []
    is_valid = True
    
    # Check each cell type
    all_cell_types = set(required_cells.keys()) | set(available_slots.keys())
    
    for cell_type in sorted(all_cell_types):
        required = len(required_cells.get(cell_type, []))
        available = available_slots.get(cell_type, 0)
        
        if required > 0:
            utilization = (required / available * 100) if available > 0 else float('inf')
            fits = required <= available
            
            validation_results.append({
                "cell_type": cell_type,
                "required": required,
                "available": available,
                "utilization": utilization,
                "fits": fits
            })
            
            if not fits:
                is_valid = False
    
    return is_valid, validation_results


def print_validation_report(logical_db, validation_results, is_valid):
    """Print a comprehensive validation report to console."""
    
    print("=" * 70)
    print("FABRIC VALIDATION REPORT")
    print("=" * 70)
    print(f"Design: {logical_db['meta']['source_file']}")
    print(f"Top Module: {logical_db['meta']['top_module']}")
    print()
    
    # Design statistics
    print("Design Statistics:")
    print(f"  Total Cells: {len(logical_db['cells'])}")
    print(f"  Total Nets: {len(logical_db['nets'])}")
    print(f"  Input Ports: {len(logical_db['ports']['inputs'])}")
    print(f"  Output Ports: {len(logical_db['ports']['outputs'])}")
    print()
    
    # Fabric utilization by cell type
    print("Fabric Utilization by Cell Type:")
    print("-" * 70)
    print(f"{'Cell Type':<20} {'Required':<12} {'Available':<12} {'Usage':<15} {'Status'}")
    print("-" * 70)
    
    for result in validation_results:
        cell_type = result["cell_type"]
        required = result["required"]
        available = result["available"]
        utilization = result["utilization"]
        fits = result["fits"]
        
        if utilization == float('inf'):
            usage_str = "N/A (no slots)"
            status = "FAIL"
        else:
            usage_str = f"{utilization:.1f}%"
            status = "OK" if fits else "FAIL"
        
        print(f"{cell_type:<20} {required:<12} {available:<12} {usage_str:<15} {status}")
    
    print("-" * 70)
    
    # Overall summary
    print()
    total_required = sum(r["required"] for r in validation_results)
    total_available = sum(r["available"] for r in validation_results)
    overall_utilization = (total_required / total_available * 100) if total_available > 0 else 0
    
    print(f"Overall Fabric Utilization: {total_required}/{total_available} ({overall_utilization:.1f}%)")
    print()
    
    # Final verdict
    if is_valid:
        print("VALIDATION PASSED: Design fits within fabric constraints")
        print("=" * 70)
        return 0
    else:
        print("VALIDATION FAILED: Design exceeds fabric capacity")
        print()
        print("Errors:")
        for result in validation_results:
            if not result["fits"]:
                print(f"  - {result['cell_type']}: needs {result['required']}, "
                      f"only {result['available']} available "
                      f"(shortfall: {result['required'] - result['available']})")
        print("=" * 70)
        return 1


def main():
    """Main validation workflow."""
    
    # Parse command line arguments
    design_path = "designs/6502_mapped.json"
    fabric_cells_path = "fabric/fabric_cells.yaml"
    pins_path = "fabric/pins.yaml"
    fabric_def_path = "fabric/fabric.yaml"
    
    if len(sys.argv) > 1:
        design_path = sys.argv[1]
    if len(sys.argv) > 2:
        fabric_cells_path = sys.argv[2]
    if len(sys.argv) > 3:
        pins_path = sys.argv[3]
    if len(sys.argv) > 3:
        fabric_def_path = sys.argv[4]
    
    # Load design
    try:
        logical_db, netlist_graph = parse_design_json(design_path)
    except Exception as e:
        print(f"Error parsing design: {e}")
        sys.exit(1)
    
    # Build fabric database dynamically
    try:
        fabric_db = build_fabric_db(fabric_cells_path, pins_path, fabric_def_path)
    except Exception as e:
        print(f"Error building fabric database: {e}")
        sys.exit(1)
    
    # Validate design against fabric
    is_valid, validation_results = validate_design(logical_db, fabric_db)
    
    # Print report and exit with appropriate code
    exit_code = print_validation_report(logical_db, validation_results, is_valid)
    
    # Print warnings if any
    warnings = logical_db["meta"].get("multi_bit_warnings", [])
    if warnings:
        print()
        print("Warnings:")
        for warning in warnings[:5]:  # Show first 5 warnings
            print(f"  - {warning}")
        if len(warnings) > 5:
            print(f"  ... and {len(warnings) - 5} more warnings")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
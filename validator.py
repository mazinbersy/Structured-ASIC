from parse_design import parse_design_json

logical_db, netlist_graph = parse_design_json("designs/6502_mapped.json")

stats = {
    "total_cells": len(logical_db["cells"]),
    "cell_type_counts": {t: len(v) for t, v in logical_db["cells_by_type"].items()},
    "total_nets": len(logical_db["nets"]),
    "inputs": len(logical_db["ports"]["inputs"]),
    "outputs": len(logical_db["ports"]["outputs"]),
}

print("Top module:", logical_db["meta"]["top_module"])
print("Cell counts:", stats["cell_type_counts"])
print(f"Total Cells: {stats['total_cells']}, Total Nets: {stats['total_nets']}")

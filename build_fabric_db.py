# build_fabric_db.py
import yaml
import json
import sys
import re


def load_yaml(file_path):
    """Safely loads a YAML file."""
    try:
        with open(file_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: File not found - {file_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"YAML parsing error in {file_path}: {e}")
        sys.exit(1)


def build_fabric_db(fabric_cells_path, pins_path, fabric_def_path):
    """Merges fabric cell placement, pin placement data, and cell dimensions."""
    fabric_cells_data = load_yaml(fabric_cells_path)
    pins_data = load_yaml(pins_path)
    fabric_def_data = load_yaml(fabric_def_path)

    # Extract key data
    cells_by_tile = fabric_cells_data.get("fabric_cells_by_tile", {}).get("tiles", {})
    pin_placement = pins_data.get("pin_placement", {})

    # Extract cell definitions with dimensions from fabric.yaml
    cell_definitions = fabric_def_data.get("cell_definitions", {})
    site_dimensions = fabric_def_data.get("fabric_info", {}).get("site_dimensions_um", {})
    tile_definition = fabric_def_data.get("tile_definition", {})

    # Verify we have the expected data
    if not cell_definitions:
        print("Error: No cell_definitions found in fabric.yaml")
        sys.exit(1)

    if not site_dimensions:
        print("Error: No site_dimensions_um found in fabric.yaml")
        sys.exit(1)

    if not tile_definition:
        print("Error: No tile_definition found in fabric.yaml")
        sys.exit(1)

    # Build cell dimensions lookup
    site_width = site_dimensions.get("width", 0.46)
    site_height = site_dimensions.get("height", 2.72)

    cell_dimensions = {}
    for raw_key, cell_info in cell_definitions.items():
        # Normalize malformed keys
        cell_type = re.sub(r"[:\s'\{].*", "", raw_key).strip()

        # If key contains inline width data, extract it directly
        if not isinstance(cell_info, dict):
            # Attempt to extract numeric width_sites from malformed strings
            match = re.search(r"width_sites[^\d]*(\d+)", raw_key)
            if not match:
                match = re.search(r"width_sites[^\d]*(\d+)", str(cell_info))
            if match:
                width_sites = int(match.group(1))
                cell_info = {"width_sites": width_sites}
            # Fallback for known TAP cells
            elif "tapvpwrvgnd" in cell_type.lower():
                cell_info = {"width_sites": 1}
            else:
                print(f"Warning: Unexpected format for {cell_type}: {cell_info}")
                continue

        width_sites = cell_info.get("width_sites")
        if width_sites is None:
            # Handle TAP cells explicitly if still missing
            if "tapvpwrvgnd" in cell_type.lower():
                width_sites = 1
            else:
                print(f"Warning: No width_sites found for {cell_type}")
                continue

        cell_dimensions[cell_type] = {
            "width_sites": int(width_sites),
            "width_um": round(int(width_sites) * site_width, 2),
            "height_um": site_height
        }

    print(f"Processed {len(cell_dimensions)} cell definitions")

    # Build template_name to cell_type mapping from tile_definition
    template_to_cell_type = {}
    for cell in tile_definition.get("cells", []):
        template_name = cell.get("template_name")
        cell_type = cell.get("cell_type")
        if template_name and cell_type:
            template_to_cell_type[template_name] = cell_type

    print(f"Mapped {len(template_to_cell_type)} template names to cell types")

    # Enrich cells_by_tile with cell_type and dimensions
    enriched_cells_by_tile = {}
    template_pattern = re.compile(r'(R[0-3]_.+)')

    for tile_key, tile_data in cells_by_tile.items():
        enriched_cells = []
        for cell in tile_data.get("cells", []):
            # Handle both dict and string formats
            if isinstance(cell, dict):
                cell_name = cell.get("name") or cell.get("template_name", "")
                enriched_cell = cell.copy()
            else:
                cell_name = str(cell)
                enriched_cell = {"name": cell_name}

            # Extract template name using regex
            match = template_pattern.search(cell_name)
            if not match:
                enriched_cells.append(enriched_cell)
                continue

            template_name = match.group(1)
            enriched_cell["template_name"] = template_name

            # Look up cell_type from template_name
            if template_name in template_to_cell_type:
                cell_type = template_to_cell_type[template_name]
                enriched_cell["cell_type"] = cell_type

                # Add dimensions if available
                if cell_type in cell_dimensions:
                    dims = cell_dimensions[cell_type]
                    enriched_cell["width_sites"] = dims["width_sites"]
                    enriched_cell["width_um"] = dims["width_um"]
                    enriched_cell["height_um"] = dims["height_um"]
                else:
                    # Provide fallback for missing TAP cell types
                    if "tapvpwrvgnd" in cell_type.lower():
                        enriched_cell["width_sites"] = 1
                        enriched_cell["width_um"] = round(1 * site_width, 2)
                        enriched_cell["height_um"] = site_height
                    else:
                        print(f"Warning: No dimensions found for cell type '{cell_type}'")
            else:
                print(f"Warning: No cell_type mapping found for template '{template_name}'")

            enriched_cells.append(enriched_cell)

        enriched_cells_by_tile[tile_key] = {"cells": enriched_cells}

    # Merge into unified DB
    fabric_db = {
        "fabric": {
            "cells_by_tile": enriched_cells_by_tile,
            "pin_placement": pin_placement,
            "site_dimensions_um": site_dimensions
        }
    }

    return fabric_db


if __name__ == "__main__":
    fabric_cells_file = "fabric/fabric_cells.yaml"
    pins_file = "fabric/pins.yaml"
    fabric_def_file = "fabric/fabric.yaml"
    output_file = "fabric/fabric_db.yaml"

    db = build_fabric_db(fabric_cells_file, pins_file, fabric_def_file)

    # Save as YAML
    with open(output_file, "w") as f:
        yaml.dump(db, f, sort_keys=False, default_flow_style=False, indent=2)
    print(f"Fabric database written to {output_file}")

    # Save as JSON
    with open("fabric/fabric_db.json", "w") as f:
        json.dump(db, f, indent=2)
    print("Fabric database also saved as fabric_db.json")

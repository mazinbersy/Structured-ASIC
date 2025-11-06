# build_fabric_db.py
import yaml
import json
import sys

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

def build_fabric_db(fabric_cells_path, pins_path):
    """Merges fabric cell placement and pin placement data."""
    fabric_cells_data = load_yaml(fabric_cells_path)
    pins_data = load_yaml(pins_path)

    # Extract key data
    cells_by_tile = fabric_cells_data.get("fabric_cells_by_tile", {}).get("tiles", {})
    pin_placement = pins_data.get("pin_placement", {})

    # Merge into unified DB
    fabric_db = {
        "fabric": {
            "cells_by_tile": cells_by_tile,
            "pin_placement": pin_placement
        }
    }

    return fabric_db

if __name__ == "__main__":
    fabric_cells_file = "fabric/fabric_cells.yaml"
    pins_file = "fabric/pins.yaml"
    output_file = "fabric/fabric_db.yaml"

    db = build_fabric_db(fabric_cells_file, pins_file)

    # Save as YAML
    with open(output_file, "w") as f:
        yaml.dump(db, f, sort_keys=False)
    print(f"Fabric database written to {output_file}")

    # Save as JSON for scripts
    with open("fabric/fabric_db.json", "w") as f:
        json.dump(db, f, indent=2)
    print("Fabric database also saved as fabric_db.json")

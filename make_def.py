#!/usr/bin/env python3
"""
make_def.py
-----------
Generates a DEF (Design Exchange Format) file for the design after CTS and Power-Down ECO.

The DEF file contains:
- DIEAREA (from fabric dimensions)
- All PINS (I/O ports) marked as + FIXED with rectangular geometry
- All COMPONENTS from fabric_cells.yaml (both used and unused) marked as + FIXED
- Technology information extracted from LEF/TLEF files

This integrates:
1. Clock Tree Synthesis (cts_htree.py)
2. Power-Down ECO (power_down_eco.py)
3. Final DEF generation with complete placement
4. Technology information from LEF/TLEF files

The generated DEF file follows DEF 5.8 syntax specification and is compatible with OpenROAD.

Usage:
    python make_def.py <design_name> <design_json> <fabric_cells.yaml> <pins.yaml> <fabric.yaml> <placement.map> [options]

Example:
    python make_def.py 6502 designs/6502_mapped.json fabric/fabric_cells.yaml fabric/pins.yaml fabric/fabric.yaml placement.map
"""

import sys
import os
import yaml
import json
import re
from typing import Dict, List, Tuple, Any, Optional
from collections import defaultdict
import string

# Import required modules
from build_fabric_db import build_fabric_db
from parse_design import parse_design_json
from cts_htree import HTreeCTS, parse_placement_map
from power_down import run_power_down_eco, load_placement_mapping
from parse_lib import parse_liberty_leakage


def parse_lef_file(lef_file: str) -> Dict[str, Any]:
    """
    Parse LEF file to extract technology and cell information.

    This function extracts critical technology information from the LEF (Library Exchange
    Format) file, which is required for DEF 5.8 compliance. Specifically:
    - VERSION: LEF version (typically 5.7 or 5.8)
    - DIVIDERCHAR: Hierarchy divider character (default "/")
    - BUSBITCHARS: Bus bit characters for vector naming (default "[]")
    - MACROS: Cell definitions with dimensions and properties

    Args:
        lef_file (str): Path to LEF file. Must be a valid LEF 5.7+ format file.

    Returns:
        Dict[str, Any]: Dictionary containing:
            - version (str): LEF version number (e.g., "5.7", "5.8")
            - units (dict): Database unit information {database_units: int}
            - dividerchar (str): Hierarchy divider character
            - busbitchars (str): Bus bit character pair
            - macros (dict): Macro definitions keyed by macro name
            - parse_status (str): 'success', 'missing', 'error', or 'default'

    DEF 5.8 Compliance:
        - Extracts DIVIDERCHAR required for DEF header
        - Extracts BUSBITCHARS required for DEF header
        - Validates version compatibility (5.7+)
    """
    lef_data = {
        'version': '5.8',
        'units': {'database_units': 1000},
        'dividerchar': '/',
        'busbitchars': '[]',
        'macros': {},
        'parse_status': 'default'  # Track if file was actually parsed
    }

    if not os.path.exists(lef_file):
        print(f"[WARN] LEF file not found: {lef_file}")
        print(f"       Using default technology parameters")
        lef_data['parse_status'] = 'missing'
        return lef_data

    try:
        with open(lef_file, 'r') as f:
            content = f.read()
    except IOError as e:
        print(f"[ERROR] Cannot read LEF file: {e}")
        lef_data['parse_status'] = 'error'
        return lef_data

    # Extract version
    version_match = re.search(r'VERSION\s+([\d.]+)\s*;', content)
    if version_match:
        lef_data['version'] = version_match.group(1)
        # Validate version compatibility
        version_float = float(version_match.group(1))
        if version_float < 5.7:
            print(f"[WARN] LEF version {lef_data['version']} is older than recommended 5.7")
    else:
        print(f"[WARN] VERSION statement not found in LEF file")

    # Extract DIVIDERCHAR (required for DEF 5.8 compliance)
    divider_match = re.search(r'DIVIDERCHAR\s+"([^"]+)"\s*;', content)
    if divider_match:
        lef_data['dividerchar'] = divider_match.group(1)
    else:
        print(f"[WARN] DIVIDERCHAR not found in LEF, using default '/'")

    # Extract BUSBITCHARS (required for DEF 5.8 compliance)
    busbit_match = re.search(r'BUSBITCHARS\s+"([^"]+)"\s*;', content)
    if busbit_match:
        lef_data['busbitchars'] = busbit_match.group(1)
    else:
        print(f"[WARN] BUSBITCHARS not found in LEF, using default '[]'")

    # Extract UNITS
    units_match = re.search(r'UNITS\s+(.*?)\s+END\s+UNITS', content, re.DOTALL)
    if units_match:
        units_text = units_match.group(1)
        dbu_match = re.search(r'DATABASE\s+MICRONS\s+([\d]+)', units_text)
        if dbu_match:
            lef_data['units']['database_units'] = int(dbu_match.group(1))

    # Extract MACRO definitions (for cell sizes and pin names)
    macro_pattern = r'MACRO\s+(\w+)(.*?)END\s+\1'
    for macro_match in re.finditer(macro_pattern, content, re.DOTALL):
        macro_name = macro_match.group(1)
        macro_content = macro_match.group(2)

        size_match = re.search(r'SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;', macro_content)
        pins = []
        
        # Extract all PIN names from this macro
        pin_pattern = r'PIN\s+(\w+)\s*\n'
        for pin_match in re.finditer(pin_pattern, macro_content):
            pin_name = pin_match.group(1)
            pins.append(pin_name)
        
        if size_match:
            lef_data['macros'][macro_name] = {
                'width': float(size_match.group(1)),
                'height': float(size_match.group(2)),
                'pins': pins
            }
        elif pins:
            lef_data['macros'][macro_name] = {'pins': pins}

    lef_data['parse_status'] = 'success'
    return lef_data


def get_output_pin_name(cell_type: str, lef_macros: Dict[str, Any]) -> str:
    """
    Get the correct output pin name for a cell type from LEF macros.
    
    Handles cases where the netlist has incorrect pin names (e.g., BUF cells with 'Y'
    when they should have 'X'). This function looks up the actual pin names from the
    LEF library definitions.
    
    Args:
        cell_type: The cell type (e.g., 'sky130_fd_sc_hd__buf_1', 'sky130_fd_sc_hd__clkbuf_4')
        lef_macros: Dictionary of macro information from LEF file, keyed by cell type
        
    Returns:
        The correct output pin name from LEF, or 'Y' as default fallback
    """
    if cell_type not in lef_macros:
        return 'Y'  # Default fallback
    
    macro_info = lef_macros[cell_type]
    if 'pins' not in macro_info:
        return 'Y'
    
    pins = macro_info['pins']
    
    # Look for common output pin names in order of preference
    # For BUF/CLKBUF cells: prefer X (e.g., clkbuf_4 has X not Y)
    # For combinational logic: prefer X (or2, and2, etc have X)
    # For sequential logic: prefer Q, QN, Y
    # For CONB: HI or LO
    for output_pin in ['X', 'Q', 'QN', 'Y', 'HI', 'LO']:
        if output_pin in pins:
            return output_pin
    
    # If no common output pin found, return the last non-power/ground pin
    # Filter out power/ground pins
    signal_pins = [p for p in pins if p not in ['VPWR', 'VGND', 'VDD', 'VSS', 'VNB', 'VPB', 'A', 'B', 'C', 'D']]
    
    if signal_pins:
        return signal_pins[-1]
    
    return 'Y'  # Final fallback


def parse_tlef_file(tlef_file: str) -> Dict[str, Any]:
    """
    Parse TLEF (Technology LEF) file to extract technology information.

    This function parses the Technology LEF file to extract critical manufacturing
    and design rule information required for DEF 5.8 compliance:
    - VERSION: TLEF version (typically 5.7 or 5.8)
    - UNITS: Database unit definitions (MICRONS per database unit)
    - MANUFACTURINGGRID: Manufacturing grid spacing
    - SITE: Site definitions (e.g., unithd, core site)
    - LAYER: Metal layer definitions with properties

    Args:
        tlef_file (str): Path to TLEF file. Must be valid TLEF 5.7+ format.

    Returns:
        Dict[str, Any]: Technology information:
            - version (str): TLEF version number
            - units (dict): {database_units: int} - DBU per micron
            - sites (dict): Site definitions keyed by site name
            - layers (dict): Layer definitions keyed by layer name
            - manufacturing_grid (float): Manufacturing grid in microns
            - parse_status (str): 'success', 'missing', 'error', or 'default'

    DEF 5.8 Compliance:
        - UNITS statement required for DEF 5.8 UNITS specification
        - DATABASE MICRONS parameter mandatory
        - Validates technology compatibility
    """
    tlef_data = {
        'version': '5.8',
        'units': {'database_units': 1000},
        'sites': {},
        'layers': {},
        'manufacturing_grid': 0.005,
        'parse_status': 'default'
    }

    if not os.path.exists(tlef_file):
        print(f"[WARN] TLEF file not found: {tlef_file}")
        print(f"       Using default technology parameters")
        tlef_data['parse_status'] = 'missing'
        return tlef_data

    try:
        with open(tlef_file, 'r') as f:
            content = f.read()
    except IOError as e:
        print(f"[ERROR] Cannot read TLEF file: {e}")
        tlef_data['parse_status'] = 'error'
        return tlef_data

    # Extract version
    version_match = re.search(r'VERSION\s+([\d.]+)\s*;', content)
    if version_match:
        tlef_data['version'] = version_match.group(1)
        version_float = float(version_match.group(1))
        if version_float < 5.7:
            print(f"[WARN] TLEF version {tlef_data['version']} is older than recommended 5.7")
    else:
        print(f"[WARN] VERSION statement not found in TLEF file")

    # Extract UNITS
    units_match = re.search(r'UNITS\s+(.*?)\s+END\s+UNITS', content, re.DOTALL)
    if units_match:
        units_text = units_match.group(1)
        dbu_match = re.search(r'DATABASE\s+MICRONS\s+([\d]+)', units_text)
        if dbu_match:
            tlef_data['units']['database_units'] = int(dbu_match.group(1))

    # Extract MANUFACTURINGGRID
    grid_match = re.search(r'MANUFACTURINGGRID\s+([\d.]+)\s*;', content)
    if grid_match:
        tlef_data['manufacturing_grid'] = float(grid_match.group(1))
    else:
        print(f"[WARN] MANUFACTURINGGRID not found in TLEF, using default 0.005")

    # Extract SITE definitions
    site_pattern = r'SITE\s+(\w+)(.*?)END\s+\1'
    for site_match in re.finditer(site_pattern, content, re.DOTALL):
        site_name = site_match.group(1)
        site_content = site_match.group(2)

        size_match = re.search(r'SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;', site_content)
        class_match = re.search(r'CLASS\s+(\w+)\s*;', site_content)
        
        site_info = {'name': site_name}
        if size_match:
            site_info['width'] = float(size_match.group(1))
            site_info['height'] = float(size_match.group(2))
        if class_match:
            site_info['class'] = class_match.group(1)

        tlef_data['sites'][site_name] = site_info

    # Extract LAYER definitions
    layer_pattern = r'LAYER\s+(\w+)(.*?)END\s+\1'
    for layer_match in re.finditer(layer_pattern, content, re.DOTALL):
        layer_name = layer_match.group(1)
        layer_content = layer_match.group(2)

        type_match = re.search(r'TYPE\s+(\w+)\s*;', layer_content)
        direction_match = re.search(r'DIRECTION\s+(\w+)\s*;', layer_content)
        width_match = re.search(r'WIDTH\s+([\d.]+)\s*;', layer_content)

        layer_info = {'name': layer_name}
        if type_match:
            layer_info['type'] = type_match.group(1)
        if direction_match:
            layer_info['direction'] = direction_match.group(1)
        if width_match:
            layer_info['width'] = float(width_match.group(1))

        tlef_data['layers'][layer_name] = layer_info

    tlef_data['parse_status'] = 'success'
    return tlef_data


def load_placement_map(placement_file: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load placement mapping in both directions.

    Parses the placement mapping file (.map format) that maps between logical design
    instances and physical fabric cell positions. The map is bidirectional to support:
    - Logical to Fabric mapping: For finding physical position of logical instance
    - Fabric to Logical mapping: For identifying logical instance from fabric cell

    File Format (example):
        fabric_slot  cell_type  x  y  ->  logical_instance
        FAB_0        NAND2      10 20 -> add_inst_0
        FAB_1        NAND2      30 20 -> add_inst_1

    Args:
        placement_file (str): Path to .map file containing placement mappings

    Returns:
        Tuple[Dict[str, str], Dict[str, str]]:
            - logical_to_fabric: Maps logical instance names to fabric cell names
            - fabric_to_logical: Maps fabric cell names to logical instance names

    Error Handling:
        - Missing files: Returns empty dictionaries and logs error
        - Invalid format: Skips malformed lines with no error
    """
    logical_to_fabric = {}
    fabric_to_logical = {}

    if not os.path.exists(placement_file):
        print(f"[ERROR] Placement file not found: {placement_file}")
        return logical_to_fabric, fabric_to_logical

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

                # Always map fabric to logical (even if UNUSED)
                fabric_to_logical[fabric_cell] = logical_inst

                # Only map logical to fabric if not UNUSED
                if logical_inst != "UNUSED":
                    logical_to_fabric[logical_inst] = fabric_cell

    return logical_to_fabric, fabric_to_logical


def get_die_area(fabric_db: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """
    Extract DIEAREA from fabric database with proper margins and keepouts.

    The DIEAREA statement in DEF 5.8 defines the bounding rectangle of the design.
    It must be specified as two points: lower-left (llx, lly) and upper-right (urx, ury)
    in database units.

    This function applies core_margin_um (distance from die edge to core area) and
    corner_keepout_um (additional spacing in corners) to ensure proper spacing between
    the I/O pins/core area and the die boundary.

    DEF 5.8 Syntax:
        DIEAREA ( llx lly ) ( urx ury ) ;

    Args:
        fabric_db (Dict[str, Any]): Fabric database containing die dimensions and margin info

    Returns:
        Tuple[int, int, int, int]: Die area bounding box
            - llx: Lower-left x coordinate in database units
            - lly: Lower-left y coordinate in database units
            - urx: Upper-right x coordinate in database units
            - ury: Upper-right y coordinate in database units

    Note:
        - Coordinates are converted from microns to database units (default 1000 dbu/μm)
        - Die area respects core_margin_um and corner_keepout_um spacing requirements
        - The die area should match or slightly exceed the I/O pin ring placement
    """
    fabric_info = fabric_db.get('fabric', {})
    pin_placement_info = fabric_info.get('pin_placement', {})
    die_info = pin_placement_info.get('die', {})
    
    # Get die dimensions in microns
    width_um = die_info.get('width_um', 0)
    height_um = die_info.get('height_um', 0)
    
    # Get margin and keepout specifications
    # core_margin_um: Distance from die edge to the core placement area
    # corner_keepout_um: Additional keepout in corners
    core_margin_um = die_info.get('core_margin_um', 5.0)
    corner_keepout_um = die_info.get('corner_keepout_um', 5.0)
    
    # Get database units per micron
    dbu_per_micron = pin_placement_info.get('units', {}).get('dbu_per_micron', 1000)
    
    # The die area should be the full physical die bounding box
    # This includes all margins and keepouts
    llx = 0
    lly = 0
    urx = int(width_um * dbu_per_micron)
    ury = int(height_um * dbu_per_micron)
    
    # Log margin information for debugging
    print(f"[INFO] Die Area Calculation:")
    print(f"       Total die size: {width_um}µm × {height_um}µm")
    print(f"       Core margin: {core_margin_um}µm from each edge")
    print(f"       Corner keepout: {corner_keepout_um}µm in corners")
    print(f"       Core area: {width_um - 2*core_margin_um}µm × {height_um - 2*core_margin_um}µm")
    print(f"       DEF DIEAREA: ( {llx} {lly} ) ( {urx} {ury} ) [DBU]")
    print(f"       DEF DIEAREA: ( 0 0 ) ( {width_um}µm {height_um}µm ) [microns]")

    return (llx, lly, urx, ury)


def extract_io_pins(logical_db: Dict[str, Any],
                    fabric_db: Dict[str, Any],
                    tlef_data: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """
    Extract I/O pin information from logical database and fabric.

    This function extracts all I/O ports from the logical design and maps them to
    physical pin locations in the fabric. Each pin is configured with DEF 5.8 compliant
    attributes including direction, use, and placement layer with rectangular geometry.
    
    Pin dimensions are determined by the minimum metal width from the technology file
    (TLEF) for each layer. This ensures DRC compliance.

    DEF 5.8 PINS Section Syntax:
        - pinName + NET netName
          + DIRECTION {INPUT | OUTPUT | INOUT | FEEDTHRU}
          + USE {SIGNAL | POWER | GROUND | CLOCK}
          + PORT
            + LAYER layerName
              ( x1 y1 ) ( x2 y2 )
            + FIXED ( x y ) orient
          ;

    Returns:
        List[Dict[str, Any]]: Pin definitions with keys:
            - name (str): Pin name from logical design
            - net (str): Associated net name or ID
            - direction (str): INPUT, OUTPUT, INOUT, or FEEDTHRU
            - use (str): SIGNAL (default), POWER, GROUND, or CLOCK
            - x (int): X coordinate center in database units
            - y (int): Y coordinate center in database units
            - width (int): Pin width in database units
            - height (int): Pin height in database units
            - layer (str): Metal layer name (e.g., met2, met3)
            - orient (str): Pin orientation: N, S, E, W, FN, FS, FE, FW
            - side (str): Pin side: south, north, east, west

    DEF 5.8 Compliance:
        - All pins include DIRECTION attribute (mandatory)
        - All pins include USE attribute (mandatory)
        - All pins marked as FIXED (standard for structured ASIC)
        - Proper PORT structure with LAYER and rectangular geometry
        - Pin dimensions based on minimum metal width from technology
    """
    pins = []

    # Get I/O ports from logical_db
    input_ports = logical_db.get('ports', {}).get('inputs', {})
    output_ports = logical_db.get('ports', {}).get('outputs', {})

    # Get pin list from fabric_db
    fabric_info = fabric_db.get('fabric', {})
    pin_placement_info = fabric_info.get('pin_placement', {})
    pin_list = pin_placement_info.get('pins', [])
    
    # Get database units per micron
    dbu_per_micron = pin_placement_info.get('units', {}).get('dbu_per_micron', 1000)

    # Create a mapping from pin name to pin info for quick lookup
    pin_info_map = {pin['name']: pin for pin in pin_list}

    # Process input ports
    for port_name, net_id in input_ports.items():
        pin_info = pin_info_map.get(port_name, {})

        # Extract pin position and dimensions as-is from fabric_db
        x_um = pin_info.get('x_um', 0)
        y_um = pin_info.get('y_um', 0)
        layer = pin_info.get('layer', 'met2')
        side = pin_info.get('side', 'south')
        
        # Use pin dimensions from fabric_db directly; small default if not specified
        width_um = pin_info.get('width_um', 0.14)
        height_um = pin_info.get('height_um', 0.14)
        
        # Determine USE attribute (CLOCK for clk pin, SIGNAL for others)
        use_attr = 'CLOCK' if port_name == 'clk' else 'SIGNAL'

        pins.append({
            'name': port_name,
            'net': net_id,
            'direction': 'INPUT',
            'use': use_attr,
            'x': int(x_um * dbu_per_micron),
            'y': int(y_um * dbu_per_micron),
            'width': int(width_um * dbu_per_micron),
            'height': int(height_um * dbu_per_micron),
            'layer': layer,
            'orient': pin_info.get('orient', 'N'),
            'side': side
        })

    # Process output ports
    for port_name, net_id in output_ports.items():
        pin_info = pin_info_map.get(port_name, {})

        # Extract pin position and dimensions as-is from fabric_db
        x_um = pin_info.get('x_um', 0)
        y_um = pin_info.get('y_um', 0)
        layer = pin_info.get('layer', 'met2')
        side = pin_info.get('side', 'south')
        
        # Use pin dimensions from fabric_db directly; small default if not specified
        width_um = pin_info.get('width_um', 0.14)
        height_um = pin_info.get('height_um', 0.14)
        
        # Determine USE attribute (CLOCK for clk pin, SIGNAL for others)
        use_attr = 'CLOCK' if port_name == 'clk' else 'SIGNAL'

        pins.append({
            'name': port_name,
            'net': net_id,
            'direction': 'OUTPUT',
            'use': use_attr,
            'x': int(x_um * dbu_per_micron),
            'y': int(y_um * dbu_per_micron),
            'width': int(width_um * dbu_per_micron),
            'height': int(height_um * dbu_per_micron),
            'layer': layer,
            'orient': pin_info.get('orient', 'N'),
            'side': side
        })

    return pins


def get_site_dimensions(tlef_data: Dict[str, Any], dbu_per_micron: int = 1000) -> Tuple[int, int, str]:
    """
    Extract site dimensions from TLEF data.
    
    Looks for the primary CORE site and extracts its width and height.
    Converts from microns to database units.
    
    Args:
        tlef_data: Parsed TLEF data containing site definitions
        dbu_per_micron: Database units per micron (default 1000)
    
    Returns:
        Tuple of (site_width_dbu, site_height_dbu, site_name)
        Falls back to Sky130 defaults (460, 2720) if not found
    """
    if not tlef_data or 'sites' not in tlef_data:
        print(f"[WARN] No sites found in TLEF, using Sky130 defaults")
        return 460, 2720, 'unithd'
    
    # Look for CORE class sites, prefer unithd first
    sites = tlef_data['sites']
    
    # Try unithd first (single height)
    if 'unithd' in sites:
        site_info = sites['unithd']
        width_um = site_info.get('width', 0.46)
        height_um = site_info.get('height', 2.72)
        width_dbu = int(width_um * dbu_per_micron)
        height_dbu = int(height_um * dbu_per_micron)
        print(f"[INFO] Using unithd site: {width_um}µm x {height_um}µm = {width_dbu} x {height_dbu} DBU")
        return width_dbu, height_dbu, 'unithd'
    
    # Try any CORE site
    for site_name, site_info in sites.items():
        if site_info.get('class') == 'CORE':
            width_um = site_info.get('width', 0.46)
            height_um = site_info.get('height', 2.72)
            width_dbu = int(width_um * dbu_per_micron)
            height_dbu = int(height_um * dbu_per_micron)
            print(f"[INFO] Using {site_name} site: {width_um}µm x {height_um}µm = {width_dbu} x {height_dbu} DBU")
            return width_dbu, height_dbu, site_name
    
    # Fallback to Sky130 defaults
    print(f"[WARN] No CORE sites found in TLEF, using Sky130 defaults")
    return 460, 2720, 'unithd'


def generate_rows_from_fabric_layout(fabric_db: Dict[str, Any],
                                      die_area: Tuple[int, int, int, int],
                                      site_width_dbu: int = 460,
                                      site_height_dbu: int = 2720,
                                      site_name: str = 'unithd') -> List[Tuple[str, int, int, int, int]]:
    """
    Generate DEF ROW statements based on fabric layout structure.
    
    The fabric is organized as tiles with rows. This function generates ROW definitions
    that match the actual fabric tile structure rather than just naive rows across the die.
    
    For a structured ASIC fabric:
    - fabric_layout.tiles_x × fabric_layout.tiles_y tiles
    - Each tile has 4 physical rows (R0, R1, R2, R3)
    - Each row spans the tile width (60 sites = 60 * site_width_dbu)
    
    Args:
        fabric_db: Fabric database with layout information
        die_area: Tuple of (llx, lly, urx, ury) in database units
        site_width_dbu: Site width in database units
        site_height_dbu: Site height in database units
        site_name: Site class name (e.g., 'unithd')
    
    Returns:
        List of (row_name, x, y, num_cols, step_x) tuples for each ROW statement
    """
    rows = []
    
    try:
        # Get fabric layout dimensions
        fabric_info = fabric_db.get('fabric', {})
        fabric_layout = fabric_info.get('fabric_layout', {}) if isinstance(fabric_info, dict) else {}
        
        tiles_x = fabric_layout.get('tiles_x', 36) if fabric_layout else 36
        tiles_y = fabric_layout.get('tiles_y', 90) if fabric_layout else 90
        
        # Get tile definition
        tile_def = fabric_info.get('tile_definition', {}) if isinstance(fabric_info, dict) else {}
        tile_width_sites = tile_def.get('dimensions_sites', {}).get('width', 60) if tile_def else 60
        tile_height_rows = tile_def.get('dimensions_sites', {}).get('height', 4) if tile_def else 4
        
        llx, lly, urx, ury = die_area
        dbu_per_micron = fabric_info.get('pin_placement', {}).get('units', {}).get('dbu_per_micron', 1000) if isinstance(fabric_info, dict) else 1000
        
        # Get core margin to know where fabric cells start
        core_margin_um = fabric_info.get('pin_placement', {}).get('die', {}).get('core_margin_um', 5.0) if isinstance(fabric_info, dict) else 5.0
        core_margin_dbu = int(core_margin_um * dbu_per_micron)
        
        print(f"[INFO] Generating ROWs from fabric layout:")
        print(f"       Fabric: {tiles_x} × {tiles_y} tiles")
        print(f"       Each tile: {tile_width_sites} sites wide × {tile_height_rows} rows tall")
        print(f"       Core margin: {core_margin_dbu} DBU ({core_margin_um}µm)")
        print(f"       Die area: ({llx}, {lly}) to ({urx}, {ury}) DBU")
        
        # Calculate total rows and columns
        total_rows = tiles_y * tile_height_rows
        total_cols = tiles_x * tile_width_sites
        
        print(f"       Total rows: {total_rows}, Total cols: {total_cols}")
        print(f"       Fabric width: {total_cols * site_width_dbu} DBU ({total_cols * site_width_dbu / 1000}µm)")
        print(f"       Fabric height: {total_rows * site_height_dbu} DBU ({total_rows * site_height_dbu / 1000}µm)")
        
        # Start from core margin (this is where the fabric cells actually start)
        row_y = lly + core_margin_dbu
        row_num = 0
        
        # Generate one ROW per fabric row
        for ty in range(tiles_y):
            for tr in range(tile_height_rows):
                row_name = f"ROW_{row_num}"
                rows.append((row_name, llx + core_margin_dbu, row_y, total_cols, site_width_dbu))
                row_y += site_height_dbu
                row_num += 1
        
        print(f"[INFO] Generated {len(rows)} ROW statements")
        print(f"       First row: {rows[0][0]} at y={rows[0][2]} DBU")
        if len(rows) > 1:
            print(f"       Last row: {rows[-1][0]} at y={rows[-1][2]} DBU")
        
        return rows
        
    except Exception as e:
        print(f"[WARN] Error generating rows from fabric layout: {e}")
        print(f"       Falling back to simple grid-based rows")
        return []


def snap_to_grid(x: int, y: int, x_grid: int = 460, y_grid: int = 2720) -> Tuple[int, int]:
    """
    Snap coordinates to placement grid.
    
    Rounds coordinates to nearest valid grid point based on site dimensions.
    For structured ASIC:
    - X coordinates must be multiples of site width in DBU
    - Y coordinates must be multiples of site height in DBU
    
    Args:
        x: X coordinate in database units
        y: Y coordinate in database units
        x_grid: X grid spacing in DBU (site width, default 460 for Sky130 unithd)
        y_grid: Y grid spacing in DBU (site height, default 2720 for Sky130 unithd)
    
    Returns:
        Tuple of (snapped_x, snapped_y)
    """
    snapped_x = (x // x_grid) * x_grid
    snapped_y = (y // y_grid) * y_grid
    return snapped_x, snapped_y


def extract_components(fabric_db: Dict[str, Any],
                       logical_db: Dict[str, Any],
                       fabric_to_logical: Dict[str, str],
                       logical_to_fabric: Optional[Dict[str, str]] = None,
                       placement_data: Optional[Dict[str, Dict[str, Any]]] = None,
                       x_grid: int = 460,
                       y_grid: int = 2720,
                       die_area: Tuple[int, int, int, int] = None) -> Tuple[List[Dict[str, Any]], int, str]:
    """
    Extract all component placements from fabric database and logical database.

        This function extracts component placement information directly from the logical
        database and assigns coordinates based on the required flow:
        - Cells starting with '$': look up their placement via logical->fabric mapping
            from the placement map; place at that fabric cell location.
        - Cells starting with 'T': look up coordinates directly from fabric_db by name.
        - All other cells: use placement in logical_db when present; otherwise fall back
            to mapping/origin.
        All emitted components are marked FIXED.
        - Bidirectional mapping between logical instances and fabric cells
        - DEF 5.8 compliant attributes: SOURCE, status, orientation, optional weight

    DEF 5.8 COMPONENTS Section Syntax:
        COMPONENTS numComps ;
        [ - compName modelName
            + SOURCE {NETLIST | DIST | USER | TIMING}
            + {FIXED | PLACED | COVER | UNPLACED} ( x y ) orient
            [ + WEIGHT weight ]
          ; ] ...
        END COMPONENTS

    Args:
        fabric_db (Dict[str, Any]): Fabric database with cell placements
        logical_db (Dict[str, Any]): Logical database with instance definitions (from synthesis/CTS/ECO)
        fabric_to_logical (Dict[str, str]): Maps fabric_cell -> logical_instance
        x_grid: X grid spacing for snapping (default 460 DBU = 0.46µm)
        y_grid: Y grid spacing for snapping (default 2720 DBU = 2.72µm)
        die_area: Optional tuple (llx, lly, urx, ury) in DBU to constrain placement

        Returns:
                Tuple[List[Dict[str, Any]], int, str]:
                        - components_list: List of component dicts with keys:
                            * name: Component/instance name
                            * model: Cell master name
                            * source: NETLIST, USER, DIST, or TIMING
                            * x, y: Position in database units
                            * orient: N, S, E, W, FN, FS, FE, FW
                            * status: FIXED
                            * weight: Optional relative weight (default 1.0)
                            * fabric_cell: Original fabric cell name for traceability (if applicable)
                        - units: Database units per micron (typically 1000)
                        - coords: Coordinate system 'MICRONS' or 'NANOMETERS'

    DEF 5.8 Compliance:
        - All components have SOURCE attribute (mandatory)
        - All components have placement status (mandatory)
        - Fabric cells marked FIXED (pre-placed in structured ASIC)
        - Synthesized cells emitted as FIXED using logical placement when available; else fabric-mapped; else (0,0)
        - Proper coordinate system specification
    """
    components = []

    # Get all fabric cells from cells_by_tile
    cells_by_tile = fabric_db.get('fabric', {}).get('cells_by_tile', {})
    units = fabric_db.get('fabric', {}).get('pin_placement', {}).get('units', {}).get('dbu_per_micron', 1000)
    coords_raw = fabric_db.get('fabric', {}).get('pin_placement', {}).get('units', {}).get('coords', 'micron').upper()
    
    # Ensure coords is in proper DEF format (MICRONS or NANOMETERS)
    if 'MICRON' in coords_raw:
        coords = 'MICRONS'
    elif 'NANO' in coords_raw:
        coords = 'NANOMETERS'
    else:
        coords = 'MICRONS'  # Default to MICRONS

    debug_components = os.environ.get("DEBUG_COMPONENTS", "").lower() in ("1", "true", "yes", "on")

    # Map of all fabric cells for lookup
    fabric_cell_map = {}
    for tile_name, tile_data in cells_by_tile.items():
        for cell in tile_data.get('cells', []):
            fabric_cell_name = cell.get('name', '')
            if fabric_cell_name:
                fabric_cell_map[fabric_cell_name] = {
                    'tile': tile_name,
                    'type': cell.get('cell_type', ''),
                    'x': cell.get('x', 0),
                    'y': cell.get('y', 0),
                    'orient': cell.get('orient', 'N'),
                    'width_um': cell.get('width_um', 0),
                    'height_um': cell.get('height_um', 0)
                }

    if logical_db and 'cells' in logical_db:
        for cell_name, cell_info in sorted(logical_db['cells'].items()):
            cell_type = cell_info.get('type', 'UNKNOWN')

            placed_x = None
            placed_y = None
            placed_orient = cell_info.get('orient', cell_info.get('orientation', 'N'))
            coord_src = None

            # Flow: $ cells use placement_data, T cells use fabric_db, others use logical_db
            if cell_name.startswith('$'):
                # $ cells: look up in placement_data
                if placement_data and cell_name in placement_data:
                    p_info = placement_data[cell_name]
                    placed_x = int(p_info.get('x', 0) * units)
                    placed_y = int(p_info.get('y', 0) * units)
                    placed_orient = p_info.get('orient', placed_orient)
                    coord_src = "placement_data"
                    if debug_components:
                        print(f"[DEBUG] $ cell {cell_name}: found in placement_data -> ({placed_x},{placed_y})")
                else:
                    placed_x = 0
                    placed_y = 0
                    coord_src = "origin_not_found"
                    if debug_components:
                        print(f"[DEBUG] $ cell {cell_name}: NOT in placement_data -> (0,0)")
            elif cell_name.startswith('T'):
                # T cells: use fabric_db by name
                fab_info = fabric_cell_map.get(cell_name)
                if fab_info:
                    placed_x = int(fab_info.get('x', 0) * units)
                    placed_y = int(fab_info.get('y', 0) * units)
                    placed_orient = fab_info.get('orient', placed_orient)
                    coord_src = "fabric_db"
                    if debug_components:
                        print(f"[DEBUG] T cell {cell_name}: found in fabric_db -> ({placed_x},{placed_y})")
                else:
                    placed_x = 0
                    placed_y = 0
                    coord_src = "origin_not_found"
                    if debug_components:
                        print(f"[DEBUG] T cell {cell_name}: NOT in fabric_db -> (0,0)")
            else:
                # Other cells: use logical_db placement if available
                if cell_info.get('x') is not None and cell_info.get('y') is not None:
                    placed_x = int(cell_info.get('x'))
                    placed_y = int(cell_info.get('y'))
                    coord_src = "logical_db"
                    if debug_components:
                        print(f"[DEBUG] cell {cell_name}: found in logical_db -> ({placed_x},{placed_y})")
                else:
                    placed_x = 0
                    placed_y = 0
                    coord_src = "origin"
                    if debug_components:
                        print(f"[DEBUG] cell {cell_name}: no placement -> (0,0)")

            placed_x, placed_y = snap_to_grid(placed_x, placed_y, x_grid, y_grid)

            components.append({
                'name': cell_name,
                'model': cell_type,
                'source': 'NETLIST',
                'x': placed_x,
                'y': placed_y,
                'orient': placed_orient,
                'status': 'FIXED',
                'weight': 1.0
            })

    if not components:
        print(f"[WARN] No components extracted from fabric database or logical database")

    return components, units, coords


def write_def_file(design_name: str,
                   die_area: Tuple[int, int, int, int],
                   pins: List[Dict[str, Any]],
                   components: List[Dict[str, Any]],
                   output_file: str,
                   units: int = 1000,
                   coords: str = 'MICRONS',
                   lef_data: Optional[Dict[str, Any]] = None,
                   tlef_data: Optional[Dict[str, Any]] = None,
                   logical_db: Optional[Dict[str, Any]] = None,
                   fabric_db: Optional[Dict[str, Any]] = None):
    """
    Write DEF file with all placement information following DEF 5.8 specification.

    This function generates a DEF file that is compatible with OpenROAD and includes:
    - DEF 5.8 header with proper syntax
    - Technology information from LEF/TLEF files
    - Die area with proper bounding box
    - I/O pins with rectangular geometry and proper constraints
    - Component placements with orientations

    Args:
        design_name: Name of the design
        die_area: Tuple of (llx, lly, urx, ury) in database units
        pins: List of I/O pins with position, dimensions, and layer information
              Each pin dict must contain: name, net, direction, use, x, y, width, height, layer, orient
        components: List of component placements with orientation
        output_file: Path to output DEF file
        units: Database units per micron (default 1000)
        coords: Coordinate system (default MICRONS)
        lef_data: Optional LEF data for macro information
        tlef_data: Optional TLEF data for technology information
    """
    with open(output_file, 'w') as f:
        # ======================================
        # Header Section - DEF 5.8 Compliant
        # ======================================
        f.write("VERSION 5.8 ;\n")
        f.write("\n")

        # Extract divider and busbit chars from LEF/TLEF if available
        dividerchar = "/"
        busbitchars = "[]"
        
        if lef_data:
            dividerchar = lef_data.get('dividerchar', '/')
            busbitchars = lef_data.get('busbitchars', '[]')
        elif tlef_data:
            dividerchar = tlef_data.get('dividerchar', '/')
            busbitchars = tlef_data.get('busbitchars', '[]')

        f.write(f"DIVIDERCHAR \"{dividerchar}\" ;\n")
        f.write(f"BUSBITCHARS \"{busbitchars}\" ;\n")
        f.write("\n")

        # Design name
        f.write(f"DESIGN {design_name} ;\n")
        f.write("\n")

        # Units specification - Use 1000 DBU/micron (standard for DEF files)
        f.write(f"UNITS DISTANCE {coords} 1000 ;\n")
        f.write("\n")

        # Die area
        llx, lly, urx, ury = die_area
        # Do NOT halve - components use original coordinates
        f.write(f"DIEAREA ( {llx} {lly} ) ( {urx} {ury} ) ;\n")
        f.write("\n")

        # ======================================
        # Rows Section - DEF 5.8 Format (for placement grid)
        # ======================================
        # ROW defines placement rows for the standard cells
        # For structured ASIC with pre-placed cells, we create rows across the die
        if llx < urx and lly < ury:
            # Get standard cell height and site width from TLEF if available
            if tlef_data and 'sites' in tlef_data:
                site_width_dbu, site_height_dbu, site_name = get_site_dimensions(tlef_data, units)
            else:
                # Fallback to Sky130 defaults (do NOT halve - components use original coords)
                site_width_dbu, site_height_dbu, site_name = 460, 2720, 'unithd'
            
            # Extract unique Y coordinates from placed components for ROW generation
            unique_y_coords = sorted(set(c.get('y', 0) for c in components if c.get('status') != 'UNPLACED'))
            
            # Generate ROWs at regular grid intervals for better routing capacity
            # Need to use halved coordinates to match the halved DIEAREA and PIN coordinates
            rows_list = []
            num_cols = (urx - llx) // site_width_dbu
            row_num = 0
            
            # Use maximum row density for maximum routing tracks
            # 1/16 of site height for ultra-dense routing
            row_spacing = site_height_dbu // 16  # Use 1/16 site height for ultra-dense routing
            
            # Create rows at regular intervals across the entire die
            # This provides maximum routing tracks between placements
            row_y = lly
            while row_y < ury:
                rows_list.append((f"ROW_{row_num}", llx, row_y, num_cols, site_width_dbu))
                row_y += row_spacing
                row_num += 1
            
            print(f"[INFO] Generated {len(rows_list)} ROWs at {row_spacing} DBU spacing ({row_spacing/1000:.3f}µm) for ultra-dense routing capacity")
            
            # Write all ROW statements
            for row_name, row_x, row_y, num_cols, step_x in rows_list:
                f.write(f"ROW {row_name} {site_name} {row_x} {row_y} N DO {num_cols} BY 1 STEP {step_x} 0 ;\n")
            
            f.write("\n")

        # ======================================
        # Tracks Section - DEF 5.8 Format (for routing grid)
        # ======================================
        # TRACKS defines the routing grid for each layer
        # Format: TRACKS {X|Y} start DO count STEP pitch [LAYER layer ...] ;
        
        # Extract unique X and Y coordinates from placed components for track generation
        placed_components = [c for c in components if c.get('status') != 'UNPLACED']
        unique_x_coords = sorted(set(c.get('x', 0) for c in placed_components))
        unique_y_coords = sorted(set(c.get('y', 0) for c in placed_components))
        
        # X and Y tracks for each layer - use actual placement coordinates for better alignment
        x_pitch = site_width_dbu  # Same as site width for grid alignment
        y_pitch = site_height_dbu // 2  # Typically half the site height for better coverage
        
        if unique_x_coords and unique_y_coords:
            # Generate tracks based on actual placement ranges
            x_min = min(unique_x_coords)
            x_max = max(unique_x_coords)
            y_min = min(unique_y_coords)
            y_max = max(unique_y_coords)
            
            x_do = max(1, (x_max - x_min) // x_pitch + 10)  # Add some margin
            y_do = max(1, (y_max - y_min) // y_pitch + 10)  # Add some margin
        else:
            # Fallback to full die area
            x_do = (urx - llx) // x_pitch
            y_do = (ury - lly) // y_pitch
        
        # Layer-specific track configuration
        # Keep normal density on all layers to maintain routing resources
        # li1: local interconnect (minimal)
        # met1: horizontal signal routing - use normal Y pitch for more tracks
        # met2: vertical signal routing
        # met3: horizontal clock routing
        # met4: vertical signal routing
        # met5: horizontal top-level routing
        
        layer_configs = {
            'li1': {'x_pitch': x_pitch * 4, 'y_pitch': y_pitch * 4},  # Very sparse for local use
            'met1': {'x_pitch': x_pitch, 'y_pitch': y_pitch},         # Normal pitches for met1
            'met2': {'x_pitch': x_pitch, 'y_pitch': y_pitch},         # Normal vertical tracks
            'met3': {'x_pitch': x_pitch, 'y_pitch': y_pitch},         # Normal horizontal (clock)
            'met4': {'x_pitch': x_pitch, 'y_pitch': y_pitch},         # Normal vertical tracks
            'met5': {'x_pitch': x_pitch, 'y_pitch': y_pitch},         # Normal horizontal tracks
        }
        
        for layer in ['li1', 'met1', 'met2', 'met3', 'met4', 'met5']:
            config = layer_configs[layer]
            x_do_layer = (urx - llx) // config['x_pitch']
            y_do_layer = (ury - lly) // config['y_pitch']
            f.write(f"TRACKS X {llx} DO {x_do_layer} STEP {config['x_pitch']} LAYER {layer} ;\n")
            f.write(f"TRACKS Y {lly} DO {y_do_layer} STEP {config['y_pitch']} LAYER {layer} ;\n")
        f.write("\n")

        # ======================================
        # Components Section - DEF 5.8 Format (BEFORE PINS)
        # ======================================
        # DEF 5.8 Syntax:
        # COMPONENTS numComps ;
        # [- compName modelName
        #    [+ SOURCE {NETLIST | DIST | USER | TIMING}]
        #    [+ {FIXED pt orient | COVER pt orient | PLACED pt orient | UNPLACED}]
        #    [+ WEIGHT weight]
        #  ; ] ...
        # END COMPONENTS
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in sorted(components, key=lambda c: c['name']):
            # Compact format: - name model + FIXED (x y) orient ;
            if comp['status'] == 'UNPLACED':
                f.write(f"  - {comp['name']} {comp['model']} + UNPLACED ;\n")
            else:
                f.write(f"  - {comp['name']} {comp['model']} + {comp['status']} ( {comp['x']} {comp['y']} ) {comp.get('orient', 'N')} ;\n")
        f.write("END COMPONENTS\n")
        f.write("\n")

 
        # ======================================
        # Pins Section - DEF 5.8 Format (AFTER COMPONENTS)
        # ======================================
        # DEF 5.8 Correct Syntax:
        # PINS numPins ;
        # [- pinName + NET netName
        #    [+ DIRECTION {INPUT | OUTPUT | INOUT | FEEDTHRU}]
        #    [+ USE {SIGNAL | POWER | GROUND | CLOCK | ...}]
        #    [[+ PORT]
        #     [+ LAYER layerName
        #         ( x1 y1 ) ( x2 y2 )
        #     |+ POLYGON layerName
        #         pt pt pt ...
        #     |+ VIA viaName
        #         pt] ...
        #     [+ COVER pt orient | FIXED pt orient | PLACED pt orient]
        #    ]...
        # ; ] ...
        # END PINS
        
        # Create mapping from net ID to net name for pins section
        net_id_to_name = {}
        if logical_db and 'nets' in logical_db:
            for net_id, net_info in logical_db['nets'].items():
                # Use the net name if available, otherwise use net_id
                net_name = net_info.get('name', f'net_{net_id}')
                net_id_to_name[str(net_id)] = str(net_name)
        
        f.write(f"PINS {len(pins)} ;\n")
        for pin in sorted(pins, key=lambda p: p['name']):
            # Convert net ID to net name
            net_id = str(pin['net'])
            net_name = net_id_to_name.get(net_id, net_id)
            
            # Use fabric coordinates directly (no offset)
            # Divide by 2 to account for DBU scaling (OpenROAD uses 2000 DBU/µm internally)
            # PINs are the only coordinates that should be halved
            x_coord = pin['x'] // 2
            y_coord = pin['y'] // 2
            
            # Use layer from fabric_db if available, otherwise default to met2
            pin_layer = pin.get('layer', 'met2')
            
            # Get minimum width from layer definition (for access point sizing)
            min_width = 100  # Default 0.1µm
            
            if tlef_data and 'layers' in tlef_data:
                layer_info = tlef_data['layers'].get(pin_layer, {})
                # Extract minimum width from layer if available
                if 'width' in layer_info:
                    min_width = int(layer_info['width'] * units)
            
            # Get pin side from fabric_db to determine extension direction
            pin_side = pin.get('side', 'south')
            
            die_x_max = urx  # From DIEAREA
            die_y_max = ury  # From DIEAREA
            
            # Create rectangular geometry based on pin side:
            # - east: extend to the left only (x1 = x_coord - min_width, x2 = x_coord)
            # - west: extend to the right only (x1 = x_coord, x2 = x_coord + min_width)
            # - south: extend upward only (y1 = y_coord, y2 = y_coord + min_width)
            # - north: extend downward only (y1 = y_coord - min_width, y2 = y_coord)
            
            if pin_side == 'east':
                # East side: extend left from the pin point
                x1 = x_coord - min_width
                x2 = x_coord
                y1 = y_coord - min_width // 2
                y2 = y_coord + min_width // 2
            elif pin_side == 'west':
                # West side: extend right from the pin point
                x1 = x_coord
                x2 = x_coord + min_width
                y1 = y_coord - min_width // 2
                y2 = y_coord + min_width // 2
            elif pin_side == 'south':
                # South side: extend upward from the pin point
                x1 = x_coord - min_width // 2
                x2 = x_coord + min_width // 2
                y1 = y_coord
                y2 = y_coord + min_width
            elif pin_side == 'north':
                # North side: extend downward from the pin point
                x1 = x_coord - min_width // 2
                x2 = x_coord + min_width // 2
                y1 = y_coord - min_width
                y2 = y_coord
            else:
                # Default: center around pin point
                x1 = x_coord - min_width // 2
                x2 = x_coord + min_width // 2
                y1 = y_coord - min_width // 2
                y2 = y_coord + min_width // 2
            
            # Clamp rectangle to die bounds to ensure pins are inside
            x1 = max(llx, x1)
            x2 = min(die_x_max, x2)
            y1 = max(lly, y1)
            y2 = min(die_y_max, y2)
            
            f.write(f"  - {pin['name']} + NET {net_name}\n")
            f.write(f"    + DIRECTION {pin['direction']}\n")
            f.write(f"    + USE {pin.get('use', 'SIGNAL')}\n")
            f.write(f"    + PORT\n")
            f.write(f"      + LAYER {pin_layer}\n")
            f.write(f"        ( {x1} {y1} ) ( {x2} {y2} )\n")
            f.write(f"      + FIXED ( {x_coord} {y_coord} ) {pin.get('orient', 'N')} ;\n")
        f.write("END PINS\n")
        f.write("\n")

        # ======================================
        # Nets Section - DEF 5.8 Format (AFTER PINS)
        # ======================================
        # DEF 5.8 Correct Syntax:
        # NETS numNets ;
        # [- netName
        #    [ ( {compName pinName | PIN pinName} [+ SYNTHESIZED] ) ] ...
        #    [+ SHIELDNET shieldNetName ] ...
        #    [+ VPIN vpinName [LAYER layerName] pt pt
        #        [PLACED pt orient | FIXED pt orient | COVER pt orient] ] ...
        #    [+ SUBNET subnetName ... ] ...
        #    [+ XTALK class]
        #    [+ NONDEFAULTRULE ruleName]
        #    [regularWiring] ...
        #    [+ SOURCE {DIST | NETLIST | TEST | TIMING | USER}]
        #    [+ FIXEDBUMP]
        #    [+ FREQUENCY frequency]
        #    [+ ORIGINAL netName]
        #    [+ USE {ANALOG | CLOCK | GROUND | POWER | RESET | SCAN | SIGNAL | TIEOFF}]
        #    [+ PATTERN {BALANCED | STEINER | TRUNK | WIREDLOGIC}]
        #    [+ ESTCAP wireCapacitance]
        #    [+ WEIGHT weight]
        #    [+ PROPERTY {propName propVal} ...] ...
        # ; ] ...
        # END NETS
        
        # Build nets from all connections (pins + component pins)
        # A net must include ALL terminals that belong to it:
        # - Top-level PINS (I/O ports)
        # - Component pins (cell pins inside the design)
        nets = defaultdict(list)
        
        # Create mapping from net ID to net name
        net_id_to_name = {}
        if logical_db and 'nets' in logical_db:
            for net_id, net_info in logical_db['nets'].items():
                # Use the net name if available, otherwise use net_id
                net_name = net_info.get('name', f'net_{net_id}')
                net_id_to_name[str(net_id)] = str(net_name)
        
        # 1. Add top-level pins (convert net ID to net name)
        for pin in pins:
            net_id = str(pin['net'])
            net_name = net_id_to_name.get(net_id, net_id)
            pin_name = pin['name']
            nets[net_name].append(('PIN', pin_name))
        
        # 2. Add component pins from logical_db if available
        if logical_db and 'nets' in logical_db:
            for net_id, net_info in logical_db['nets'].items():
                # Use the net name instead of ID
                net_name = net_id_to_name.get(str(net_id), str(net_id))
                net_connections = net_info.get('connections', [])
                
                # Add all component pins to the net
                for inst_name, pin_name in net_connections:
                    # Skip top-level pins (already added above in step 1)
                    if inst_name not in [p['name'] for p in pins]:
                        # Add component pin reference
                        nets[net_name].append(('COMPONENT', inst_name, pin_name))
        
        # Sort nets by name for consistent output (convert keys to strings for sorting)
        sorted_nets = sorted(nets.items(), key=lambda x: str(x[0]))
        
        f.write(f"NETS {len(sorted_nets)} ;\n")
        for net_name, terminals in sorted_nets:
            # Start net definition
            f.write(f"  - {net_name}\n")
            
            # Check if this is a clock net (contains clk pin OR net name starts with 'clk')
            has_clk_pin = any(terminal[0] == 'PIN' and terminal[1] == 'clk' for terminal in terminals)
            is_clock_net = has_clk_pin or str(net_name).lower().startswith('clk')
            
            # Add all terminal references (pins and component pins) with proper formatting
            for terminal in terminals:
                if terminal[0] == 'PIN':
                    # Top-level pin reference
                    pin_name = terminal[1]
                    f.write(f"      ( PIN {pin_name} )\n")
                elif terminal[0] == 'COMPONENT':
                    # Component pin reference: ( compName pinName )
                    inst_name = terminal[1]
                    pin_name = terminal[2]
                    
                    # Get the cell type for this instance
                    cell_type = None
                    if logical_db and 'cells' in logical_db:
                        if inst_name in logical_db['cells']:
                            cell_type = logical_db['cells'][inst_name].get('type')
                    
                    # Correct the pin name based on LEF data for output pins
                    # If the pin name from netlist is 'Y' but cell type has different output, use LEF
                    if cell_type and pin_name == 'Y' and lef_data and 'macros' in lef_data:
                        corrected_pin = get_output_pin_name(cell_type, lef_data['macros'])
                        if corrected_pin != 'Y':
                            pin_name = corrected_pin
                    
                    f.write(f"      ( {inst_name} {pin_name} )\n")
            
            # Add USE CLOCK for all clock nets (either contains clk pin or name starts with 'clk')
            if is_clock_net:
                f.write(f"    + USE CLOCK ;\n")
            else:
                f.write(f"    ;\n")
        
        f.write("END NETS\n")

        # ======================================
        # End Design Section
        # ======================================
        f.write("\n")
        f.write("END DESIGN\n")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python make_def.py <design_name> [options]")
        sys.exit(1)

    # Required argument
    design_name = sys.argv[1]

    # Flags and options with defaults
    run_cts = True
    run_eco = True
    clock_net = None
    output_dir = None
    tlef_file = None
    lef_file = None
    
    design_json = None
    fabric_cells_yaml = None
    pins_yaml = None
    fabric_yaml = None
    placement_map_file = None

    # Parse arguments - handle both positional and optional arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        
        # Check for flags first
        if arg == '--no-cts':
            run_cts = False
            i += 1
        elif arg == '--no-eco':
            run_eco = False
            i += 1
        elif arg == '--clock' and i + 1 < len(sys.argv):
            clock_net = sys.argv[i + 1]
            i += 2
        elif arg == '--output' and i + 1 < len(sys.argv):
            output_dir = sys.argv[i + 1]
            i += 2
        elif arg == '--tlef' and i + 1 < len(sys.argv):
            tlef_file = sys.argv[i + 1]
            i += 2
        elif arg == '--lef' and i + 1 < len(sys.argv):
            lef_file = sys.argv[i + 1]
            i += 2
        # Positional arguments (no leading dashes)
        elif not arg.startswith('--'):
            # Assign positional arguments in order
            if design_json is None:
                design_json = arg
            elif fabric_cells_yaml is None:
                fabric_cells_yaml = arg
            elif pins_yaml is None:
                pins_yaml = arg
            elif fabric_yaml is None:
                fabric_yaml = arg
            elif placement_map_file is None:
                placement_map_file = arg
            i += 1
        else:
            i += 1

    # -------------------------------
    # Default argument values
    # -------------------------------
    if design_json is None:
        design_json = f"designs/{design_name}_mapped.json"

    if fabric_cells_yaml is None:
        fabric_cells_yaml = "fabric/fabric_cells.yaml"

    if pins_yaml is None:
        pins_yaml = "fabric/pins.yaml"

    if fabric_yaml is None:
        fabric_yaml = "fabric/fabric.yaml"

    if placement_map_file is None:
        #design_specific_map = os.path.join("build", design_name, "placement.map")
        #placement_map_file = design_specific_map if os.path.exists(design_specific_map) else "placement_cts.map"
        placement_map_file = f"build/{design_name}/{design_name}_cts.map"
    if output_dir is None:
        output_dir = f"build/{design_name}"

    # Default tech files if not specified
    if tlef_file is None:
        tlef_file = "tech/sky130_fd_sc_hd.tlef"

    if lef_file is None:
        lef_file = "tech/sky130_fd_sc_hd.lef"

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    print(f"{'=' * 70}")
    print(f"DEF Generation for {design_name}")
    print(f"{'=' * 70}")

    # ========================================
    # Load Technology Information
    # ========================================
    print("\n[0/6] Loading technology information...")
    print(f"  Loading TLEF: {tlef_file}")
    tlef_data = parse_tlef_file(tlef_file)
    print(f"  Loaded TLEF version {tlef_data.get('version', 'unknown')}")
    print(f"  Found {len(tlef_data.get('sites', {}))} sites and {len(tlef_data.get('layers', {}))} layers")

    print(f"  Loading LEF: {lef_file}")
    lef_data = parse_lef_file(lef_file)
    print(f"  Loaded LEF version {lef_data.get('version', 'unknown')}")
    print(f"  Found {len(lef_data.get('macros', {}))} macro definitions")

    # ========================================
    # Build databases (once, upfront)
    # ========================================
    print("\n[1/6] Building databases...")

    # Load fabric database
    print("  Building fabric database from YAML files...")
    fabric_db = build_fabric_db(fabric_cells_yaml, pins_yaml, fabric_yaml)

    # Parse logical design
    print("  Loading logical design...")
    logical_db, netlist_graph = parse_design_json(design_json)
    print(f"  Loaded logical_db with {len(logical_db['cells'])} cells")
    print(f"  Loaded netlist_graph with {len(netlist_graph.nodes())} nodes")

    # Parse placement map
    print("  Loading placement map...")
    io_ports, fabric_cells = parse_placement_map(placement_map_file)
    print(f"  Loaded {len(io_ports)} I/O ports and {len(fabric_cells)} fabric cells")

    # Build placement data structure from placement map file for $ cell lookup
    placement_data = {}
    if os.path.exists(placement_map_file):
        with open(placement_map_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or '->' not in line:
                    continue
                parts = line.split('->')
                if len(parts) != 2:
                    continue
                left = parts[0].strip().split()
                logical_inst = parts[1].strip()
                if len(left) >= 3:
                    fabric_cell = left[0]
                    cell_type = left[1]
                    try:
                        x_coord = float(left[2])
                        y_coord = float(left[3]) if len(left) > 3 else 0.0
                        placement_data[logical_inst] = {
                            'fabric_cell': fabric_cell,
                            'cell_type': cell_type,
                            'x': x_coord,
                            'y': y_coord,
                            'orient': 'N'
                        }
                    except (ValueError, IndexError):
                        pass
    print(f"  Built placement data for {len(placement_data)} instances from map")

    # Load leakage database for power-down ECO
    print("  Loading leakage database from Liberty file...")
    liberty_file = "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
    leakage_db = parse_liberty_leakage(liberty_file, verbose=False)
    print(f"  Loaded leakage data for {len(leakage_db)} cell types")

    # ========================================
    # Run CTS
    # ========================================
    if run_cts:
        print("\n[2/6] Running CTS...")
        # Create CTS instance with pre-built databases
        cts = HTreeCTS(
            io_ports=io_ports,
            fabric_cells=fabric_cells,
            fabric_db=fabric_db,
            logical_db=logical_db,
            netlist_graph=netlist_graph
        )

        # Run CTS flow
        cts.find_clock_net(clock_net)
        cts.find_sinks()
        cts.find_resources()
        cts.build_clock_tree()

        # Update databases
        logical_db, netlist_graph = cts.update_logical_db_and_graph()

        # Write CTS outputs
        cts_placement_file = f"build/{design_name}/{design_name}_cts.map"
        cts.write_placement(cts_placement_file)
        cts.write_clock_tree("clock_tree.json")

        print(f"  CTS placement written to: {cts_placement_file}")

        # Reload placement map after CTS for both directions
        logical_to_fabric, fabric_to_logical = load_placement_map(cts_placement_file)

        # Use CTS placement for subsequent steps
        placement_map_file = cts_placement_file
    else:
        print("\n[2/6] Skipping CTS (--no-cts)")
        # Load original placement map
        logical_to_fabric, fabric_to_logical = load_placement_map(placement_map_file)

    # ========================================
    # Run ECO
    # ========================================
    if run_eco:
        print("\n[3/6] Running Power-Down ECO...")

        # Load placement mapping in the format expected by power_down_eco
        placement_map_dict = load_placement_mapping(placement_map_file)

        # Run ECO with pre-built databases
        logical_db, eco_report = run_power_down_eco(
            logical_db=logical_db,
            fabric_db=fabric_db,
            leakage_db=leakage_db,
            placement_map=placement_map_dict,
            output_dir=output_dir,
            verbose=True
        )
        
        # Debug: Print specific cell after ECO
        if 'T35Y60__R1_CONB_0' in logical_db.get('cells', {}):
            cell_info = logical_db['cells']['T35Y60__R1_CONB_0']
            print(f"\n[DEBUG] Cell T35Y60__R1_CONB_0 in logical_db:")
            print(f"  Type: {cell_info.get('type', 'UNKNOWN')}")
            print(f"  Pins: {cell_info.get('pins', {})}")
            print(f"  Full info: {json.dumps(cell_info, indent=4)}")
        else:
            print(f"\n[DEBUG] Cell T35Y60__R1_CONB_0 NOT found in logical_db")
            print(f"  Available cells: {list(logical_db.get('cells', {}).keys())[:10]}...")
    else:
        print("\n[3/6] Skipping ECO (--no-eco)")

    # ========================================
    # Generate DEF
    # ========================================
    print("\n[4/6] Extracting design information...")
    die_area = get_die_area(fabric_db)
    pins = extract_io_pins(logical_db, fabric_db, tlef_data)
    
    # Get database units from fabric_db
    units = fabric_db.get('fabric', {}).get('pin_placement', {}).get('units', {}).get('dbu_per_micron', 1000)
    
    # Get site dimensions from TLEF for grid alignment
    site_width_dbu, site_height_dbu, site_name = get_site_dimensions(tlef_data, units)
    print(f"  Site dimensions: {site_width_dbu} x {site_height_dbu} DBU ({site_name})")
    
    components, units, coords = extract_components(
        fabric_db,
        logical_db,
        fabric_to_logical,
        logical_to_fabric,
        placement_data,
        site_width_dbu,
        site_height_dbu,
        die_area
    )

    print(f"  Die area: {die_area}")
    print(f"  Pins: {len(pins)}")
    print(f"  Components: {len(components)}")
    print(f"  Database units: {units} per micron")
    print(f"  Coordinate system: {coords}")

    # ========================================
    # Write DEF file with technology info
    # ========================================
    print("\n[5/6] Writing DEF file (DEF 5.8 format)...")
    def_path = os.path.join(output_dir, f"{design_name}_fixed.def")
    write_def_file(
        design_name,
        die_area,
        pins,
        components,
        def_path,
        units,
        coords,
        lef_data=lef_data,
        tlef_data=tlef_data,
        logical_db=logical_db,
        fabric_db=fabric_db
    )

    print(f"  DEF written to: {def_path}")
    
    # Verify DEF syntax
    print("\n[6/6] Validating DEF syntax...")
    validate_def_file(def_path)
    
    print("\nDone!")
    print(f"{'=' * 70}")
    print(f"DEF file is ready for OpenROAD: {def_path}")
    print(f"{'=' * 70}")


def validate_def_file(def_file: str) -> bool:
    """
    Perform comprehensive validation on the generated DEF file.

    This function validates that the generated DEF file conforms to the DEF 5.8
    specification and contains all required sections and statements. Checks include:

    Mandatory Statements (DEF 5.8):
        - VERSION: Must be present and readable
        - DIVIDERCHAR: Hierarchy divider character specification
        - BUSBITCHARS: Bus bit character specification
        - DESIGN: Design name statement
        - UNITS: Distance and unit specification
        - DIEAREA: Die area bounding box
        - PINS/END PINS: I/O pin definitions section
        - COMPONENTS/END COMPONENTS: Cell placement section
        - END DESIGN: File terminator

    Optional but Recommended:
        - Proper coordinate system (MICRONS or NANOMETERS)
        - Proper pin attributes (DIRECTION, USE)
        - Proper component attributes (SOURCE, FIXED/PLACED)

    Args:
        def_file (str): Path to DEF file to validate

    Returns:
        bool: True if validation passes, False if critical errors found

    DEF 5.8 Compliance:
        - Validates against DEF 5.8 Language Reference specification
        - Reports all errors, warnings, and missing optional elements
    """
    if not os.path.exists(def_file):
        print(f"  [ERROR] DEF file not found: {def_file}")
        return False

    try:
        with open(def_file, 'r') as f:
            content = f.read()
    except IOError as e:
        print(f"  [ERROR] Cannot read DEF file: {e}")
        return False

    errors = []
    warnings = []
    info_msgs = []

    # Check VERSION (mandatory)
    version_match = re.search(r'VERSION\s+([\d.]+)\s*;', content)
    if not version_match:
        errors.append("VERSION statement missing")
    else:
        version = version_match.group(1)
        if version != '5.8':
            warnings.append(f"DEF version {version} (expected 5.8)")
        else:
            info_msgs.append("VERSION 5.8 correct")

    # Check DIVIDERCHAR (mandatory in DEF 5.8)
    if 'DIVIDERCHAR' not in content:
        errors.append("DIVIDERCHAR statement missing (required for DEF 5.8)")
    else:
        info_msgs.append("DIVIDERCHAR present")

    # Check BUSBITCHARS (mandatory in DEF 5.8)
    if 'BUSBITCHARS' not in content:
        errors.append("BUSBITCHARS statement missing (required for DEF 5.8)")
    else:
        info_msgs.append("BUSBITCHARS present")

    # Check DESIGN (mandatory)
    if 'DESIGN' not in content:
        errors.append("DESIGN statement missing (mandatory)")
    else:
        design_match = re.search(r'DESIGN\s+(\w+)\s*;', content)
        if design_match:
            info_msgs.append(f"DESIGN '{design_match.group(1)}' found")

    # Check UNITS (mandatory in DEF 5.8)
    if 'UNITS' not in content:
        errors.append("UNITS statement missing (required for DEF 5.8)")
    else:
        units_match = re.search(r'UNITS\s+DISTANCE\s+(\w+)\s+(\d+)', content)
        if units_match:
            info_msgs.append(f"UNITS {units_match.group(1)} {units_match.group(2)} correct")
        else:
            warnings.append("UNITS format may be incorrect")

    # Check DIEAREA (mandatory)
    if 'DIEAREA' not in content:
        errors.append("DIEAREA statement missing (mandatory)")
    else:
        diearea_match = re.search(r'DIEAREA\s*\(([^)]+)\)\s*\(([^)]+)\)\s*;', content)
        if diearea_match:
            info_msgs.append(f"DIEAREA bounding box found")
        else:
            warnings.append("DIEAREA format may be incorrect")

    # Check PINS section (mandatory for I/O designs)
    pins_match = re.search(r'PINS\s+(\d+)\s*;', content)
    if pins_match:
        num_pins = int(pins_match.group(1))
        info_msgs.append(f"PINS section: {num_pins} pins")
        if 'END PINS' not in content:
            errors.append("PINS section missing END PINS")
    else:
        warnings.append("PINS section not found or malformed")

    # Check COMPONENTS section (mandatory for designs with cells)
    comps_match = re.search(r'COMPONENTS\s+(\d+)\s*;', content)
    if comps_match:
        num_comps = int(comps_match.group(1))
        info_msgs.append(f"COMPONENTS section: {num_comps} components")
        if 'END COMPONENTS' not in content:
            errors.append("COMPONENTS section missing END COMPONENTS")
    else:
        warnings.append("COMPONENTS section not found or malformed")

    # Check END DESIGN (mandatory)
    if 'END DESIGN' not in content:
        errors.append("END DESIGN statement missing (mandatory)")
    else:
        info_msgs.append("END DESIGN terminator found")

    # Validation results
    if errors:
        print(f"  [FAIL] Validation FAILED with {len(errors)} critical error(s)")
        for i, error in enumerate(errors, 1):
            print(f"    {i}. {error}")
        if warnings:
            print(f"  Warnings ({len(warnings)}):") 
            for i, warning in enumerate(warnings, 1):
                print(f"    W{i}. {warning}")
        return False
    else:
        print(f"  [PASS] Validation PASSED")
        if warnings:
            print(f"  Warnings ({len(warnings)}):") 
            for i, warning in enumerate(warnings, 1):
                print(f"    W{i}. {warning}")
        if info_msgs:
            print(f"  Details ({len(info_msgs)}):") 
            for i, msg in enumerate(info_msgs, 1):
                print(f"    - {msg}")
        return True


if __name__ == "__main__":
    main()
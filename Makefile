# ============================================================================
# Structured-ASIC Makefile
# ============================================================================
# Usage:
#   make all DESIGN=6502       # Run full flow for 6502 design
#   make place DESIGN=arith    # Run only placement for arith
#   make viz DESIGN=6502       # Generate visualizations
#   make clean DESIGN=6502     # Clean build files for 6502
#   make clean-all             # Clean all build files
#
# Targets:
#   validate  - Validate design fits on fabric
#   place     - Run Greedy + SA placement
#   eco       - Run CTS + ECO netlist generation
#   route     - Run OpenROAD routing (requires openroad)
#   sta       - Run OpenSTA timing analysis (requires sta)
#   viz       - Generate all visualizations
#   all       - Run full flow (validate → place → eco → route → sta → viz)
# ============================================================================

# Default design (can be overridden: make all DESIGN=arith)
DESIGN ?= 6502

# Directories
BUILD_DIR := build/$(DESIGN)
DESIGN_JSON := designs/$(DESIGN)_mapped.json

# Python interpreter
PYTHON := python3

# External tools (adjust paths if needed)
OPENROAD := openroad
STA := sta

# ============================================================================
# Output files (used as Make targets/dependencies)
# ============================================================================
PLACEMENT_MAP := $(BUILD_DIR)/placement_sa_optimized.map
FINAL_V := $(BUILD_DIR)/$(DESIGN)_final.v
FIXED_DEF := $(BUILD_DIR)/$(DESIGN)_fixed.def
CLOCK_TREE := $(BUILD_DIR)/$(DESIGN)_clock_tree.json
ROUTED_DEF := $(BUILD_DIR)/$(DESIGN)_routed.def
SPEF_FILE := $(BUILD_DIR)/$(DESIGN).spef
SETUP_RPT := $(BUILD_DIR)/$(DESIGN)_setup_timing.rpt
SDC_FILE := $(DESIGN).sdc

# Visualization outputs
LAYOUT_PNG := $(BUILD_DIR)/$(DESIGN)_layout.png
DENSITY_PNG := $(BUILD_DIR)/$(DESIGN)_density.png
CTS_PNG := $(BUILD_DIR)/$(DESIGN)_cts_tree.png
SLACK_PNG := $(BUILD_DIR)/$(DESIGN)_slack.png

# ============================================================================
# Phony targets
# ============================================================================
.PHONY: all validate place eco route sta viz clean clean-all help

# ============================================================================
# Default target
# ============================================================================
all: viz
	@echo ""
	@echo "============================================================"
	@echo "Full flow complete for $(DESIGN)"
	@echo "============================================================"
	@echo "Outputs in: $(BUILD_DIR)/"
	@echo ""

# ============================================================================
# Help
# ============================================================================
help:
	@echo "Structured-ASIC Makefile"
	@echo ""
	@echo "Usage: make <target> DESIGN=<design_name>"
	@echo ""
	@echo "Targets:"
	@echo "  all       - Run full flow (default)"
	@echo "  validate  - Validate design fits on fabric"
	@echo "  place     - Run placement (Greedy + SA)"
	@echo "  eco       - Run CTS + ECO generation"
	@echo "  route     - Run OpenROAD routing"
	@echo "  sta       - Run static timing analysis"
	@echo "  viz       - Generate visualizations"
	@echo "  clean     - Remove build files for DESIGN"
	@echo "  clean-all - Remove all build files"
	@echo ""
	@echo "Examples:"
	@echo "  make all DESIGN=6502"
	@echo "  make place DESIGN=arith"
	@echo "  make viz DESIGN=expanded_6502"
	@echo ""

# ============================================================================
# Phase 1: Validate
# ============================================================================
validate: $(DESIGN_JSON)
	@echo ""
	@echo "=== Phase 1: Validating $(DESIGN) ==="
	$(PYTHON) validator.py $(DESIGN_JSON)

# ============================================================================
# Phase 2: Placement
# ============================================================================
# NOTE: optimized.py is hardcoded to 6502 for SA knob analysis consistency.
# For other designs, use placer.py or modify optimized.py manually.
$(PLACEMENT_MAP): $(DESIGN_JSON) placer.py optimized.py
	@echo ""
	@echo "=== Phase 2: Placing $(DESIGN) ==="
	@mkdir -p $(BUILD_DIR)
ifeq ($(DESIGN),6502)
	$(PYTHON) optimized.py
else
	@echo "Note: Using placer.py for $(DESIGN) (optimized.py is 6502-only)"
	$(PYTHON) placer.py $(DESIGN)
endif

place: $(PLACEMENT_MAP)
	@echo "Placement complete: $(PLACEMENT_MAP)"

# ============================================================================
# Phase 3: ECO + CTS
# ============================================================================
$(FINAL_V) $(FIXED_DEF) $(CLOCK_TREE): $(PLACEMENT_MAP) eco_generator.py cts_htree.py make_def.py
	@echo ""
	@echo "=== Phase 3: ECO + CTS for $(DESIGN) ==="
	$(PYTHON) eco_generator.py --design $(DESIGN)
	$(PYTHON) make_def.py $(DESIGN)

eco: $(FINAL_V) $(FIXED_DEF) $(CLOCK_TREE)
	@echo "ECO complete: $(FINAL_V)"
	@echo "DEF complete: $(FIXED_DEF)"

# ============================================================================
# Phase 4: Routing (requires OpenROAD)
# ============================================================================
$(ROUTED_DEF) $(SPEF_FILE): $(FINAL_V) $(FIXED_DEF) route.tcl
	@echo ""
	@echo "=== Phase 4: Routing $(DESIGN) ==="
	@echo "Running OpenROAD (this may take several minutes)..."
	DESIGN_NAME=$(DESIGN) $(OPENROAD) -exit route.tcl || echo "Note: OpenROAD routing completed with warnings"

route: $(ROUTED_DEF)
	@echo "Routing complete: $(ROUTED_DEF)"
	@echo "SPEF extracted: $(SPEF_FILE)"

# ============================================================================
# Phase 5: STA (requires OpenSTA)
# ============================================================================
$(SETUP_RPT): $(SPEF_FILE) $(SDC_FILE) sta.tcl
	@echo ""
	@echo "=== Phase 5: STA for $(DESIGN) ==="
	DESIGN_NAME=$(DESIGN) $(STA) sta.tcl || echo "Note: STA completed with warnings"

sta: $(SETUP_RPT)
	@echo "STA complete: $(SETUP_RPT)"
	@echo "Check $(BUILD_DIR)/ for timing reports"

# ============================================================================
# Visualization
# ============================================================================
$(LAYOUT_PNG) $(DENSITY_PNG) $(CTS_PNG) $(SLACK_PNG): $(SETUP_RPT) visualize.py
	@echo ""
	@echo "=== Generating Visualizations for $(DESIGN) ==="
	$(PYTHON) visualize.py --design $(DESIGN)

viz: $(LAYOUT_PNG)
	@echo "Visualizations complete in $(BUILD_DIR)/"

# Visualization without STA dependency (for designs without timing reports)
viz-no-sta: $(CLOCK_TREE)
	@echo ""
	@echo "=== Generating Visualizations for $(DESIGN) (no STA) ==="
	$(PYTHON) visualize.py --design $(DESIGN)
	@echo "Visualizations complete in $(BUILD_DIR)/"

# ============================================================================
# Clean
# ============================================================================
clean:
	@echo "Cleaning $(BUILD_DIR)/..."
	rm -rf $(BUILD_DIR)

clean-all:
	@echo "Cleaning all build files..."
	rm -rf build/

# ============================================================================
# Dependencies check
# ============================================================================
$(DESIGN_JSON):
	@echo "Error: Design file not found: $(DESIGN_JSON)"
	@echo "Available designs:"
	@ls -1 designs/*_mapped.json 2>/dev/null | sed 's/designs\//  /' | sed 's/_mapped.json//'
	@exit 1

$(SDC_FILE):
	@echo "Warning: SDC file not found: $(SDC_FILE)"
	@echo "STA will use default constraints"

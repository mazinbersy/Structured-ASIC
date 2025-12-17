#!/usr/bin/tclsh
# sta.tcl - Static Timing Analysis Script for expanded_6502 design
# This version works with STANDALONE OpenSTA (no LEF required)

# Force output to flush immediately
fconfigure stdout -buffering line
fconfigure stderr -buffering line

# ============================================================================
# Configuration Parameters
# ============================================================================
set design_name "expanded_6502"
set lib_file "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
set verilog_file "build/expanded_6502/expanded_6502_final.v"
set spef_file "build/expanded_6502/expanded_6502.spef"
set sdc_file "arith.sdc"
set report_dir "build/${design_name}"

puts "========================================="
puts "Static Timing Analysis Script"
puts "========================================="
puts "Design: $design_name"
puts "Working Directory: [pwd]"
puts "Using: Standalone OpenSTA"
puts ""

# ============================================================================
# Create Report Directory
# ============================================================================
if {![file exists $report_dir]} {
    file mkdir $report_dir
    puts "Created report directory: $report_dir"
} else {
    puts "Report directory exists: $report_dir"
}
puts ""

# ============================================================================
# Load Liberty Library
# ============================================================================
puts "========================================="
puts "STEP 1: Loading Liberty Library"
puts "========================================="
puts "File: $lib_file"
if {[file exists $lib_file]} {
    if {[catch {read_liberty $lib_file} err]} {
        puts "✗ ERROR: Failed to read Liberty file: $err"
        exit 1
    }
    puts "✓ Successfully loaded Liberty library"
} else {
    puts "✗ ERROR: Liberty file not found: $lib_file"
    exit 1
}
puts ""

# ============================================================================
# Load Gate-Level Verilog Netlist
# ============================================================================
puts "========================================="
puts "STEP 2: Loading Verilog Netlist"
puts "========================================="
puts "File: $verilog_file"
if {[file exists $verilog_file]} {
    if {[catch {read_verilog $verilog_file} err]} {
        puts "✗ ERROR: Failed to read Verilog: $err"
        exit 1
    }
    puts "✓ Verilog netlist loaded"
    
    if {[catch {link_design $design_name} err]} {
        puts "✗ ERROR: Failed to link design: $err"
        exit 1
    }
    puts "✓ Design linked successfully"
} else {
    puts "✗ ERROR: Verilog file not found: $verilog_file"
    exit 1
}
puts ""

# ============================================================================
# Load SPEF (Parasitics)
# ============================================================================
puts "========================================="
puts "STEP 3: Loading SPEF Parasitics"
puts "========================================="
puts "File: $spef_file"
if {[file exists $spef_file]} {
    if {[catch {read_spef $spef_file} err]} {
        puts "⚠ WARNING: Failed to read SPEF: $err"
        puts "Continuing without parasitic information..."
    } else {
        puts "✓ Successfully loaded SPEF parasitics"
    }
} else {
    puts "⚠ WARNING: SPEF file not found: $spef_file"
    puts "Continuing without parasitic information (ideal timing)..."
}
puts ""

# ============================================================================
# Load SDC Constraints
# ============================================================================
puts "========================================="
puts "STEP 4: Loading SDC Constraints"
puts "========================================="
puts "File: $sdc_file"
if {[file exists $sdc_file]} {
    if {[catch {read_sdc $sdc_file} err]} {
        puts "✗ ERROR: Failed to read SDC: $err"
        exit 1
    }
    puts "✓ Successfully loaded SDC constraints"
} else {
    puts "✗ ERROR: SDC file not found: $sdc_file"
    exit 1
}
puts ""

# ============================================================================
# Check Design Statistics
# ============================================================================
puts "========================================="
puts "STEP 5: Design Statistics"
puts "========================================="

# Get instance count
set inst_count 0
catch {
    foreach_in_collection inst [get_cells *] {
        incr inst_count
    }
}
puts "Total instances: $inst_count"

# Get net count
set net_count 0
catch {
    foreach_in_collection net [get_nets *] {
        incr net_count
    }
}
puts "Total nets: $net_count"

# Get port count
set port_count 0
catch {
    foreach_in_collection port [get_ports *] {
        incr port_count
    }
}
puts "Total ports: $port_count"
puts ""

# ============================================================================
# Report Setup Timing (Top 100 Paths)
# ============================================================================
puts "========================================="
puts "STEP 6: Setup Timing Analysis"
puts "========================================="
set setup_report "$report_dir/${design_name}_setup_timing.rpt"

if {[catch {
    report_checks -path_delay max -format full_clock_expanded \
        -fields {slew cap input_pins nets fanout} \
        -digits 3 \
        -group_count 100 \
        > $setup_report
    puts "✓ Setup timing report: $setup_report"
} err]} {
    puts "⚠ WARNING: Failed to generate setup report: $err"
}

# Summary to console
puts ""
puts "--- Setup Timing Summary (Worst Path) ---"
catch {report_checks -path_delay max -format summary -group_count 1}
puts ""

# ============================================================================
# Report Hold Timing (Top 100 Paths)
# ============================================================================
puts "========================================="
puts "STEP 7: Hold Timing Analysis"
puts "========================================="
set hold_report "$report_dir/${design_name}_hold_timing.rpt"

if {[catch {
    report_checks -path_delay min -format full_clock_expanded \
        -fields {slew cap input_pins nets fanout} \
        -digits 3 \
        -group_count 100 \
        > $hold_report
    puts "✓ Hold timing report: $hold_report"
} err]} {
    puts "⚠ WARNING: Failed to generate hold report: $err"
}

# Summary to console
puts ""
puts "--- Hold Timing Summary (Worst Path) ---"
catch {report_checks -path_delay min -format summary -group_count 1}
puts ""

# ============================================================================
# Report Clock Skew
# ============================================================================
puts "========================================="
puts "STEP 8: Clock Skew Analysis"
puts "========================================="
set skew_report "$report_dir/${design_name}_clock_skew.rpt"

if {[catch {
    report_clock_skew > $skew_report
    puts "✓ Clock skew report: $skew_report"
} err]} {
    puts "⚠ WARNING: Failed to generate clock skew report: $err"
}

# Summary to console
puts ""
puts "--- Clock Skew Summary ---"
catch {report_clock_skew}
puts ""

# ============================================================================
# Report Worst Slack
# ============================================================================
puts "========================================="
puts "STEP 9: Worst Slack Analysis"
puts "========================================="
set wns_report "$report_dir/${design_name}_worst_slack.rpt"

if {[catch {
    report_worst_slack > $wns_report
    puts "✓ Worst slack report: $wns_report"
} err]} {
    puts "⚠ WARNING: Failed to generate worst slack report: $err"
}

puts ""
puts "--- Worst Slack ---"
catch {report_worst_slack}
puts ""

# ============================================================================
# Report Total Negative Slack
# ============================================================================
puts "========================================="
puts "STEP 10: Total Negative Slack"
puts "========================================="
set tns_report "$report_dir/${design_name}_total_negative_slack.rpt"

if {[catch {
    report_tns > $tns_report
    puts "✓ Total negative slack report: $tns_report"
} err]} {
    puts "⚠ WARNING: Failed to generate TNS report: $err"
}

puts ""
puts "--- Total Negative Slack ---"
catch {report_tns}
puts ""

# ============================================================================
# Report Clocks
# ============================================================================
puts "========================================="
puts "STEP 11: Clock Information"
puts "========================================="
set clock_report "$report_dir/${design_name}_clocks.rpt"

if {[catch {
    report_clocks > $clock_report
    puts "✓ Clock report: $clock_report"
} err]} {
    puts "⚠ WARNING: Failed to generate clock report: $err"
}

puts ""
puts "--- Clock Summary ---"
catch {report_clocks}
puts ""

# ============================================================================
# Report Power (Optional)
# ============================================================================
puts "========================================="
puts "STEP 12: Power Analysis (Optional)"
puts "========================================="
set power_report "$report_dir/${design_name}_power.rpt"

if {[catch {
    report_power > $power_report
    puts "✓ Power report: $power_report"
} err]} {
    puts "⚠ Power analysis not available (this is normal)"
}
puts ""

# ============================================================================
# Summary Statistics
# ============================================================================
puts "========================================="
puts "STA ANALYSIS COMPLETE!"
puts "========================================="
puts ""
puts "Design: $design_name"
puts "Reports directory: $report_dir/"
puts ""
puts "Generated Reports:"
puts "  1. Setup timing:    ${design_name}_setup_timing.rpt"
puts "  2. Hold timing:     ${design_name}_hold_timing.rpt"
puts "  3. Clock skew:      ${design_name}_clock_skew.rpt"
puts "  4. Worst slack:     ${design_name}_worst_slack.rpt"
puts "  5. Total TNS:       ${design_name}_total_negative_slack.rpt"
puts "  6. Clock info:      ${design_name}_clocks.rpt"
puts ""
puts "========================================="
puts "Review the reports to check timing closure"
puts "========================================="

# Exit cleanly
exit 0
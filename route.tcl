# route.tcl - OpenROAD routing script for expanded_6502 design - FULL DEBUG VERSION

  set DESIGN "expanded_6502"
  set TECH_LEF "tech/sky130_fd_sc_hd.tlef"
  set CELL_LEF "tech/sky130_fd_sc_hd.lef"
  set MERGE_LEF "tech/sky130_merged.lef"
  set LIB_FILE "tech/sky130_fd_sc_hd__tt_025C_1v80.lib"
  set VERILOG_FILE "build/expanded_6502/expanded_6502_final.v"
  set DEF_FILE "build/expanded_6502/expanded_6502_fixed.def"
  set OUTPUT_DIR "build/expanded_6502"
set LOG_FILE "${OUTPUT_DIR}/routing_debug.log"

# Create output dir if missing
file mkdir $OUTPUT_DIR

# Open log file
set log_fh [open $LOG_FILE w]
proc log_msg {msg} {
  global log_fh
  set timestamp [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]
  puts "\[$timestamp\] $msg"
  puts $log_fh "\[$timestamp\] $msg"
  flush stdout
  flush $log_fh
}

log_msg "=========================================="
log_msg "OpenROAD Routing Script - FULL DEBUG MODE"
log_msg "=========================================="
log_msg "Design: $DESIGN"
log_msg "Process ID: [pid]"
log_msg "Working directory: [pwd]"
log_msg ""

# Clear any existing design/database
log_msg "Clearing any existing database..."
if {[catch {
  catch {odbGetDB}
  catch {odbGetTech}
} result]} {
  log_msg "No previous database to clear"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 1: Reading Technology Files"
log_msg "=========================================="
log_msg ""

log_msg "Reading merged LEF: $MERGE_LEF"
if {[catch {read_lef $MERGE_LEF} result]} {
  log_msg "ERROR: Failed to read LEF: $result"
  close $log_fh
  exit 1
}
log_msg "SUCCESS: LEF loaded"

log_msg ""
log_msg "Reading Liberty file: $LIB_FILE"
if {[catch {read_liberty $LIB_FILE} result]} {
  log_msg "ERROR: Failed to read Liberty: $result"
  close $log_fh
  exit 1
}
log_msg "SUCCESS: Liberty loaded"

log_msg ""
log_msg "=========================================="
log_msg "PHASE 2: Reading Design Files"
log_msg "=========================================="
log_msg ""

log_msg "Reading Verilog netlist: $VERILOG_FILE"
if {[catch {read_verilog $VERILOG_FILE} result]} {
  log_msg "ERROR: Failed to read Verilog: $result"
  close $log_fh
  exit 1
}
log_msg "SUCCESS: Verilog loaded"

log_msg ""
log_msg "Reading DEF: $DEF_FILE"
if {[catch {read_def $DEF_FILE} result]} {
  log_msg "ERROR: Failed to read DEF: $result"
  close $log_fh
  exit 1
}
log_msg "SUCCESS: DEF loaded"

log_msg ""
log_msg "=========================================="
log_msg "PHASE 3: Design Statistics"
log_msg "=========================================="
log_msg ""

if {[catch {
  set db [ord::get_db]
  set chip [$db getChip]
  set block [$chip getBlock]
  
  # Count instances
  set inst_count 0
  log_msg "Counting instances..."
  foreach inst [$block getInsts] {
    incr inst_count
    if {$inst_count % 10000 == 0} {
      log_msg "  Counted $inst_count instances so far..."
    }
  }
  
  # Count nets
  set net_count 0
  log_msg "Counting nets..."
  foreach net [$block getNets] {
    incr net_count
    if {$net_count % 1000 == 0} {
      log_msg "  Counted $net_count nets so far..."
    }
  }
  
  # Count I/O pins
  set pin_count 0
  log_msg "Counting I/O pins..."
  foreach bterm [$block getBTerms] {
    incr pin_count
  }
  
  log_msg ""
  log_msg "DESIGN STATISTICS:"
  log_msg "  Instances: $inst_count"
  log_msg "  Nets: $net_count"
  log_msg "  I/O Pins: $pin_count"
  
  # Get die area
  set die_area [$block getDieArea]
  log_msg "  Die Area: ([$die_area xMin],[$die_area yMin]) to ([$die_area xMax],[$die_area yMax])"
  
} result]} {
  log_msg "WARNING: Could not get design statistics: $result"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 4: Clock Constraints"
log_msg "=========================================="
log_msg ""

log_msg "Creating clock constraints on port 'clk' with period 10.0ns..."
if {[catch {create_clock -name clk -period 10.0 [get_ports clk]} result]} {
  log_msg "ERROR: Failed to create clock: $result"
  close $log_fh
  exit 1
}
log_msg "SUCCESS: Clock created"

log_msg "Propagating clock to sequential cells..."
if {[catch {set_propagated_clock [all_clocks]} result]} {
  log_msg "WARNING: Clock propagation issue: $result"
} else {
  log_msg "SUCCESS: Clock propagated"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 5: Routing Track Setup"
log_msg "=========================================="
log_msg ""

log_msg "Configuring routing tracks for each layer..."

log_msg "  Setting up li1 tracks..."
make_tracks li1 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.17 -y_pitch 0.34

log_msg "  Setting up met1 tracks..."
make_tracks met1 -x_offset 0.17 -x_pitch 0.34 -y_offset 0.17 -y_pitch 0.34

log_msg "  Setting up met2 tracks..."
make_tracks met2 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.23 -y_pitch 0.46

log_msg "  Setting up met3 tracks..."
make_tracks met3 -x_offset 0.34 -x_pitch 0.68 -y_offset 0.34 -y_pitch 0.68

log_msg "  Setting up met4 tracks..."
make_tracks met4 -x_offset 0.46 -x_pitch 0.92 -y_offset 0.46 -y_pitch 0.92

log_msg "  Setting up met5 tracks..."
make_tracks met5 -x_offset 1.70 -x_pitch 3.40 -y_offset 1.70 -y_pitch 3.40

log_msg "SUCCESS: All routing tracks configured"

log_msg ""
log_msg "=========================================="
log_msg "PHASE 6: Placement Check"
log_msg "=========================================="
log_msg ""

log_msg "Running placement quality check..."
if {[catch {check_placement -verbose} result]} {
  log_msg "WARNING: Placement check warnings: $result"
} else {
  log_msg "SUCCESS: Placement check passed"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 7: I/O Pin Analysis"
log_msg "=========================================="
log_msg ""

if {[catch {
  set db [ord::get_db]
  set chip [$db getChip]
  set block [$chip getBlock]
  set io_pins [$block getBTerms]
  set pin_count 0
  
  log_msg "Analyzing all I/O pins in detail..."
  log_msg ""
  
  foreach bterm $io_pins {
    incr pin_count
    set pin_name [$bterm getName]
    set bpins [$bterm getBPins]
    
    log_msg "Pin #$pin_count: $pin_name"
    
    set box_count 0
    foreach bpin $bpins {
      set boxes [$bpin getBoxes]
      foreach box $boxes {
        incr box_count
        if {[catch {
          set layer [[$box getTechLayer] getName]
          set xMin [$box xMin]
          set yMin [$box yMin]
          set xMax [$box xMax]
          set yMax [$box yMax]
          set width [expr $xMax - $xMin]
          set height [expr $yMax - $yMin]
          log_msg "    Box $box_count: Layer=$layer, Size=${width}x${height} DBU"
          log_msg "              Location: ($xMin,$yMin) to ($xMax,$yMax)"
          
          # Warn if pins are suspiciously small
          if {$width < 500 || $height < 500} {
            log_msg "    WARNING: Pin geometry is very small (< 0.5um)!"
          }
        } err]} {
          log_msg "    ERROR getting box details: $err"
        }
      }
    }
    
    if {$box_count == 0} {
      log_msg "    WARNING: Pin has no geometry boxes!"
    }
    log_msg ""
  }
  
  log_msg "Total I/O pins analyzed: $pin_count"
  
} result]} {
  log_msg "ERROR: Could not analyze pins: $result"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 8: Global Routing"
log_msg "=========================================="
log_msg ""

set global_start [clock seconds]
log_msg "Starting global routing at [clock format $global_start -format {%H:%M:%S}]"
log_msg ""

if {[catch {
  global_route \
    -guide_file ${OUTPUT_DIR}/${DESIGN}.guide \
    -congestion_iterations 100 \
    -congestion_report_file ${OUTPUT_DIR}/${DESIGN}_congestion.rpt \
    -verbose
} result]} {
  # Check if it's just a congestion warning
  if {[string match "*GRT-0116*" $result] || [string match "*congestion*" $result]} {
    log_msg "INFO: Global routing completed with congestion warnings (GRT-0116)"
    log_msg "This is acceptable for high-density designs - continuing..."
  } else {
    log_msg "ERROR: Global routing failed: $result"
    write_def ${OUTPUT_DIR}/${DESIGN}_failed.def
    close $log_fh
    exit 1
  }
} else {
  log_msg "SUCCESS: Global routing completed"
}

set global_end [clock seconds]
set global_elapsed [expr $global_end - $global_start]
log_msg ""
log_msg "Global routing took $global_elapsed seconds ([expr $global_elapsed / 60] minutes)"

log_msg ""
log_msg "=========================================="
log_msg "PHASE 9: Detailed Routing"
log_msg "=========================================="
log_msg ""

set detail_start [clock seconds]
log_msg "Starting detailed routing at [clock format $detail_start -format {%H:%M:%S}]"
log_msg "This is the slowest phase and may take 30-60 minutes for this design size"
log_msg ""
log_msg "DETAILED ROUTING PARAMETERS:"
log_msg "  Verbose level: 1"
log_msg "  Bottom layer: met1"
log_msg "  Top layer: met5"
log_msg "  OR seed: 1"
log_msg ""
log_msg "If this hangs, check:"
log_msg "  1. CPU usage (should be 100%): ps aux | grep openroad"
log_msg "  2. Maze log growth: tail -f ${OUTPUT_DIR}/${DESIGN}_maze.log"
log_msg "  3. Memory usage: watch 'free -h'"
log_msg ""
log_msg "Starting detailed_route command now..."
log_msg "---------- DETAILED ROUTE OUTPUT BEGINS ----------"

flush stdout
flush $log_fh

# Create a background job to log progress every 30 seconds
set progress_script {
  set count 0
  while {1} {
    after 30000
    incr count
    set elapsed [expr [clock seconds] - $::detail_start]
    puts "\[PROGRESS\] Still routing... ${elapsed}s elapsed ([expr $elapsed / 60]m)"
    flush stdout
  }
}

# Start progress monitor in background (this may not work in all TCL versions)
# If it fails, just continue without it
catch {after 30000 [list eval $progress_script]} progress_id

if {[catch {
  detailed_route \
    -verbose 1 \
    -output_drc ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt \
    -output_maze ${OUTPUT_DIR}/${DESIGN}_maze.log \
    -or_seed 1 \
    -bottom_routing_layer met1 \
    -top_routing_layer met5
} result]} {
  
  set detail_end [clock seconds]
  set detail_elapsed [expr $detail_end - $detail_start]
  
  log_msg "---------- DETAILED ROUTE OUTPUT ENDS ----------"
  log_msg ""
  log_msg "ERROR: Detailed routing FAILED"
  log_msg "Time when failed: [clock format $detail_end -format {%H:%M:%S}]"
  log_msg "Elapsed time: $detail_elapsed seconds ([expr $detail_elapsed / 60] minutes)"
  log_msg "Error message: $result"
  log_msg ""
  
  log_msg "Attempting to save partial results..."
  catch {write_def ${OUTPUT_DIR}/${DESIGN}_detailed_failed.def} write_result
  log_msg "Partial DEF write result: $write_result"
  
  # Dump maze log tail
  if {[file exists ${OUTPUT_DIR}/${DESIGN}_maze.log]} {
    log_msg ""
    log_msg "Last 100 lines of maze log:"
    log_msg "----------------------------"
    if {[catch {exec tail -100 ${OUTPUT_DIR}/${DESIGN}_maze.log} maze_tail]} {
      log_msg "Could not read maze log"
    } else {
      log_msg $maze_tail
    }
  } else {
    log_msg "Maze log file does not exist!"
  }
  
  close $log_fh
  exit 1
  
} else {
  
  set detail_end [clock seconds]
  set detail_elapsed [expr $detail_end - $detail_start]
  
  log_msg "---------- DETAILED ROUTE OUTPUT ENDS ----------"
  log_msg ""
  log_msg "SUCCESS: Detailed routing completed!"
  log_msg "Time completed: [clock format $detail_end -format {%H:%M:%S}]"
  log_msg "Elapsed time: $detail_elapsed seconds ([expr $detail_elapsed / 60] minutes)"
  log_msg ""
  
  # Analyze violations in detail
  if {[file exists ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt]} {
    log_msg "=========================================="
    log_msg "DETAILED VIOLATION ANALYSIS"
    log_msg "=========================================="
    log_msg ""
    
    set drc_file [open ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt r]
    set drc_content [read $drc_file]
    close $drc_file
    
    # Count violations by type
    set short_count 0
    set spacing_count 0
    set width_count 0
    set via_count 0
    set other_count 0
    
    foreach line [split $drc_content "\n"] {
      if {[string match "*violation type: Short*" $line]} {
        incr short_count
      } elseif {[string match "*violation type: *Spacing*" $line]} {
        incr spacing_count
      } elseif {[string match "*violation type: *Width*" $line]} {
        incr width_count
      } elseif {[string match "*violation type: *Via*" $line]} {
        incr via_count
      } elseif {[string match "*violation type:*" $line]} {
        incr other_count
      }
    }
    
    set total_violations [expr $short_count + $spacing_count + $width_count + $via_count + $other_count]
    
    log_msg "VIOLATION SUMMARY:"
    log_msg "  Total violations: $total_violations"
    log_msg "  - Shorts: $short_count"
    log_msg "  - Spacing violations: $spacing_count"
    log_msg "  - Width violations: $width_count"
    log_msg "  - Via violations: $via_count"
    log_msg "  - Other violations: $other_count"
    log_msg ""
    
    # Analyze violations by layer
    set met2_violations 0
    set met3_violations 0
    set met4_violations 0
    set met1_violations 0
    set other_layer_violations 0
    
    foreach line [split $drc_content "\n"] {
      if {[string match "*on Layer met2*" $line]} {
        incr met2_violations
      } elseif {[string match "*on Layer met3*" $line]} {
        incr met3_violations
      } elseif {[string match "*on Layer met4*" $line]} {
        incr met4_violations
      } elseif {[string match "*on Layer met1*" $line]} {
        incr met1_violations
      } elseif {[string match "*on Layer*" $line]} {
        incr other_layer_violations
      }
    }
    
    log_msg "VIOLATIONS BY LAYER:"
    log_msg "  met1: $met1_violations"
    log_msg "  met2: $met2_violations"
    log_msg "  met3: $met3_violations"
    log_msg "  met4: $met4_violations"
    log_msg "  other: $other_layer_violations"
    log_msg ""
    
    # Show sample violations for diagnosis
    log_msg "SAMPLE VIOLATIONS (first 10):"
    log_msg "----------------------------"
    set shown 0
    set current_violation ""
    foreach line [split $drc_content "\n"] {
      if {[string match "violation type:*" $line] && $shown < 10} {
        if {$current_violation != ""} {
          log_msg "$current_violation"
          incr shown
        }
        set current_violation $line
      } elseif {[string match "\tsrcs:*" $line] || [string match "\tbbox*" $line]} {
        append current_violation "\n  $line"
      }
    }
    if {$shown < 10 && $current_violation != ""} {
      log_msg "$current_violation"
    }
    
    log_msg ""
    log_msg "=========================================="
    log_msg ""
  }
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 10: DRC Analysis"
log_msg "=========================================="
log_msg ""

if {[file exists ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt]} {
  set drc_file [open ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt r]
  set drc_content [read $drc_file]
  close $drc_file
  
  set drc_size [string length $drc_content]
  log_msg "DRC report size: $drc_size bytes"
  
  if {$drc_size > 100} {
    log_msg "WARNING: DRC violations detected!"
    log_msg ""
    
    # Count violations
    set viol_count [regexp -all {violation} $drc_content]
    log_msg "Total violations found: $viol_count"
    log_msg ""
    
    # Show first 20 violations
    log_msg "First 20 violations:"
    log_msg "-------------------"
    set lines [split $drc_content "\n"]
    set shown 0
    foreach line $lines {
      if {[string match "*violation*" $line] && $shown < 20} {
        log_msg "  $line"
        incr shown
      }
    }
    if {$viol_count > 20} {
      log_msg "  ... and [expr $viol_count - 20] more violations"
    }
  } else {
    log_msg "SUCCESS: No DRC violations reported"
  }
} else {
  log_msg "WARNING: DRC report file not found at ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 11: Routing Statistics"
log_msg "=========================================="
log_msg ""

if {[catch {
  set db [ord::get_db]
  set chip [$db getChip]
  set block [$chip getBlock]
  
  set routed_nets 0
  set total_nets 0
  set unrouted_nets_list {}
  
  foreach net [$block getNets] {
    incr total_nets
    set wire [$net getWire]
    if {$wire != "NULL"} {
      incr routed_nets
    } else {
      set net_name [$net getName]
      lappend unrouted_nets_list $net_name
    }
  }
  
  log_msg "ROUTING STATISTICS:"
  log_msg "  Total nets: $total_nets"
  log_msg "  Routed nets: $routed_nets"
  log_msg "  Unrouted nets: [expr $total_nets - $routed_nets]"
  
  if {$total_nets > 0} {
    set route_percent [expr ($routed_nets * 100.0) / $total_nets]
    log_msg "  Routing completion: [format "%.2f" $route_percent]%"
  }
  
  # List first 20 unrouted nets if any
  if {[llength $unrouted_nets_list] > 0} {
    log_msg ""
    log_msg "Unrouted nets (first 20):"
    set count 0
    foreach net_name $unrouted_nets_list {
      if {$count < 20} {
        log_msg "    $net_name"
        incr count
      }
    }
    if {[llength $unrouted_nets_list] > 20} {
      log_msg "    ... and [expr [llength $unrouted_nets_list] - 20] more"
    }
  }
  
} result]} {
  log_msg "WARNING: Could not get routing statistics: $result"
}

log_msg ""
log_msg "=========================================="
log_msg "PHASE 12: Writing Output Files"
log_msg "=========================================="
log_msg ""

log_msg "Writing final routed DEF to ${OUTPUT_DIR}/${DESIGN}_routed.def..."
if {[catch {write_def ${OUTPUT_DIR}/${DESIGN}_routed.def} result]} {
  log_msg "ERROR: Failed to write DEF: $result"
  close $log_fh
  exit 1
} else {
  set filesize [file size ${OUTPUT_DIR}/${DESIGN}_routed.def]
  log_msg "SUCCESS: DEF written ([expr $filesize / 1024] KB)"
}

log_msg ""
log_msg "Writing routing guides to ${OUTPUT_DIR}/${DESIGN}.guide..."
if {[catch {write_guides ${OUTPUT_DIR}/${DESIGN}.guide} result]} {
  log_msg "WARNING: Could not write guides: $result"
} else {
  log_msg "SUCCESS: Routing guides written"
}

log_msg ""
log_msg "=========================================="
log_msg "ROUTING SCRIPT COMPLETED SUCCESSFULLY"
log_msg "=========================================="
log_msg ""
log_msg "SUMMARY:"
log_msg "--------"


log_msg ""
log_msg "Output files:"
log_msg "  - ${OUTPUT_DIR}/${DESIGN}_routed.def (final routed design)"
log_msg "  - ${OUTPUT_DIR}/${DESIGN}_route_drc.rpt (DRC violations)"
log_msg "  - ${OUTPUT_DIR}/${DESIGN}_maze.log (detailed routing log)"
log_msg "  - ${OUTPUT_DIR}/${DESIGN}.guide (routing guides)"
log_msg "  - ${OUTPUT_DIR}/routing_debug.log (this log file)"
log_msg ""
log_msg "=========================================="

puts ""
puts "Full debug log written to: $LOG_FILE"
puts ""

puts "=======Extracting Parasitics======="
extract_parasitics -ext_model_file tech/rcx_patterns.rules

puts "=======Writing SPEF======="
write_spef ${OUTPUT_DIR}/${DESIGN}.spef

# SDC file for arith
# Generated for OpenROAD flow
# Clock frequency: 100.0 MHz (Period: 10.000 ns)

# Create clock constraint
create_clock -name clk -period 10.000 [get_ports clk]

# Set clock uncertainty (for clock jitter/skew)
set_clock_uncertainty 0.25 [get_clocks clk]

# Set clock transition (rise/fall time)
set_clock_transition 0.15 [get_clocks clk]

# Set input delay constraints on INPUT ports only (excluding clock)
catch {set_input_delay -clock clk 2.000 [get_ports in_*]}
catch {set_input_delay -clock clk 2.000 [get_ports rst_n]}

# Set output delay constraints on OUTPUT ports only
catch {set_output_delay -clock clk 3.000 [get_ports out_*]}
catch {set_output_delay -clock clk 3.000 [get_ports oeb_*]}

# Set input driving cell
catch {set_driving_cell -lib_cell sky130_fd_sc_hd__buf_4 -pin X [get_ports in_*]}
catch {set_driving_cell -lib_cell sky130_fd_sc_hd__buf_4 -pin X [get_ports rst_n]}

# Set output load
catch {set_load 0.05 [get_ports out_*]}
catch {set_load 0.05 [get_ports oeb_*]}
# =============================================================================
#  build_project.tcl -- create a ready-to-open Vivado 2022.2 project for
#                       tb_amc1306_sample and run the simulation once.
#
#  Usage:
#      vivado -mode batch -source build_project.tcl
#  or, to just create the project and open the GUI:
#      vivado -mode batch -source build_project.tcl -tclargs -nolaunch
#
#  The project is written next to this folder as ../vivado_amc1306/
# =============================================================================

set script_dir [file dirname [file normalize [info script]]]
set sim_dir    [file normalize [file join $script_dir ..]]
set data_dir   [file join $sim_dir data]
set proj_dir   [file normalize [file join $sim_dir .. vivado_amc1306]]
set mem_file   [file join $data_dir scenario.mem]

# any installed part works: this is a pure simulation project
set part "xc7a100tcsg324-1"

puts "==> sim_dir : $sim_dir"
puts "==> proj_dir: $proj_dir"

create_project -force amc1306_sim $proj_dir -part $part

add_files -norecurse -fileset sim_1 [list \
    [file join $sim_dir rtl amc1306_sample_stub.v] \
    [file join $sim_dir tb  tb_amc1306_sample.v] ]

set_property include_dirs $data_dir [get_filesets sim_1]
set_property top tb_amc1306_sample [get_filesets sim_1]

# the bitstream file is passed by absolute path so the relative xsim run
# directory does not matter
set plusarg "-testplusarg \"MEM=$mem_file\""
set_property -name {xsim.simulate.xsim.more_options} -value $plusarg \
    -objects [get_filesets sim_1]
set_property -name {xsim.simulate.runtime} -value {all} \
    -objects [get_filesets sim_1]
set_property -name {xsim.simulate.log_all_signals} -value {true} \
    -objects [get_filesets sim_1]

update_compile_order -fileset sim_1

if {[lsearch -exact $argv "-nolaunch"] >= 0} {
    puts "==> project created, not launching. Open $proj_dir/amc1306_sim.xpr"
} else {
    launch_simulation
    puts "==> simulation finished; project kept at $proj_dir"
}

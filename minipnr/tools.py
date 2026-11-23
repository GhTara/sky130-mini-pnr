"""Check layout rules with Magic, compare circuit connections with Netgen
and simulate the extracted circuit with ngspice.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Pdk:
    root: str                     # directory that contains sky130A/

    @property
    def base(self):
        return os.path.join(self.root, "sky130A")

    @property
    def magic_tech(self):
        return os.path.join(self.base, "libs.tech/magic/sky130A.tech")

    @property
    def magicrc(self):
        return os.path.join(self.base, "libs.tech/magic/sky130A.magicrc")

    @property
    def mag_dir(self):
        return os.path.join(self.base, "libs.ref/sky130_fd_sc_hd/mag")

    @property
    def lef(self):
        return os.path.join(self.base, "libs.ref/sky130_fd_sc_hd/lef/sky130_fd_sc_hd.lef")

    @property
    def cell_spice(self):
        return os.path.join(self.base, "libs.ref/sky130_fd_sc_hd/spice/sky130_fd_sc_hd.spice")

    @property
    def netgen_setup(self):
        return os.path.join(self.base, "libs.tech/netgen/sky130A_setup.tcl")

    @property
    def ngspice_lib(self):
        return os.path.join(self.base, "libs.tech/ngspice/sky130.lib.spice")

    def check(self):
        for path in (self.magic_tech, self.mag_dir, self.lef, self.cell_spice,
                     self.netgen_setup, self.ngspice_lib):
            if not os.path.exists(path):
                raise FileNotFoundError(f"PDK file missing: {path}")


def find_tool(name):
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"'{name}' not found on PATH (see environment.yml)")
    return path


def run(command, cwd, log_name, timeout=600):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            timeout=timeout)
    with open(os.path.join(cwd, log_name), "w") as handle:
        handle.write(result.stdout)
        handle.write(result.stderr)
    return result


# ----- Magic ------------------------------------------------------------

MAGIC_SCRIPT = """\
set PDKPATH {pdk_base}
tech load {tech}
addpath {mag_dir}
load {top}
select top cell
drc euclidean on
drc style drc(full)
drc check
drc catchup
set f [open {drc_report} w]
puts $f "total [drc list count total]"
foreach {{why boxes}} [drc listall why] {{
    puts $f "[llength $boxes]\\t$why"
}}
close $f
{extract_and_write}
quit -noprompt
"""

EXTRACT_AND_WRITE = """\
extract all
ext2spice lvs
ext2spice -o {top}_extracted.spice
gds write {top}.gds
"""


def run_magic(pdk, work_dir, top, drc_report, extract=True, write_gds=False):
    """DRC (and optionally extraction + GDS) of work_dir/<top>.mag."""
    if extract:
        extra = EXTRACT_AND_WRITE.format(top=top)
    elif write_gds:
        extra = f"gds write {top}.gds\n"
    else:
        extra = ""
    script = MAGIC_SCRIPT.format(
        pdk_base=pdk.base, tech=pdk.magic_tech, mag_dir=pdk.mag_dir, top=top,
        drc_report=drc_report, extract_and_write=extra)
    script_name = f"magic_{top}.tcl"
    with open(os.path.join(work_dir, script_name), "w") as handle:
        handle.write(script)
    env_command = [find_tool("magic"), "-dnull", "-noconsole", "-rcfile", "/dev/null",
                   script_name]
    result = run(env_command, work_dir, f"magic_{top}.log")
    report_path = os.path.join(work_dir, drc_report)
    if not os.path.exists(report_path):
        raise RuntimeError(f"magic failed, see {work_dir}/magic_{top}.log")
    return parse_drc_report(report_path), result


GDS_DRC_SCRIPT = """\
tech load {tech}
gds read {gds}
load {top}
select top cell
drc euclidean on
drc style drc(full)
drc check
drc catchup
set f [open {drc_report} w]
puts $f "total [drc list count total]"
foreach {{why boxes}} [drc listall why] {{
    puts $f "[llength $boxes]\\t$why"
}}
close $f
quit -noprompt
"""


def run_gds_drc(pdk, work_dir, top, drc_report):
    """DRC of work_dir/<top>.gds, read back into a fresh Magic session.

    The exported GDS includes mask layers generated from cell properties.
    At subcell boundaries these can affect checks such as the resistor
    implant (RPM) width rule.
    """
    script_name = f"gds_drc_{top}.tcl"
    with open(os.path.join(work_dir, script_name), "w") as handle:
        handle.write(GDS_DRC_SCRIPT.format(tech=pdk.magic_tech, gds=f"{top}.gds", top=top,
                                           drc_report=drc_report))
    run([find_tool("magic"), "-dnull", "-noconsole", "-rcfile", "/dev/null", script_name],
        work_dir, f"gds_drc_{top}.log")
    report_path = os.path.join(work_dir, drc_report)
    if not os.path.exists(report_path):
        raise RuntimeError(f"magic GDS DRC failed, see {work_dir}/gds_drc_{top}.log")
    return parse_drc_report(report_path)


def parse_drc_report(path):
    """Return {"total": int, "rules": {rule text: count}}."""
    rules = {}
    total = None
    with open(path) as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.startswith("total "):
                total = int(line.split()[1])
            elif "\t" in line:
                count, why = line.split("\t", 1)
                rules[why] = int(count)
    return {"total": total, "rules": rules}


# ----- Netgen -----------------------------------------------------------

def read_subckt_ports(spice_path, names):
    """Port order of the requested .subckt definitions."""
    ports = {}
    with open(spice_path) as handle:
        for line in handle:
            words = line.split()
            if len(words) >= 2 and words[0].lower() == ".subckt" and words[1] in names:
                ports[words[1]] = words[2:]
    return ports


def write_schematic(path, pdk, circuit, top):
    """Reference netlist built from the .bench file (not from the layout)."""
    cells = sorted({gate.cell for gate in circuit.gates})
    ports = read_subckt_ports(pdk.cell_spice, cells)
    supply = {"VPWR": "VPWR", "VPB": "VPWR", "VGND": "VGND", "VNB": "VGND"}

    lines = [f"* reference netlist of {circuit.name}, generated from the .bench file",
             f".include {pdk.cell_spice}",
             f".subckt {top} " + " ".join(circuit.inputs + circuit.outputs + ["VPWR", "VGND"])]
    for gate in circuit.gates:
        nets = [gate.pin_nets.get(pin, supply.get(pin)) for pin in ports[gate.cell]]
        lines.append(f"X{gate.name} " + " ".join(nets) + f" {gate.cell}")
    lines += [".ends", ".end"]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


def run_lvs(pdk, work_dir, top, layout_spice, schematic_spice):
    command = [find_tool("netgen"), "-batch", "lvs",
               f"{layout_spice} {top}", f"{schematic_spice} {top}",
               pdk.netgen_setup, "lvs_report.txt"]
    run(command, work_dir, "netgen.log")
    with open(os.path.join(work_dir, "lvs_report.txt")) as handle:
        report = handle.read()
    # Netgen ends its report with "Final result: Circuits match uniquely."
    final = re.findall(r"Final result:\s*(.*)", report)
    final_text = final[-1].strip() if final else "no final result found"
    # Netgen can match the connections and still report a wrong device size.
    property_errors = "Property errors were found" in report
    return {"final_result": final_text, "property_errors": property_errors,
            "match": final_text.startswith("Circuits match uniquely") and not property_errors}


# ----- ngspice ----------------------------------------------------------

def write_testbench(path, pdk, circuit, top, extracted_spice, port_order, period_ns):
    """Apply all 2^n input vectors, one every `period_ns`, as binary counter."""
    n_inputs = len(circuit.inputs)
    n_vectors = 2 ** n_inputs
    rise_ns = 0.05
    lines = [f"* post-layout functional test of {top} (no parasitics)",
             f'.lib "{pdk.ngspice_lib}" tt',
             f".include {extracted_spice}",
             f"Xdut " + " ".join(port_order) + f" {top}",
             "Vvpwr VPWR 0 1.8",
             "Vvgnd VGND 0 0"]
    for bit, net in enumerate(circuit.inputs):
        width = period_ns * 2 ** bit
        lines.append(f"V{net} {net} 0 PULSE(0 1.8 {width}n {rise_ns}n {rise_ns}n "
                     f"{width - rise_ns}n {2 * width}n)")
    stop = period_ns * n_vectors
    outputs = " ".join(f"v({net})" for net in circuit.outputs)
    lines += [f".tran 10p {stop}n",
              ".control", "set wr_singlescale", "run",
              f"wrdata sim_outputs.txt {outputs}", ".endc", ".end"]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return n_vectors


def run_simulation(work_dir, testbench):
    run([find_tool("ngspice"), "-b", testbench], work_dir, "ngspice.log")
    data_path = os.path.join(work_dir, "sim_outputs.txt")
    if not os.path.exists(data_path):
        raise RuntimeError(f"ngspice failed, see {work_dir}/ngspice.log")
    rows = []
    with open(data_path) as handle:
        for line in handle:
            values = line.split()
            try:
                rows.append([float(v) for v in values])
            except ValueError:
                continue    # header line
    return rows

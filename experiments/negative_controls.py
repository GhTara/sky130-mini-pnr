"""Three deliberate errors test the layout rules, wiring check and logic
simulation on a completed gate layout. A successful run detects all three.

    python experiments/negative_controls.py benchmarks/c17.bench work/c17
"""

import argparse
import copy
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.flow import check_simulation, load_design  # noqa: E402
from minipnr.tools import (Pdk, run_lvs, run_magic, run_simulation,  # noqa: E402
                           write_schematic)


def main(bench, run_dir):
    pdk = Pdk(os.environ["PDK_ROOT"])
    circuit, _ = load_design(bench, pdk)
    top = circuit.name
    work = os.path.join(run_dir, "negative_controls")
    os.makedirs(work, exist_ok=True)
    results = {}

    # Keep the narrow strip away from the existing wires.
    with open(os.path.join(run_dir, f"{top}.mag")) as handle:
        lines = handle.read().splitlines()
    bad_cell = f"{top}_bad"
    index = lines.index("<< metal1 >>")
    lines.insert(index + 1, "rect 0 -2000 400 -1980")   # 2.0 um x 0.10 um
    with open(os.path.join(work, f"{bad_cell}.mag"), "w") as handle:
        handle.write("\n".join(lines) + "\n")
    drc, _ = run_magic(pdk, work, bad_cell, "drc_bad.txt", extract=False)
    results["drc_injected_narrow_wire"] = {"errors": drc["total"], "detected": drc["total"] > 0}

    # Swap one input from each of the first two gates in the reference circuit.
    wrong = copy.deepcopy(circuit)
    gate_a, gate_b = wrong.gates[0], wrong.gates[1]
    pin_a, pin_b = sorted(gate_a.pin_nets)[0], sorted(gate_b.pin_nets)[0]
    gate_a.pin_nets[pin_a], gate_b.pin_nets[pin_b] = gate_b.pin_nets[pin_b], gate_a.pin_nets[pin_a]
    write_schematic(os.path.join(work, "wrong_schematic.spice"), pdk, wrong, top)
    shutil.copy(os.path.join(run_dir, f"{top}_extracted.spice"), work)
    lvs = run_lvs(pdk, work, top, f"{top}_extracted.spice", "wrong_schematic.spice")
    results["lvs_swapped_inputs"] = {"final_result": lvs["final_result"],
                                     "detected": not lvs["match"]}

    # Keep the simulated circuit intact and change the expected logic.
    rows = run_simulation(run_dir, "testbench.spice")
    wrong_function = copy.deepcopy(circuit)
    wrong_function.gates[0].kind = "NOR"
    check = check_simulation(wrong_function, rows, 4.0, 2 ** len(circuit.inputs))
    results["simulation_wrong_reference"] = {"mismatches": len(check["mismatches"]),
                                             "detected": not check["pass"]}

    print(json.dumps(results, indent=2))
    with open(os.path.join(run_dir, "negative_controls.json"), "w") as handle:
        json.dump(results, handle, indent=2)
    return all(item["detected"] for item in results.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bench", help="the .bench file used to generate the layout")
    parser.add_argument("run_dir", help="output directory from run_flow.py")
    args = parser.parse_args()
    ok = main(args.bench, args.run_dir)
    sys.exit(0 if ok else 1)

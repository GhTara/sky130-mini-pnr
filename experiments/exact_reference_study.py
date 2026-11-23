"""Annealing and CP-SAT use the same placement score. runs.csv contains the
annealing scores. exact.csv contains solver costs, bounds and layout checks.

    python experiments/exact_reference_study.py --out work/exact --time-limit 600 --workers 12
"""

import argparse
import csv
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.bench import parse_bench  # noqa: E402
from minipnr.device_flow import (SHARED_RING_SPACING, best_grid_offset,  # noqa: E402
                                 generate_device_cells, run_device_flow)
from minipnr.device_netlist import DEVICE_TYPES, expand_circuit  # noqa: E402
from minipnr.device_placer import place_devices  # noqa: E402
from minipnr.exact_placer import solve_exact  # noqa: E402
from minipnr.tools import Pdk  # noqa: E402

CIRCUIT_LABELS = {"inv": "Inverter", "nand2": "NAND2", "and2": "AND2",
                  "nand_chain2": "Two-NAND chain", "nand_chain3": "Three-NAND chain",
                  "xor2_nand": "XOR2 (NAND2)", "c17": "c17", "full_adder_nand": "Full adder (NAND2)"}
DESIGNS = list(CIRCUIT_LABELS)
SCHEDULES = {"annealing": 50, "annealing_4x": 200}


def load_design(name):
    path = os.path.join(os.path.dirname(__file__), "..", "benchmarks", name + ".bench")
    with open(path) as handle:
        return path, expand_circuit(parse_bench(handle.read(), name))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--time-limit", type=float, default=600)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--designs", nargs="+", default=DESIGNS)
    args = parser.parse_args()
    pdk = Pdk(os.environ["PDK_ROOT"])
    os.makedirs(args.out, exist_ok=True)

    cell_dir = os.path.join(args.out, "cells")
    os.makedirs(cell_dir, exist_ok=True)
    params = {k: dict(DEVICE_TYPES[k]["generator"], guard=0) for k in ("nfet", "rload")}
    layouts = generate_device_cells(pdk, cell_dir, params)
    offsets = {kind: best_grid_offset(layout) for kind, layout in layouts.items()}

    runs, exact_rows = [], []
    for design in args.designs:
        bench, devices = load_design(design)
        best = None
        for schedule, moves in SCHEDULES.items():
            for seed in range(1, args.seeds + 1):
                started = time.perf_counter()
                placement, _, info = place_devices(devices, layouts, offsets,
                                                   SHARED_RING_SPACING, "annealing", seed,
                                                   moves_per_device=moves)
                seconds = time.perf_counter() - started
                runs.append({"design": design, "devices": len(devices), "schedule": schedule,
                             "seed": seed, "cost": info["placed_cost"],
                             "area_um2": info["placed_area_um2"],
                             "hpwl_um": info["placed_hpwl_um"], "seconds": round(seconds, 2)})
                if best is None or info["placed_cost"] < best[0]:
                    best = (info["placed_cost"], placement)
        print(design, "best annealing cost", best[0], flush=True)

        placement, info = solve_exact(devices, layouts, offsets, SHARED_RING_SPACING,
                                      hint=best[1], time_limit_s=args.time_limit,
                                      workers=args.workers)
        row = {"design": design, "devices": len(devices), "best_annealing_cost": best[0]}
        row.update(info)
        stats, _ = run_device_flow(bench, os.path.join(args.out, "flow", design), pdk,
                                   placer="exact", guard="shared", given_placement=placement)
        row.update({"drc_errors": stats["drc"]["routed_total"],
                    "lvs_match": stats["lvs"]["match"], "sim_pass": stats["simulation"]["pass"],
                    "cluster_area_um2": stats["cluster_area_um2"],
                    "die_area_um2": stats["die_area_um2"],
                    "routed_wirelength_um": stats["routed_wirelength_um"]})
        print(row, flush=True)
        exact_rows.append(row)

    write_csv(os.path.join(args.out, "runs.csv"), runs)
    write_csv(os.path.join(args.out, "exact.csv"), exact_rows)
    summarize(runs, exact_rows, args.out)


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(runs, exact_rows, out_dir):
    seed_count = len({int(r["seed"]) for r in runs})
    lines = [f"Annealing costs show mean ± sample standard deviation (n={seed_count} per schedule). "
             "Moves are per device per temperature. Percentages use the CP-SAT cost as reference. "
             "[Annealing runs](runs.csv) · [Solver runs](exact.csv)", "",
             "| Circuit | Devices | Annealing, 50 moves | Annealing, 200 moves | CP-SAT cost | "
             "Lower bound | Proven optimal | 50 moves vs CP-SAT | 200 moves vs CP-SAT | "
             "Layout rules / wiring / simulation |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in exact_rows:
        design = row["design"]
        cp_cost = float(row["objective"])
        cells = []
        gaps = []
        for schedule in SCHEDULES:
            costs = [float(r["cost"]) for r in runs
                     if r["design"] == design and r["schedule"] == schedule]
            mean_cost = statistics.mean(costs)
            value = f"{mean_cost:.2f}"
            if len(costs) > 1:
                value += f" ± {statistics.stdev(costs):.2f}"
            cells.append(value)
            gaps.append(100 * (mean_cost / cp_cost - 1))
        proven = "yes" if row["status"] == "OPTIMAL" else "no"
        errors = int(row["drc_errors"])
        rules = "clean" if errors == 0 else str(errors)
        wiring = "match" if row["lvs_match"] in (True, "True") else "mismatch"
        simulation = "pass" if row["sim_pass"] in (True, "True") else "fail"
        checks = f"{rules} / {wiring} / {simulation}"
        bound = float(row["lower_bound"])
        circuit = CIRCUIT_LABELS.get(design, design)
        lines.append(f"| {circuit} | {row['devices']} | {cells[0]} | {cells[1]} | {cp_cost:.2f} | "
                     f"{bound:.2f} | {proven} | {gaps[0]:+.1f} % | {gaps[1]:+.1f} % | "
                     f"{checks} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "summary.md"), "w") as handle:
        handle.write(text)
    print(text)


if __name__ == "__main__":
    main()

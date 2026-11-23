"""Annealing restarts on four circuits. Each group contributes its best score to
runs.csv. The layout checks for the best result per circuit go to layouts.csv.

    python experiments/annealing_restarts.py --cells work/exact/cells --out work/restarts --jobs 10
"""

import argparse
import csv
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.bench import parse_bench  # noqa: E402
from minipnr.device_flow import (SHARED_RING_SPACING, best_grid_offset,  # noqa: E402
                                 run_device_flow)
from minipnr.device_netlist import expand_circuit  # noqa: E402
from minipnr.device_placer import DevicePlacer  # noqa: E402
from minipnr.devices import load_device  # noqa: E402
from minipnr.tools import Pdk  # noqa: E402

DESIGNS = ["nand_chain3", "xor2_nand", "c17", "full_adder_nand"]   # optimum not proven
RUNS = 25


def bench_path(design):
    return os.path.join(os.path.dirname(__file__), "..", "benchmarks", design + ".bench")


def best_of_runs(job):
    design, group, cells = job
    layouts = {k: load_device(os.path.join(cells, f"dev_{k}.mag"), f"dev_{k}")
               for k in ("nfet", "rload")}
    offsets = {kind: best_grid_offset(layout) for kind, layout in layouts.items()}
    with open(bench_path(design)) as handle:
        devices = expand_circuit(parse_bench(handle.read(), design))
    engine = DevicePlacer(devices, layouts, offsets, SHARED_RING_SPACING)
    best = None
    for run in range(RUNS):
        rng = random.Random(1000 * group + run)
        placement, _ = engine.anneal(engine.random_placement(rng), rng)
        cost = engine.cost(placement)
        if best is None or cost < best[0]:
            best = (cost, placement)
    best_cost, best_placement = best
    origins = {name: (x, y) for name, (x, y, _) in best_placement.items()}
    return design, group, best_cost, origins


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cells", required=True, help="cells directory from exact_reference_study.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    jobs = [(d, g, args.cells) for d in DESIGNS for g in range(1, 11)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(best_of_runs, jobs))

    rows = [{"design": d, "group": g, "runs": RUNS, "best_cost": round(c, 3)}
            for d, g, c, _ in results]
    layouts = []
    for design in DESIGNS:
        _, group, cost, placement = min((r for r in results if r[0] == design),
                                        key=lambda r: r[2])
        stats, _ = run_device_flow(bench_path(design), os.path.join(args.out, design),
                                   Pdk(os.environ["PDK_ROOT"]), placer="annealing_restarts",
                                   guard="shared", given_placement=placement)
        layouts.append({"design": design, "group": group, "best_cost": round(cost, 3),
                        "drc_errors": stats["drc"]["routed_total"],
                        "lvs_match": stats["lvs"]["match"],
                        "sim_pass": stats["simulation"]["pass"]})
        print(layouts[-1], flush=True)
    for name, table in (("runs.csv", rows), ("layouts.csv", layouts)):
        with open(os.path.join(args.out, name), "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)


if __name__ == "__main__":
    main()

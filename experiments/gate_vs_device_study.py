"""Three placement methods compare area and routed wire length for whole
NAND2 gates and individual devices.

    python experiments/gate_vs_device_study.py --out work/gate_vs_device --jobs 10
"""

import argparse
import csv
import json
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.bench import parse_bench  # noqa: E402
from minipnr.device_flow import (SHARED_RING_SPACING, best_grid_offset,  # noqa: E402
                                 generate_device_cells, run_device_flow)
from minipnr.device_netlist import DEVICE_TYPES, expand_circuit  # noqa: E402
from minipnr.device_placer import DevicePlacer  # noqa: E402
from minipnr.devices import load_device  # noqa: E402
from minipnr.gate_placer import GatePlacer, nand2_cells  # noqa: E402
from minipnr.tools import Pdk  # noqa: E402

DESIGNS = ["nand_chain3", "xor2_nand", "c17", "full_adder_nand"]
SEEDS = range(1, 11)
SCREEN_SEEDS = range(101, 106)
RUNS = 5
CELLS = 8
REFINE = {"start_scale": 0.05, "moves_per_device": 88}   # 77 steps * 88 ~ 135 steps * 50


def bench_path(design):
    return os.path.join(os.path.dirname(__file__), "..", "benchmarks", design + ".bench")


def load(design, cell_dir):
    layouts = {k: load_device(os.path.join(cell_dir, f"dev_{k}.mag"), f"dev_{k}")
               for k in ("nfet", "rload")}
    offsets = {kind: best_grid_offset(layout) for kind, layout in layouts.items()}
    with open(bench_path(design)) as handle:
        return expand_circuit(parse_bench(handle.read(), design)), layouts, offsets


def anneal_once(engine, seed, run):
    rng = random.Random(1000 * seed + run)
    placement, _ = engine.anneal(engine.random_placement(rng), rng)
    return engine.cost(placement), placement


def screen(job):
    design, index, cell, seed, cell_dir = job
    devices, layouts, offsets = load(design, cell_dir)
    engine = GatePlacer(devices, layouts, offsets, SHARED_RING_SPACING, cell)
    return design, index, anneal_once(engine, seed, 0)[0]


def search(job):
    design, setting, seed, cell, cell_dir = job
    devices, layouts, offsets = load(design, cell_dir)
    free = DevicePlacer(devices, layouts, offsets, SHARED_RING_SPACING)
    if setting == "device":
        results = [anneal_once(free, seed, run) for run in range(RUNS)]
    else:
        rigid = GatePlacer(devices, layouts, offsets, SHARED_RING_SPACING, cell)
        runs = RUNS if setting == "gate" else RUNS - 1
        results = [anneal_once(rigid, seed, run) for run in range(runs)]
    cost, placement = min(results, key=lambda r: r[0])
    if setting == "gate_device":
        placement, _ = free.anneal(placement, random.Random(1000 * seed + RUNS), **REFINE)
        cost = free.cost(placement)
    for name, state in placement.items():
        if not free.is_legal(placement, name, state):
            raise RuntimeError(f"illegal placement: {design} {setting} {seed}")
    row = {"design": design, "setting": setting, "seed": seed, "cost": round(cost, 3),
           "area_um2": round(free.area_um2(placement), 3),
           "hpwl_um": round(free.hpwl_um(placement), 3)}
    origins = {name: (x, y) for name, (x, y, _) in placement.items()}
    return row, origins


def layout(job):
    row, placement, out, pdk_root = job
    row = dict(row)
    work = os.path.join(out, "runs", "{design}_{setting}_{seed}".format(**row))
    try:
        stats, _ = run_device_flow(bench_path(row["design"]), work, Pdk(pdk_root),
                                   placer=row["setting"], seed=row["seed"], guard="shared",
                                   given_placement=placement)
    except Exception as error:
        row.update({"status": "failed", "error": str(error)[:120]})
        return row
    row.update({"status": "ok", "cluster_area_um2": stats["cluster_area_um2"],
                "die_area_um2": stats["die_area_um2"],
                "routed_wirelength_um": stats["routed_wirelength_um"],
                "drc_errors": stats["drc"]["routed_total"],
                "lvs_match": stats["lvs"]["match"], "sim_pass": stats["simulation"]["pass"],
                "error": ""})
    return row


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    pdk_root = os.environ["PDK_ROOT"]
    cell_dir = os.path.join(args.out, "cells")
    os.makedirs(cell_dir, exist_ok=True)
    params = {k: dict(DEVICE_TYPES[k]["generator"], guard=0) for k in ("nfet", "rload")}
    generate_device_cells(Pdk(pdk_root), cell_dir, params)

    cells = nand2_cells(*load("nand2", cell_dir), SHARED_RING_SPACING, CELLS)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        jobs = [(d, i, c["cell"], s, cell_dir) for d in DESIGNS
                for i, c in enumerate(cells) for s in SCREEN_SEEDS]
        screening = {}
        for design, index, cost in pool.map(screen, jobs):
            screening.setdefault(design, {}).setdefault(index, []).append(cost)
        chosen = {d: min(costs, key=lambda i: sum(costs[i])) for d, costs in screening.items()}
        with open(os.path.join(args.out, "cells.json"), "w") as handle:
            json.dump({"cells": cells, "screening_costs": screening, "chosen": chosen},
                      handle, indent=1)
        print("chosen cells", chosen, flush=True)

        jobs = [(d, setting, s, cells[chosen[d]]["cell"], cell_dir) for d in DESIGNS
                for setting in ("gate", "device", "gate_device") for s in SEEDS]
        found = list(pool.map(search, jobs))
        rows = list(pool.map(layout, [(row, placement, args.out, pdk_root)
                                      for row, placement in found]))
    write_csv(os.path.join(args.out, "runs.csv"), rows)


if __name__ == "__main__":
    main()

"""Device placement, resistor folding and guard-ring studies on c17 and a full adder.
Each study records its measurements in runs.csv.

    python experiments/device_placement_study.py --study placement --out work/placement --jobs 6
"""

import argparse
import csv
import json
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.device_flow import run_device_flow  # noqa: E402
from minipnr.tools import Pdk  # noqa: E402

DESIGNS = ["c17", "full_adder_nand"]
# setting -> (placer, wire weight, allowed resistor fold counts, seeded?, guard)
STUDIES = {
    "placement": {"row": ("row", 1.0, (2,), False, "device"),
                  "random": ("random", 1.0, (2,), True, "device"),
                  "annealing": ("annealing", 1.0, (2,), True, "device"),
                  "annealing_area": ("annealing", 0.0, (2,), True, "device")},
    "folding": {"folds_2": ("annealing", 1.0, (2,), True, "device"),
                "folds_3": ("annealing", 1.0, (3,), True, "device"),
                "folds_4": ("annealing", 1.0, (4,), True, "device"),
                "folds_5": ("annealing", 1.0, (5,), True, "device"),
                "folds_free": ("annealing", 1.0, (2, 3, 4, 5), True, "device")},
    "guard": {"own_row": ("row", 1.0, (2,), False, "device"),
              "own_annealing": ("annealing", 1.0, (2,), True, "device"),
              "shared_row": ("row", 1.0, (2,), False, "shared"),
              "shared_annealing": ("annealing", 1.0, (2,), True, "shared")},
}
SETTING_LABELS = {
    "row": "One row",
    "random": "Random placement",
    "annealing": "Annealing",
    "annealing_area": "Annealing, area only",
    "folds_2": "2 segments per resistor",
    "folds_3": "3 segments per resistor",
    "folds_4": "4 segments per resistor",
    "folds_5": "5 segments per resistor",
    "folds_free": "Placer chooses 2–5 segments per resistor",
    "own_row": "Individual rings, one row",
    "own_annealing": "Individual rings, annealing",
    "shared_row": "Shared ring, one row",
    "shared_annealing": "Shared ring, annealing",
}
COLUMNS = ["design", "setting", "seed", "status", "resistor_folds_used",
           "device_bbox_area_um2", "cluster_area_um2", "die_area_um2",
           "placed_hpwl_um", "routed_wirelength_um", "via1_count", "routing_iterations",
           "drc_errors", "lvs_match", "sim_pass", "error"]


def parse_seeds(text):
    first, last = text.split("-")
    return list(range(int(first), int(last) + 1))


def one_run(job):
    design, setting, seed, out_dir, pdk_root, config = job
    placer, wire_weight, folds, _, guard = config
    bench = os.path.join(os.path.dirname(__file__), "..", "benchmarks", design + ".bench")
    run_dir = os.path.join(out_dir, "runs", f"{design}_{setting}_{seed}")
    row = {"design": design, "setting": setting, "seed": seed}
    try:
        stats, _ = run_device_flow(bench, run_dir, Pdk(pdk_root), placer=placer, seed=seed,
                                   wire_weight=wire_weight, resistor_folds=folds,
                                   guard=guard)
    except RuntimeError as error:
        row.update({"status": "failed", "error": str(error)})
        return row, None
    row.update({
        "status": "ok", "error": "",
        "resistor_folds_used": " ".join(f"{k}x{v}" for k, v in
                                        stats["resistor_folds_used"].items()),
        "device_bbox_area_um2": stats["device_bbox_area_um2"],
        "cluster_area_um2": stats["cluster_area_um2"],
        "die_area_um2": stats["die_area_um2"],
        "placed_hpwl_um": stats["placement"].get("placed_hpwl_um", ""),
        "routed_wirelength_um": stats["routed_wirelength_um"],
        "via1_count": stats["via1_count"],
        "routing_iterations": stats["routing_iterations"],
        "drc_errors": stats["drc"]["routed_total"],
        "lvs_match": stats["lvs"]["match"],
        "sim_pass": stats["simulation"]["pass"]})
    return row, stats["placement"].get("cost_history")


def mean_std(values):
    if not values:
        return "–"
    if len(values) == 1:
        return f"{values[0]:.1f}"
    return f"{statistics.mean(values):.1f} ± {statistics.stdev(values):.1f}"


def summarize(rows, out_dir, settings):
    metrics = {"device_bbox_area_um2": "Device box (µm²)"}
    # The earlier placement and folding files only recorded device and die area.
    if all("cluster_area_um2" in r for r in rows if r["status"] == "ok"):
        metrics["cluster_area_um2"] = "Box including rings (µm²)"
    metrics.update({"die_area_um2": "Layout area (µm²)",
                    "routed_wirelength_um": "Routed wirelength (µm)",
                    "via1_count": "Vias between routing layers"})
    columns = ["Circuit", "Setting", "Runs", "Completed", *metrics.values(),
               "Layout rules passed", "Netlists matched", "Simulation passed"]
    lines = ["Means, sample standard deviations and check counts use completed runs. "
             "[Run data](runs.csv)", "",
             "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for design in DESIGNS:
        circuit = "Full adder (NAND2)" if design == "full_adder_nand" else design
        for setting in settings:
            group = [r for r in rows if r["design"] == design and r["setting"] == setting]
            if not group:
                continue
            completed = [r for r in group if r["status"] == "ok"]
            count = len(completed)
            cells = [circuit, SETTING_LABELS.get(setting, setting), str(len(group)),
                     f"{count}/{len(group)}"]
            for metric in metrics:
                cells.append(mean_std([float(r[metric]) for r in completed]))
            clean = sum(int(r["drc_errors"]) == 0 for r in completed)
            matched = sum(r["lvs_match"] in (True, "True") for r in completed)
            passed = sum(r["sim_pass"] in (True, "True") for r in completed)
            cells.extend([f"{clean}/{count}", f"{matched}/{count}", f"{passed}/{count}"])
            lines.append("| " + " | ".join(cells) + " |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "summary.md"), "w") as handle:
        handle.write(text)
    print(text)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--study", choices=list(STUDIES), default="placement")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", default="1-10")
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    pdk_root = os.environ["PDK_ROOT"]
    os.makedirs(args.out, exist_ok=True)

    settings = STUDIES[args.study]
    jobs = []
    for design in DESIGNS:
        for setting, config in settings.items():
            seeds = parse_seeds(args.seeds) if config[3] else [1]
            for seed in seeds:
                jobs.append((design, setting, seed, args.out, pdk_root, config))

    rows, histories = [], {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for row, history in pool.map(one_run, jobs):
            print({k: row.get(k) for k in COLUMNS if k != "error"}, row.get("error", ""),
                  flush=True)
            rows.append(row)
            if history and settings[row["setting"]][0] == "annealing":
                histories[f"{row['design']}_{row['setting']}_{row['seed']}"] = history

    with open(os.path.join(args.out, "runs.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in COLUMNS})
    with open(os.path.join(args.out, "cost_histories.json"), "w") as handle:
        json.dump(histories, handle)
    summarize(rows, args.out, settings)


if __name__ == "__main__":
    main()

"""Random and annealed gate placements use the same seeds and router. Completed
runs go to runs.csv and runtime errors to failures.txt.

    python experiments/seed_sweep.py --out work/seed_sweep --seeds 1-10
"""

import argparse
import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minipnr.flow import run_flow  # noqa: E402
from minipnr.tools import Pdk  # noqa: E402

DESIGNS = ["c17", "full_adder"]
COLUMNS = ["design", "placer", "seed", "hpwl_final_um", "routed_wirelength_um", "via1_count",
           "routing_iterations", "drc_errors", "lvs_match", "sim_pass"]


def parse_seeds(text):
    first, last = text.split("-")
    return list(range(int(first), int(last) + 1))


def summarize(rows, out_dir):
    lines = ["Completed runs, mean ± sample standard deviation. [Run data](runs.csv)", "",
             "| Circuit | Placer | Completed runs | Estimated length (HPWL, µm) | "
             "Routed wirelength (µm) | Vias between routing layers | Layout rules passed | "
             "Netlists matched | Simulation passed |",
             "|---|---|---|---|---|---|---|---|---|"]
    for design in DESIGNS:
        for placer in ("random", "annealing"):
            group = [r for r in rows if r["design"] == design and r["placer"] == placer]
            if not group:
                continue

            def mean_std(key):
                values = [float(r[key]) for r in group]
                if len(values) == 1:
                    return f"{values[0]:.1f}"
                return f"{statistics.mean(values):.1f} ± {statistics.stdev(values):.1f}"

            n = len(group)
            clean = sum(int(r["drc_errors"]) == 0 for r in group)
            lvs = sum(r["lvs_match"] in (True, "True") for r in group)
            sim = sum(r["sim_pass"] in (True, "True") for r in group)
            circuit = "Full adder" if design == "full_adder" else design
            lines.append(f"| {circuit} | {placer.capitalize()} | {n} | {mean_std('hpwl_final_um')} | "
                         f"{mean_std('routed_wirelength_um')} | {mean_std('via1_count')} | "
                         f"{clean}/{n} | {lvs}/{n} | {sim}/{n} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "summary.md"), "w") as handle:
        handle.write(text)
    print(text)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seeds", default="1-10")
    args = parser.parse_args()
    pdk = Pdk(os.environ["PDK_ROOT"])
    os.makedirs(args.out, exist_ok=True)

    rows = []
    failures = []
    for design in DESIGNS:
        bench = os.path.join(os.path.dirname(__file__), "..", "benchmarks", design + ".bench")
        for seed in parse_seeds(args.seeds):
            for placer in ("random", "annealing"):
                run_dir = os.path.join(args.out, "runs", f"{design}_{placer}_{seed}")
                try:
                    stats = run_flow(bench, run_dir, pdk, seed=seed,
                                     use_annealing=(placer == "annealing"))
                except RuntimeError as error:
                    print(f"{design} {placer} seed {seed}: {error}", flush=True)
                    failures.append((design, placer, seed, str(error)))
                    continue
                rows.append({
                    "design": design, "placer": placer, "seed": seed,
                    "hpwl_final_um": stats["hpwl_final_um"],
                    "routed_wirelength_um": stats["routed_wirelength_um"],
                    "via1_count": stats["via1_count"],
                    "routing_iterations": stats["routing_iterations"],
                    "drc_errors": stats["drc"]["routed_total"],
                    "lvs_match": stats["lvs"]["match"],
                    "sim_pass": stats["simulation"]["pass"],
                })
                print(rows[-1], flush=True)

    with open(os.path.join(args.out, "runs.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    summarize(rows, args.out)
    with open(os.path.join(args.out, "failures.txt"), "w") as handle:
        for failure in failures:
            handle.write(" ".join(str(item) for item in failure) + "\n")
    print(f"{len(failures)} runs failed (see failures.txt)")


if __name__ == "__main__":
    main()

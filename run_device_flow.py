"""Transistor and resistor placement, routing and layout checks on SKY130."""

import argparse
import json
import os

from minipnr.device_flow import run_device_flow
from minipnr.tools import Pdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench", help=".bench circuit using NOT, two-input NAND or two-input NOR")
    parser.add_argument("--out", required=True, help="directory for the layout and check results")
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT"),
                        help="directory that contains sky130A/ (default: $PDK_ROOT)")
    parser.add_argument("--placer", choices=["row", "random", "annealing"], default="row",
                        help="placement method (default: one row in netlist order)")
    parser.add_argument("--seed", type=int, default=1, help="placement seed (default: 1)")
    parser.add_argument("--wire-weight", type=float, default=1.0,
                        help="weight on estimated wire length in the placement score (default: 1.0)")
    parser.add_argument("--folds", type=int, nargs="+", default=[2],
                        help="allowed resistor fold counts, 2 to 5 (default: 2)")
    parser.add_argument("--guard", choices=["device", "shared"], default="device",
                        help="one ring per device (default) or one shared ring (requires --folds 2)")
    args = parser.parse_args()
    if not args.pdk_root:
        parser.error("set PDK_ROOT or pass --pdk-root")
    stats, _ = run_device_flow(args.bench, args.out, Pdk(args.pdk_root),
                               placer=args.placer, seed=args.seed,
                               wire_weight=args.wire_weight,
                               resistor_folds=args.folds, guard=args.guard)
    stats["placement"].pop("cost_history", None)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

"""Standard-cell placement, routing and layout checks on SKY130."""

import argparse
import json
import os

from minipnr.flow import run_flow
from minipnr.tools import Pdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench", help="circuit in ISCAS .bench format")
    parser.add_argument("--out", required=True, help="directory for the layout and check results")
    parser.add_argument("--seed", type=int, default=1, help="placement seed (default: 1)")
    parser.add_argument("--utilization", type=float, default=0.6,
                        help="target fraction of placement space used by gates (default: 0.6)")
    parser.add_argument("--no-anneal", action="store_true", help="keep the random placement")
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT"),
                        help="directory that contains sky130A/ (default: $PDK_ROOT)")
    args = parser.parse_args()
    if not args.pdk_root:
        parser.error("set PDK_ROOT or pass --pdk-root")

    stats = run_flow(args.bench, args.out, Pdk(args.pdk_root), seed=args.seed,
                     utilization=args.utilization, use_annealing=not args.no_anneal)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

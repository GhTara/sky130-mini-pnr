# sky130-mini-pnr

The study compares five placement methods on eight small circuits using a
layout algorithm adapted to SkyWater’s open 130 nm process (SKY130).

## Placement results

Annealing with restarts had the lowest mean score on six circuits. It tied
random search on the inverter and NAND2.

The score adds the area of the smallest rectangle around the devices (µm²)
to their estimated connection length (µm) before routing. Lower is better.
Each entry shows the mean ± sample standard deviation over ten seeds at
the listed CPU budget per run.

| Circuit | CPU budget (s) | Annealing with restarts | ES + REINFORCE | ES | REINFORCE | Random search |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Inverter | 10 | 11.64 ± 0.00 | 12.75 ± 0.52 | 12.75 ± 0.52 | 13.97 ± 0.00 | 11.64 ± 0.00 |
| NAND2 | 10 | 15.48 ± 0.00 | 16.36 ± 0.99 | 16.41 ± 1.64 | 21.90 ± 0.00 | 15.48 ± 0.00 |
| AND2 | 30 | 36.90 ± 0.00 | 38.71 ± 0.53 | 39.15 ± 1.02 | 40.26 ± 0.00 | 39.04 ± 0.83 |
| Two-NAND chain | 30 | 38.08 ± 0.00 | 43.09 ± 3.88 | 44.32 ± 2.52 | 51.37 ± 0.00 | 45.39 ± 2.27 |
| Three-NAND chain | 60 | 67.68 ± 0.05 | 74.17 ± 4.42 | 75.11 ± 4.25 | 72.32 ± 1.02 | 91.55 ± 1.47 |
| XOR2 | 60 | 100.27 ± 2.54 | 107.25 ± 5.56 | 109.24 ± 5.59 | 111.35 ± 2.03 | 148.88 ± 3.50 |
| c17 | 120 | 148.87 ± 3.47 | 161.49 ± 4.67 | 166.48 ± 7.78 | 180.94 ± 3.13 | 251.15 ± 8.64 |
| Full adder | 180 | 250.40 ± 5.26 | 274.74 ± 6.89 | 275.79 ± 15.46 | 308.86 ± 9.57 | 461.51 ± 7.75 |

The three columns using ES (evolution strategies) or REINFORCE report
results only.

## Run c17

c17 has six NAND gates. Set `PDK_ROOT` to the directory containing
`sky130A/`. [PDK setup](https://github.com/fossi-foundation/ciel#downloading-and-enabling-pdks)

```bash
micromamba env create -f environment.yml
micromamba activate sky130-eda
export PDK_ROOT=/path/to/pdks

# Place whole gates
python run_flow.py benchmarks/c17.bench --seed 1 --out work/c17

# Place individual transistors and resistors
python run_device_flow.py benchmarks/c17.bench --seed 1 --placer annealing --guard shared --out work/c17_device
```

The layouts are written to `work/c17/c17.gds` and
`work/c17_device/c17_dev.gds`. Each directory also contains the extracted
SPICE circuit and the check results in `summary.json`.
Run `python -m pytest tests` for the test suite.

The recorded runs used Magic 8.3.465, Netgen 1.5.272, ngspice 42 and
open_pdks revision `8afc8346a57fe1ab7934ba5a6056ea8b43078e71`.

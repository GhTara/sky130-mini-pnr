"""CP-SAT reference for the bounded, fixed-shape device placement model.
In 5 nm units, 40000 × cost = area_units + 100 × wire_weight × hpwl2.
Doubling HPWL keeps half-unit pin centres exact.
"""

import time

from ortools.sat.python import cp_model

from . import units as u
from .device_placer import SUBSTRATE_TERMINAL, required_gap


def solve_exact(devices, layouts, offsets, device_gap, wire_weight=1.0, hint=None,
                time_limit_s=600, workers=8, seed=1):
    """Return the best placement found, its cost and the solver's lower bound."""
    model = cp_model.CpModel()
    names = [d.name for d in devices]
    kind = {d.name: d.kind for d in devices}
    weight = round(100 * wire_weight)
    if abs(weight - 100 * wire_weight) > 1e-9:
        raise ValueError("wire_weight must be a multiple of 0.01")

    # Bound each axis by the sum of the longest sides and a gap per device.
    largest_gap = max(max(required_gap(device_gap, kind[a], kind[b]))
                      for a in names for b in names)
    span = 0
    for name in names:
        x1, y1, x2, y2 = layouts[kind[name]].bbox
        span += max(x2 - x1, y2 - y1) + largest_gap

    x, y, left, right, bottom, top = {}, {}, {}, {}, {}, {}
    lattice = {}                                   # name -> (kx, ky) variables
    for name in names:
        b = layouts[kind[name]].bbox
        ox, oy = offsets[kind[name]]
        kx = model.NewIntVar(-span // u.TRACK_X_PITCH - 2, span // u.TRACK_X_PITCH + 2, f"kx_{name}")
        ky = model.NewIntVar(-span // u.TRACK_Y_PITCH - 2, span // u.TRACK_Y_PITCH + 2, f"ky_{name}")
        lattice[name] = (kx, ky)
        x[name] = ox + u.TRACK_X_PITCH * kx
        y[name] = oy + u.TRACK_Y_PITCH * ky
        for store, offset, var_name in ((left, b[0], "l"), (right, b[2], "r")):
            store[name] = model.NewIntVar(-2 * span, 2 * span, f"{var_name}_{name}")
            model.Add(store[name] == x[name] + offset)
        for store, offset, var_name in ((bottom, b[1], "b"), (top, b[3], "t")):
            store[name] = model.NewIntVar(-2 * span, 2 * span, f"{var_name}_{name}")
            model.Add(store[name] == y[name] + offset)

    # Moving all devices by a whole grid step leaves the score unchanged.
    # Keep one representative by fixing the corner within the first grid step.
    min_left = model.NewIntVar(0, u.TRACK_X_PITCH - 1, "min_left")
    min_bottom = model.NewIntVar(0, u.TRACK_Y_PITCH - 1, "min_bottom")
    max_right = model.NewIntVar(0, 2 * span, "max_right")
    max_top = model.NewIntVar(0, 2 * span, "max_top")
    model.AddMinEquality(min_left, [left[n] for n in names])
    model.AddMinEquality(min_bottom, [bottom[n] for n in names])
    model.AddMaxEquality(max_right, [right[n] for n in names])
    model.AddMaxEquality(max_top, [top[n] for n in names])
    width = model.NewIntVar(0, 2 * span, "width")
    height = model.NewIntVar(0, 2 * span, "height")
    model.Add(width == max_right - min_left)
    model.Add(height == max_top - min_bottom)
    area = model.NewIntVar(0, 4 * span * span, "area")
    model.AddMultiplicationEquality(area, [width, height])

    # pairwise spacing: apart in x OR in y
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            gap_x, gap_y = required_gap(device_gap, kind[a], kind[b])
            sides = [model.NewBoolVar(f"{a}_{side}_{b}") for side in ("l", "r", "d", "u")]
            model.Add(right[a] + gap_x <= left[b]).OnlyEnforceIf(sides[0])
            model.Add(right[b] + gap_x <= left[a]).OnlyEnforceIf(sides[1])
            model.Add(top[a] + gap_y <= bottom[b]).OnlyEnforceIf(sides[2])
            model.Add(top[b] + gap_y <= bottom[a]).OnlyEnforceIf(sides[3])
            model.AddBoolOr(sides)

    # HPWL (doubled, so terminal centres on half units stay integer)
    nets = {}
    for device in devices:
        for terminal, net in device.nets.items():
            if terminal != SUBSTRATE_TERMINAL:
                nets.setdefault(net, []).append((device.name, terminal))
    spans = []
    for net, pins in nets.items():
        if len(pins) < 2:
            continue
        for axis in ("x", "y"):
            points = []
            for name, terminal in pins:
                rects = layouts[kind[name]].terminals[terminal].rects
                if axis == "x":
                    centre2 = min(r[0] for r in rects) + max(r[2] for r in rects)
                    points.append(2 * x[name] + centre2)
                else:
                    centre2 = min(r[1] for r in rects) + max(r[3] for r in rects)
                    points.append(2 * y[name] + centre2)
            high = model.NewIntVar(-8 * span, 8 * span, f"hi_{axis}_{net}")
            low = model.NewIntVar(-8 * span, 8 * span, f"lo_{axis}_{net}")
            model.AddMaxEquality(high, points)
            model.AddMinEquality(low, points)
            spans.append(high - low)
    hpwl2 = sum(spans)
    model.Minimize(area + weight * hpwl2)

    if hint is not None:
        for name, (kx, ky) in lattice_hint(hint, kind, offsets, layouts).items():
            model.AddHint(lattice[name][0], kx)
            model.AddHint(lattice[name][1], ky)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    started = time.perf_counter()
    status = solver.Solve(model)
    seconds = time.perf_counter() - started
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT found no placement ({solver.StatusName(status)})")

    placement = {n: (solver.Value(x[n]), solver.Value(y[n])) for n in names}
    scale = 40000.0
    info = {"status": solver.StatusName(status),
            "objective": round(solver.ObjectiveValue() / scale, 3),
            "lower_bound": round(solver.BestObjectiveBound() / scale, 3),
            "area_um2": round(solver.Value(area) / scale, 3),
            "hpwl_um": round(solver.Value(hpwl2) / 400.0, 3),
            "seconds": round(seconds, 1), "time_limit_s": time_limit_s,
            "workers": workers}
    return placement, info


def lattice_hint(hint, kind, offsets, layouts):
    """Shift the supplied placement by whole grid steps into the solver’s reference window."""
    min_left = min(hint[n][0] + layouts[kind[n]].bbox[0] for n in hint)
    min_bottom = min(hint[n][1] + layouts[kind[n]].bbox[1] for n in hint)
    shift_x = (min_left // u.TRACK_X_PITCH) * u.TRACK_X_PITCH
    shift_y = (min_bottom // u.TRACK_Y_PITCH) * u.TRACK_Y_PITCH
    indices = {}
    for name, (px, py) in hint.items():
        ox, oy = offsets[kind[name]]
        indices[name] = ((px - shift_x - ox) // u.TRACK_X_PITCH,
                         (py - shift_y - oy) // u.TRACK_Y_PITCH)
    return indices

"""Place the devices of each NAND2 gate as one group using the device placement score."""

import math

from . import units as u
from .device_placer import DevicePlacer, far_enough

WINDOW_X = 10            # search window for cell layouts, in lattice steps
WINDOW_Y = 20         # enough for both transistors stacked on the resistor


def gate_of(device_name):
    return device_name.rsplit("_", 1)[0]


def role_of(device_name):
    return device_name.rsplit("_", 1)[1]


class GatePlacer(DevicePlacer):
    """cell: {role: (x, y)} positions of the devices of one gate (same cell
    for every gate)."""

    def __init__(self, devices, layouts, offsets, device_gap, cell, wire_weight=1.0):
        super().__init__(devices, layouts, offsets, device_gap, wire_weight)
        self.cell = cell
        self.kind = {d.name: d.kind for d in devices}
        self.gates = {}
        for device in devices:
            self.gates.setdefault(gate_of(device.name), []).append(device.name)
        for gate, names in self.gates.items():
            if sorted(role_of(n) for n in names) != sorted(cell):
                raise ValueError(f"gate {gate} does not fit the cell")

    def gate_is_legal(self, placement, gate):
        """Spacing of the gate's devices to the devices of all other gates."""
        others = {n: s for n, s in placement.items() if gate_of(n) != gate}
        return all(self.is_legal(others, n, placement[n]) for n in self.gates[gate])

    def moved(self, placement, gate, dx, dy):
        trial = dict(placement)
        for name in self.gates[gate]:
            x, y, kind = placement[name]
            trial[name] = (x + dx, y + dy, kind)
        return trial

    def random_placement(self, rng, fill=0.5, max_tries=2000):
        need = 0
        for name in self.names:
            b = self.layouts[self.kind[name]].bbox
            largest = max(self.min_gap(self.kind[name], self.kind[name]))
            need += (b[2] - b[0] + largest) * (b[3] - b[1] + largest)
        side = int(math.sqrt(need / fill))
        while True:
            placement = {}
            for gate in rng.sample(sorted(self.gates), len(self.gates)):
                for _ in range(max_tries):
                    dx = rng.randrange(0, side) // u.TRACK_X_PITCH * u.TRACK_X_PITCH
                    dy = rng.randrange(0, side) // u.TRACK_Y_PITCH * u.TRACK_Y_PITCH
                    trial = dict(placement)
                    for name in self.gates[gate]:
                        x, y = self.cell[role_of(name)]
                        trial[name] = (x + dx, y + dy, self.kind[name])
                    if self.gate_is_legal(trial, gate):
                        placement = trial
                        break
                else:
                    break
            if len(placement) == len(self.names):
                return placement
            side = int(side * 1.2)

    def propose_move(self, placement, rng, max_step):
        names = sorted(self.gates)
        gate = rng.choice(names)
        if rng.random() < 0.3 and len(names) > 1:
            other = rng.choice(names)
            if other == gate:
                return None
            ax, ay, _ = placement[self.gates[gate][0]]
            bx, by, _ = placement[self.gates[other][0]]
            trial = self.moved(placement, gate, bx - ax, by - ay)
            trial = self.moved(trial, other, ax - bx, ay - by)
            legal = self.gate_is_legal(trial, gate) and self.gate_is_legal(trial, other)
            return trial if legal else None

        dx = rng.randint(-max_step, max_step) * u.TRACK_X_PITCH
        dy = rng.randint(-max_step, max_step) * u.TRACK_Y_PITCH
        if dx == 0 and dy == 0:
            return None
        trial = self.moved(placement, gate, dx, dy)
        return trial if self.gate_is_legal(trial, gate) else None


def relations(engine, states):
    """Which device is left of / below which: tells cell layouts apart."""
    result = []
    names = sorted(states)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            gap_x, gap_y = engine.min_gap(states[a][2], states[b][2])
            box_a, box_b = engine.box(states[a]), engine.box(states[b])
            result.append((box_a[2] + gap_x <= box_b[0], box_b[2] + gap_x <= box_a[0],
                           box_a[3] + gap_y <= box_b[1], box_b[3] + gap_y <= box_a[1]))
    return tuple(result)


def nand2_cells(devices, layouts, offsets, device_gap, count):
    """Return up to count distinct NAND2 arrangements, ranked by their best cost in the search window."""
    engine = DevicePlacer(devices, layouts, offsets, device_gap)
    by_role = {role_of(d.name): d for d in devices}
    resistor = by_role["r"]
    fixed = (*offsets[resistor.kind], resistor.kind)
    ox, oy = offsets["nfet"]
    spots = [(ox + u.TRACK_X_PITCH * i, oy + u.TRACK_Y_PITCH * j, "nfet")
             for i in range(-WINDOW_X, WINDOW_X + 1) for j in range(-WINDOW_Y, WINDOW_Y + 1)]
    gap = engine.min_gap("nfet", resistor.kind)
    spots = [s for s in spots if far_enough(engine.box(s), engine.box(fixed), *gap)]
    gap = engine.min_gap("nfet", "nfet")
    best = {}
    for first in spots:
        for second in spots:
            if not far_enough(engine.box(first), engine.box(second), *gap):
                continue
            states = {resistor.name: fixed, by_role["m1"].name: first,
                      by_role["m2"].name: second}
            cost = engine.cost(states)
            key = relations(engine, states)
            if key not in best or cost < best[key][0]:
                best[key] = (cost, states)
    ranked = sorted(best.values(), key=lambda item: item[0])[:count]
    return [{"cost": cost, "cell": {role_of(n): s[:2] for n, s in states.items()}}
            for cost, states in ranked]

"""Random and annealed placement of devices on the routing grid.
The score adds device bounding-box area (µm²) to weighted HPWL (µm).
HPWL uses device pin centres and excludes substrate and external pins.
"""

import math
import random

from . import units as u
from .devices import shift

SUBSTRATE_TERMINAL = "B"


def required_gap(device_gap, kind_a, kind_b):
    """(gap x, gap y) between two device kinds, in Magic units."""
    if isinstance(device_gap, int):
        return device_gap, device_gap
    if (kind_a, kind_b) in device_gap:
        return device_gap[(kind_a, kind_b)]
    return device_gap[(kind_b, kind_a)]


def far_enough(box_a, box_b, gap_x, gap_y):
    """True if the outlines are at least gap_x apart in x or gap_y apart in y."""
    dx = max(box_a[0] - box_b[2], box_b[0] - box_a[2])
    dy = max(box_a[1] - box_b[3], box_b[1] - box_a[3])
    return dx >= gap_x or dy >= gap_y


def snap_down(value, offset, pitch):
    """Largest offset + pitch * k that is <= value."""
    return offset + pitch * ((value - offset) // pitch)


class DevicePlacer:
    def __init__(self, devices, layouts, offsets, device_gap, wire_weight=1.0, variants=None):
        """variants: optional {device name: [allowed kinds]}; default is the
        device's own kind only."""
        self.names = [d.name for d in devices]
        self.layouts = layouts
        self.offsets = offsets
        self.device_gap = device_gap      # int, or {(kind a, kind b): (gap x, gap y)}
        self.wire_weight = wire_weight
        self.variants = {d.name: [d.kind] for d in devices}
        self.variants.update(variants or {})
        self.foldable = [name for name in self.names if len(self.variants[name]) > 1]

        # centre of each terminal relative to the device origin
        self.terminal_centre = {}
        for kind, layout in layouts.items():
            for name, terminal in layout.terminals.items():
                xs = [r[0] for r in terminal.rects] + [r[2] for r in terminal.rects]
                ys = [r[1] for r in terminal.rects] + [r[3] for r in terminal.rects]
                self.terminal_centre[(kind, name)] = ((min(xs) + max(xs)) / 2,
                                                      (min(ys) + max(ys)) / 2)
        self.nets = {}
        for device in devices:
            for terminal, net in device.nets.items():
                if terminal != SUBSTRATE_TERMINAL:
                    self.nets.setdefault(net, []).append((device.name, terminal))
        self.nets = {net: pins for net, pins in self.nets.items() if len(pins) > 1}

    # ----- geometry -------------------------------------------------------

    def box(self, state):
        x, y, kind = state
        return shift(self.layouts[kind].bbox, x, y)

    def min_gap(self, kind_a, kind_b):
        return required_gap(self.device_gap, kind_a, kind_b)

    def is_legal(self, placement, name, state):
        box = self.box(state)
        for other, other_state in placement.items():
            if other == name:
                continue
            gap_x, gap_y = self.min_gap(state[2], other_state[2])
            if not far_enough(box, self.box(other_state), gap_x, gap_y):
                return False
        return True

    def at_corner(self, kind, left, bottom):
        """State of a `kind` device whose outline starts near (left, bottom)."""
        ox, oy = self.offsets[kind]
        bbox = self.layouts[kind].bbox
        return (snap_down(left - bbox[0], ox, u.TRACK_X_PITCH),
                snap_down(bottom - bbox[1], oy, u.TRACK_Y_PITCH), kind)

    # ----- cost -----------------------------------------------------------

    def area_um2(self, placement):
        boxes = [self.box(state) for state in placement.values()]
        width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        height = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        return u.units_to_um(width) * u.units_to_um(height)

    def hpwl_um(self, placement):
        total = 0.0
        for pins in self.nets.values():
            xs, ys = [], []
            for name, terminal in pins:
                x, y, kind = placement[name]
                dx, dy = self.terminal_centre[(kind, terminal)]
                xs.append(x + dx)
                ys.append(y + dy)
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return u.units_to_um(total)

    def cost(self, placement):
        return self.area_um2(placement) + self.wire_weight * self.hpwl_um(placement)

    # ----- start placement ------------------------------------------------

    def random_placement(self, rng, fill=0.5, max_tries=2000):
        """Return a legal random placement, expanding the starting region as needed."""
        # Draw only when there is a choice. Fixed shapes use no random numbers.
        kinds = {}
        for name in self.names:
            variants = self.variants[name]
            if name in self.foldable:
                kinds[name] = rng.choice(variants)
            else:
                kinds[name] = variants[0]
        need = 0
        for name in self.names:
            b = self.layouts[kinds[name]].bbox
            largest = max(self.min_gap(kinds[name], kinds[name]))
            need += (b[2] - b[0] + largest) * (b[3] - b[1] + largest)
        side = int(math.sqrt(need / fill))
        while True:
            placement = {}
            for name in rng.sample(self.names, len(self.names)):
                b = self.layouts[kinds[name]].bbox
                for _ in range(max_tries):
                    left = rng.randrange(0, max(1, side - (b[2] - b[0])))
                    bottom = rng.randrange(0, max(1, side - (b[3] - b[1])))
                    state = self.at_corner(kinds[name], left, bottom)
                    if self.is_legal(placement, name, state):
                        placement[name] = state
                        break
                else:
                    break
            if len(placement) == len(self.names):
                return placement
            side = int(side * 1.2)

    # ----- simulated annealing --------------------------------------------

    def propose_move(self, placement, rng, max_step):
        """Return a proposed legal placement, or None."""
        trial = dict(placement)
        if self.foldable and rng.random() < 0.2:
            name = rng.choice(self.foldable)
            x, y, kind = placement[name]
            new_kind = rng.choice([k for k in self.variants[name] if k != kind])
            box = self.box(placement[name])
            trial[name] = self.at_corner(new_kind, box[0], box[1])
            return trial if self.is_legal(trial, name, trial[name]) else None

        name = rng.choice(self.names)
        if rng.random() < 0.3 and len(self.names) > 1:
            other = rng.choice(self.names)
            if other == name:
                return None
            box_a, box_b = self.box(placement[name]), self.box(placement[other])
            trial[name] = self.at_corner(placement[name][2], box_b[0], box_b[1])
            trial[other] = self.at_corner(placement[other][2], box_a[0], box_a[1])
            if not (self.is_legal(trial, name, trial[name])
                    and self.is_legal(trial, other, trial[other])):
                return None
            return trial

        x, y, kind = placement[name]
        dx = rng.randint(-max_step, max_step) * u.TRACK_X_PITCH
        dy = rng.randint(-max_step, max_step) * u.TRACK_Y_PITCH
        if dx == 0 and dy == 0:
            return None
        state = (x + dx, y + dy, kind)
        if not self.is_legal(placement, name, state):
            return None
        trial[name] = state
        return trial

    def anneal(self, placement, rng, moves_per_device=50, cooling=0.95,
               final_temperature_ratio=1e-3, start_scale=1.0):
        """Return the lowest-cost placement and the current cost at each temperature."""
        cost = self.cost(placement)
        best, best_cost = placement, cost
        boxes = [self.box(state) for state in placement.values()]
        span = max(max(b[2] for b in boxes) - min(b[0] for b in boxes),
                   max(b[3] for b in boxes) - min(b[1] for b in boxes))
        start_step = max(1, span // u.TRACK_X_PITCH // 2)

        increases = []
        for _ in range(200):
            trial = self.propose_move(placement, rng, start_step)
            if trial is not None:
                delta = self.cost(trial) - cost
                if delta > 0:
                    increases.append(delta)
        temperature = sum(increases) / len(increases) if increases else 1.0
        start_temperature = temperature
        final_temperature = temperature * final_temperature_ratio
        temperature *= start_scale
        moves = moves_per_device * len(self.names)

        history = [cost]
        while temperature > final_temperature:
            max_step = max(1, round(start_step * temperature / start_temperature))
            for _ in range(moves):
                trial = self.propose_move(placement, rng, max_step)
                if trial is None:
                    continue
                trial_cost = self.cost(trial)
                delta = trial_cost - cost
                if delta <= 0 or rng.random() < math.exp(-delta / temperature):
                    placement, cost = trial, trial_cost
                    if cost < best_cost:
                        best, best_cost = placement, cost
            history.append(cost)
            temperature *= cooling
        return best, history


def place_devices(devices, layouts, offsets, device_gap, placer, seed, wire_weight=1.0,
                  variants=None, moves_per_device=50):
    """placer: "random" (random legal start only) or "annealing" (random
    start, then annealing). Returns (placement {name: (x, y)},
    kinds {name: chosen kind}, info)."""
    rng = random.Random(seed)
    engine = DevicePlacer(devices, layouts, offsets, device_gap, wire_weight, variants)
    start = engine.random_placement(rng)
    info = {"start_area_um2": round(engine.area_um2(start), 3),
            "start_hpwl_um": round(engine.hpwl_um(start), 3)}
    placement, history = start, [engine.cost(start)]
    if placer == "annealing":
        placement, history = engine.anneal(start, rng, moves_per_device=moves_per_device)
    info.update({"placed_cost": round(engine.cost(placement), 3),
                 "placed_area_um2": round(engine.area_um2(placement), 3),
                 "placed_hpwl_um": round(engine.hpwl_um(placement), 3),
                 "cost_history": [round(c, 3) for c in history]})
    positions = {name: (x, y) for name, (x, y, _) in placement.items()}
    kinds = {name: kind for name, (_, _, kind) in placement.items()}
    return positions, kinds, info

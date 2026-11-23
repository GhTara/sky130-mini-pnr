"""Place standard cells in rows and score them by estimated wire length (HPWL)."""

import math
import random

from . import units as u
from .floorplan import pin_access_offsets, to_absolute, track_x, track_y


class Placer:
    def __init__(self, circuit, macros, plan):
        self.circuit = circuit
        self.plan = plan
        self.gates = {gate.name: gate for gate in circuit.gates}
        self.width_sites = {
            gate.name: macros[gate.cell].width // u.SITE_WIDTH for gate in circuit.gates
        }
        # Average pin access point, used as the pin location for HPWL.
        self.pin_offsets = {}
        for gate in circuit.gates:
            for pin in gate.pin_nets:
                points = pin_access_offsets(macros[gate.cell], pin)
                mean_dx = sum(p[0] for p in points) / len(points)
                mean_dy = sum(p[1] for p in points) / len(points)
                self.pin_offsets[(gate.name, pin)] = (mean_dx, mean_dy)
        self.nets = circuit.nets()

    # ----- cost -------------------------------------------------------

    def pin_location(self, placement, owner, pin):
        if owner == "PIN":
            ix, iy = self.plan.io_nodes[pin]
            return track_x(ix), track_y(iy)
        row, site = placement[owner]
        dx, dy = self.pin_offsets[(owner, pin)]
        return to_absolute(self.plan, row, site, dx, dy)

    def net_hpwl(self, placement, net):
        points = [self.pin_location(placement, owner, pin) for owner, pin in self.nets[net]]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    def total_hpwl(self, placement):
        return sum(self.net_hpwl(placement, net) for net in self.nets)

    # ----- legality ---------------------------------------------------

    def is_free(self, placement, name, row, site):
        """True if `name` fits at (row, site) without overlapping other cells."""
        width = self.width_sites[name]
        if site < self.plan.first_free_site or site + width > self.plan.end_free_site:
            return False
        for other, (other_row, other_site) in placement.items():
            if other == name or other_row != row:
                continue
            other_width = self.width_sites[other]
            if site < other_site + other_width and other_site < site + width:
                return False
        return True

    def random_location(self, rng):
        row = rng.randrange(self.plan.n_rows)
        site = rng.randrange(self.plan.first_free_site, self.plan.end_free_site)
        return row, site

    def random_placement(self, rng, max_tries=10000):
        placement = {}
        names = list(self.gates)
        rng.shuffle(names)
        for name in names:
            for _ in range(max_tries):
                row, site = self.random_location(rng)
                if self.is_free(placement, name, row, site):
                    placement[name] = (row, site)
                    break
            else:
                raise RuntimeError("could not find a legal random placement")
        return placement

    # ----- simulated annealing ----------------------------------------

    def propose_move(self, placement, rng):
        """Return a new placement after one random move, or None."""
        names = list(placement)
        name = rng.choice(names)
        new_placement = dict(placement)

        if rng.random() < 0.5 and len(names) > 1:
            other = rng.choice(names)
            same_width = self.width_sites[other] == self.width_sites[name]
            if other == name or not same_width:
                return None
            new_placement[name], new_placement[other] = placement[other], placement[name]
            return new_placement

        row, site = self.random_location(rng)
        if not self.is_free(placement, name, row, site):
            return None
        new_placement[name] = (row, site)
        return new_placement

    def anneal(self, placement, rng, moves_per_temperature=200, cooling=0.95,
               final_temperature_ratio=1e-3):
        """Cool from the mean sampled cost increase and keep the best placement.

        The history records the current cost after each set of trial moves.
        """
        cost = self.total_hpwl(placement)
        best, best_cost = placement, cost

        increases = []
        for _ in range(100):
            trial = self.propose_move(placement, rng)
            if trial is not None:
                delta = self.total_hpwl(trial) - cost
                if delta > 0:
                    increases.append(delta)
        temperature = (sum(increases) / len(increases)) if increases else 1.0
        final_temperature = temperature * final_temperature_ratio

        history = [cost]
        while temperature > final_temperature:
            for _ in range(moves_per_temperature):
                trial = self.propose_move(placement, rng)
                if trial is None:
                    continue
                trial_cost = self.total_hpwl(trial)
                delta = trial_cost - cost
                if delta <= 0 or rng.random() < math.exp(-delta / temperature):
                    placement, cost = trial, trial_cost
                    if cost < best_cost:
                        best, best_cost = placement, cost
            history.append(cost)
            temperature *= cooling
        return best, history


def place(circuit, macros, plan, seed, use_annealing=True):
    """Return (placement, info). info has the HPWL before and after annealing."""
    rng = random.Random(seed)
    placer = Placer(circuit, macros, plan)
    start = placer.random_placement(rng)
    info = {"hpwl_random_um": u.units_to_um(placer.total_hpwl(start))}
    if not use_annealing:
        info["hpwl_final_um"] = info["hpwl_random_um"]
        return start, info
    best, history = placer.anneal(start, rng)
    info["hpwl_final_um"] = u.units_to_um(placer.total_hpwl(best))
    info["anneal_steps"] = len(history)
    return best, info

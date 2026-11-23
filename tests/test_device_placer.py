"""Device placer checks with synthetic device outlines (no PDK needed)."""

import random
import unittest

from minipnr.bench import parse_bench
from minipnr.device_netlist import expand_circuit
from minipnr.device_placer import DevicePlacer, place_devices
from minipnr.devices import DeviceLayout, DeviceTerminal, gap

GAP = 120


def fake_layouts():
    nfet = DeviceLayout("nfet", (-211, -279, 211, 279), terminals={
        "D": DeviceTerminal("D", "met1", [(-67, -131, -21, 69)]),
        "S": DeviceTerminal("S", "met1", [(21, -131, 67, 69)]),
        "G": DeviceTerminal("G", "met1", [(-29, 101, 29, 147)]),
        "B": DeviceTerminal("B", "li1", [(-211, -279, 211, -245)])})
    rload = DeviceLayout("rload", (-230, -600, 230, 600), terminals={
        "R1": DeviceTerminal("R1", "met1", [(-150, 400, -100, 580)]),
        "R2": DeviceTerminal("R2", "met1", [(100, 400, 150, 580)]),
        "B": DeviceTerminal("B", "li1", [(-230, -600, 230, -566)])})
    return {"nfet": nfet, "rload": rload}


def c17_devices():
    with open("benchmarks/c17.bench") as handle:
        return expand_circuit(parse_bench(handle.read(), "c17"))


class DevicePlacerTest(unittest.TestCase):
    def setUp(self):
        self.devices = c17_devices()
        self.layouts = fake_layouts()
        self.offsets = {"nfet": (0, 37), "rload": (26, 52)}

    def assert_legal(self, engine, placement):
        names = list(placement)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                box_a = engine.box(placement[a])
                box_b = engine.box(placement[b])
                self.assertGreaterEqual(gap(box_a, box_b), GAP, (a, b))

    def assert_on_lattice(self, placement):
        for name, (x, y, kind) in placement.items():
            ox, oy = self.offsets[kind]
            self.assertEqual((x - ox) % 92, 0)
            self.assertEqual((y - oy) % 68, 0)

    def test_random_and_annealed_placements_are_legal_and_on_lattice(self):
        engine = DevicePlacer(self.devices, self.layouts, self.offsets, GAP)
        for placer in ("random", "annealing"):
            positions, kinds, _ = place_devices(self.devices, self.layouts, self.offsets,
                                                GAP, placer, seed=3)
            self.assertEqual(set(positions), {d.name for d in self.devices})
            states = {n: positions[n] + (kinds[n],) for n in positions}
            self.assert_legal(engine, states)
            self.assert_on_lattice(states)

    def test_annealing_never_returns_worse_than_its_start(self):
        engine = DevicePlacer(self.devices, self.layouts, self.offsets, GAP)
        rng = random.Random(5)
        start = engine.random_placement(rng)
        best, history = engine.anneal(start, rng)
        self.assertGreater(len(history), 10)
        self.assertLessEqual(engine.cost(best), engine.cost(start))
        self.assertLess(engine.cost(best), 0.9 * engine.cost(start))

    def test_hpwl_ignores_substrate_and_single_pin_nets(self):
        engine = DevicePlacer(self.devices, self.layouts, self.offsets, GAP)
        for pins in engine.nets.values():
            self.assertGreater(len(pins), 1)
            self.assertTrue(all(terminal != "B" for _, terminal in pins))

    def test_fold_variants_are_chosen_and_stay_legal(self):
        layouts = dict(self.layouts)
        # a "folded" resistor: wider and shorter, same terminals
        short = DeviceLayout("rload_nx4", (-460, -300, 460, 300), terminals={
            "R1": DeviceTerminal("R1", "met1", [(-400, 200, -350, 280)]),
            "R2": DeviceTerminal("R2", "met1", [(350, 200, 400, 280)]),
            "B": DeviceTerminal("B", "li1", [(-460, -300, 460, -266)])})
        layouts["rload_nx4"] = short
        offsets = dict(self.offsets, rload_nx4=(10, 20))
        variants = {d.name: ["rload", "rload_nx4"] for d in self.devices if d.kind == "rload"}
        self.offsets = offsets
        engine = DevicePlacer(self.devices, layouts, offsets, GAP, variants=variants)
        positions, kinds, _ = place_devices(self.devices, layouts, offsets, GAP, "annealing",
                                            seed=2, variants=variants)
        states = {n: positions[n] + (kinds[n],) for n in positions}
        self.assert_legal(engine, states)
        self.assert_on_lattice(states)
        for device in self.devices:
            allowed = variants.get(device.name, [device.kind])
            self.assertIn(kinds[device.name], allowed)


if __name__ == "__main__":
    unittest.main()

"""Check device spacing and relative positions when an entire NAND gate moves."""

import random
import unittest

from minipnr.device_netlist import Device
from minipnr.device_placer import DevicePlacer
from minipnr.exact_placer import solve_exact
from minipnr.gate_placer import GatePlacer, gate_of, nand2_cells, role_of
from tests.test_exact_placer import SPACING, small_case


def nand2(gate, a, b, out):
    return [Device(f"{gate}_r", "rload", {"R1": "VPWR", "R2": out, "B": "VGND"}),
            Device(f"{gate}_m1", "nfet", {"D": out, "G": a, "S": f"{gate}x", "B": "VGND"}),
            Device(f"{gate}_m2", "nfet", {"D": f"{gate}x", "G": b, "S": "VGND", "B": "VGND"})]


class GatePlacerTest(unittest.TestCase):
    def setUp(self):
        _, self.layouts, self.offsets = small_case()
        self.one = nand2("g1", "a", "b", "y")
        self.cells = nand2_cells(self.one, self.layouts, self.offsets, SPACING, 3)

    def test_best_cell_is_the_proven_optimum_of_one_gate(self):
        _, info = solve_exact(self.one, self.layouts, self.offsets, SPACING, time_limit_s=30,
                              workers=2)
        self.assertEqual(info["status"], "OPTIMAL")
        self.assertAlmostEqual(self.cells[0]["cost"], info["objective"], places=2)

    def test_annealed_gates_stay_rigid_and_legal(self):
        devices = self.one + nand2("g2", "y", "c", "z") + nand2("g3", "y", "z", "q")
        cell = self.cells[0]["cell"]
        engine = GatePlacer(devices, self.layouts, self.offsets, SPACING, cell)
        rng = random.Random(1)
        placement, _ = engine.anneal(engine.random_placement(rng), rng, moves_per_device=5)
        free = DevicePlacer(devices, self.layouts, self.offsets, SPACING)
        for name, state in placement.items():
            self.assertTrue(free.is_legal(placement, name, state))
            resistor_x, resistor_y, _ = placement[gate_of(name) + "_r"]
            role = role_of(name)
            placed_offset = (state[0] - resistor_x, state[1] - resistor_y)
            cell_offset = (cell[role][0] - cell["r"][0], cell[role][1] - cell["r"][1])
            self.assertEqual(placed_offset, cell_offset)

    def test_refinement_is_never_worse_than_its_start(self):
        devices = self.one + nand2("g2", "y", "c", "z")
        rigid = GatePlacer(devices, self.layouts, self.offsets, SPACING, self.cells[0]["cell"])
        rng = random.Random(2)
        start, _ = rigid.anneal(rigid.random_placement(rng), rng, moves_per_device=5)
        free = DevicePlacer(devices, self.layouts, self.offsets, SPACING)
        refined, _ = free.anneal(start, rng, moves_per_device=5, start_scale=0.05)
        self.assertLessEqual(free.cost(refined), free.cost(start) + 1e-9)


if __name__ == "__main__":
    unittest.main()

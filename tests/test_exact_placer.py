"""Check that CP-SAT and annealing use the same placement rules and score."""

import unittest

from minipnr.device_netlist import Device
from minipnr.device_placer import DevicePlacer, place_devices
from minipnr.devices import DeviceLayout, DeviceTerminal
from minipnr.exact_placer import solve_exact

SPACING = {("nfet", "nfet"): (56, 44), ("nfet", "rload"): (96, 96),
           ("rload", "rload"): (44, 44)}


def small_case():
    # SKY130 device outlines with simplified resistor pin rectangles for this test.
    nfet = DeviceLayout("n", (-73, -157, 73, 157), terminals={
        "D": DeviceTerminal("D", "met1", [(-67, -131, -21, 69)]),
        "S": DeviceTerminal("S", "met1", [(21, -131, 67, 69)]),
        "G": DeviceTerminal("G", "met1", [(-29, 101, 29, 147)])})
    rload = DeviceLayout("r", (-118, -443, 118, 443), terminals={
        "R1": DeviceTerminal("R1", "met1", [(-110, -440, -56, -380)]),
        "R2": DeviceTerminal("R2", "met1", [(56, -440, 110, -380)])})
    devices = [Device("r", "rload", {"R1": "VPWR", "R2": "y", "B": "VGND"}),
               Device("m1", "nfet", {"D": "y", "G": "a", "S": "x", "B": "VGND"}),
               Device("m2", "nfet", {"D": "x", "G": "b", "S": "VGND", "B": "VGND"})]
    return devices, {"nfet": nfet, "rload": rload}, {"nfet": (0, 37), "rload": (26, 52)}


class ExactPlacerTest(unittest.TestCase):
    def test_exact_is_legal_and_its_cost_matches_annealing_cost(self):
        devices, layouts, offsets = small_case()
        placement, info = solve_exact(devices, layouts, offsets, SPACING, time_limit_s=30,
                                      workers=2)
        self.assertEqual(info["status"], "OPTIMAL")
        engine = DevicePlacer(devices, layouts, offsets, SPACING)
        states = {dev.name: (*placement[dev.name], dev.kind) for dev in devices}
        for name, state in states.items():
            self.assertTrue(engine.is_legal(states, name, state))
        self.assertAlmostEqual(engine.cost(states), info["objective"], places=2)

    def test_optimum_is_not_worse_than_annealing(self):
        devices, layouts, offsets = small_case()
        _, info = solve_exact(devices, layouts, offsets, SPACING, time_limit_s=30, workers=2)
        for seed in (1, 2, 3):
            _, _, sa = place_devices(devices, layouts, offsets, SPACING, "annealing", seed)
            self.assertGreaterEqual(sa["placed_cost"], info["objective"] - 1e-3)


if __name__ == "__main__":
    unittest.main()

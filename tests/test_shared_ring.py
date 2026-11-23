import unittest

from minipnr import units as u
from minipnr.device_placer import far_enough, required_gap
from minipnr.devices import DeviceLayout, DeviceTerminal
from minipnr.device_netlist import Device
from minipnr.device_flow import (SHARED_RING_SPACING, ring_box_units, ring_nodes,
                                 shared_ring_box)
from minipnr.substrate_ring import HALF, ring_rects


class SharedRingTest(unittest.TestCase):
    def test_ring_strips_are_0p17um_and_contacts_stay_off_corners(self):
        shapes = ring_rects((0, 0, 1000, 800))
        for rect in shapes["locali"]:
            self.assertEqual(min(rect[2] - rect[0], rect[3] - rect[1]), 34)
        for cont in shapes["psubdiffcont"]:
            inside = any(d[0] <= cont[0] and cont[2] <= d[2] and d[1] <= cont[1]
                         and cont[3] <= d[3] for d in shapes["psubdiff"])
            self.assertTrue(inside)
            outer = (-HALF, -HALF, 1000 + HALF, 800 + HALF)
            if cont[2] - cont[0] > cont[3] - cont[1]:      # on a horizontal side
                ends = (cont[0] - outer[0], outer[2] - cont[2])
            else:
                ends = (cont[1] - outer[1], outer[3] - cont[3])
            self.assertGreaterEqual(min(ends), 96)

    def test_ring_keeps_measured_gap_and_lies_on_tracks(self):
        nfet = DeviceLayout("n", (-73, -157, 73, 157),
                            terminals={"D": DeviceTerminal("D", "met1", [(-60, -100, -20, 60)])})
        rload = DeviceLayout("r", (-118, -443, 118, 443),
                             terminals={"R1": DeviceTerminal("R1", "met1", [(-90, -400, -60, -300)])})
        layouts = {"nfet": nfet, "rload": rload}
        devices = [Device("m1", "nfet", {}), Device("r1", "rload", {})]
        placement = {"m1": (1000, 500), "r1": (1600, 900)}
        ring = shared_ring_box(devices, layouts, placement, SHARED_RING_SPACING)
        x1, y1, x2, y2 = ring_box_units(ring)
        self.assertEqual((x1 - u.TRACK_X_OFFSET) % u.TRACK_X_PITCH, 0)
        self.assertEqual((y1 - u.TRACK_Y_OFFSET) % u.TRACK_Y_PITCH, 0)
        for device in devices:
            bbox = layouts[device.kind].bbox
            px, py = placement[device.name]
            box = (bbox[0] + px, bbox[1] + py, bbox[2] + px, bbox[3] + py)
            gap_x, gap_y = required_gap(SHARED_RING_SPACING, "tap", device.kind)
            self.assertGreaterEqual(box[0] - (x1 + HALF), gap_x)
            self.assertGreaterEqual((x2 - HALF) - box[2], gap_x)
            self.assertGreaterEqual(box[1] - (y1 + HALF), gap_y)
            self.assertGreaterEqual((y2 - HALF) - box[3], gap_y)
        self.assertGreater(len(ring_nodes(ring)), 10)

    def test_spacing_table_is_x_or_y(self):
        a = (0, 0, 100, 100)
        self.assertTrue(far_enough(a, (156, 0, 256, 100), 56, 44))     # side by side
        self.assertFalse(far_enough(a, (150, 0, 250, 100), 56, 44))
        self.assertTrue(far_enough(a, (0, 144, 100, 244), 56, 44))     # stacked
        self.assertFalse(far_enough(a, (120, 120, 200, 200), 56, 44))  # diagonal, both short


if __name__ == "__main__":
    unittest.main()

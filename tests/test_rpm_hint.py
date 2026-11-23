import os
import tempfile
import unittest

from minipnr.device_flow import add_rpm_mask_hint
from minipnr.device_netlist import DEVICE_TYPES, RESISTOR_FOLDS, is_resistor, resistor_kind

MAG = """magic
tech sky130A
magscale 1 2
<< xpolycontact >>
rect 131 222 201 654
rect -201 -654 -131 -222
<< xpolyres >>
rect -201 -222 -131 48
<< properties >>
string FIXED_BBOX -314 -767 314 767
<< end >>
"""


class RpmHintTest(unittest.TestCase):
    def test_hint_is_resistor_poly_box_plus_0p2um(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.mag")
            with open(path, "w") as handle:
                handle.write(MAG)
            add_rpm_mask_hint(path)
            with open(path) as handle:
                text = handle.read()
        self.assertIn("string MASKHINTS_RPM -241 -694 241 694\n", text)
        self.assertIn("string FIXED_BBOX -314 -767 314 767\n", text)

    def test_every_fold_variant_has_the_same_electrical_spec(self):
        kinds = [resistor_kind(nx) for nx in RESISTOR_FOLDS]
        self.assertEqual(kinds[0], "rload")
        for kind in kinds:
            self.assertTrue(is_resistor(kind))
            self.assertEqual(DEVICE_TYPES[kind]["spice"], {"l": 5.0})
            self.assertEqual(DEVICE_TYPES[kind]["spice_ports"], ["R1", "R2", "B"])


if __name__ == "__main__":
    unittest.main()

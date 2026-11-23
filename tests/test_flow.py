"""End-to-end test with the real PDK and tools. Skipped if they are missing."""

import os
import shutil
import tempfile
import unittest

from minipnr.device_flow import run_device_flow
from minipnr.flow import run_flow
from minipnr.tools import Pdk

HERE = os.path.dirname(__file__)
TOOLS = ("magic", "netgen", "ngspice")
HAVE_TOOLS = os.environ.get("PDK_ROOT") and all(shutil.which(t) for t in TOOLS)


@unittest.skipUnless(HAVE_TOOLS, "needs PDK_ROOT and magic/netgen/ngspice on PATH")
class FlowTest(unittest.TestCase):
    def test_full_adder_is_clean(self):
        bench = os.path.join(HERE, "..", "benchmarks", "full_adder.bench")
        with tempfile.TemporaryDirectory() as out_dir:
            stats = run_flow(bench, out_dir, Pdk(os.environ["PDK_ROOT"]), seed=3)
        self.assertEqual(stats["drc"]["added_by_pnr"], {})
        self.assertEqual(stats["drc"]["routed_total"], 0)
        self.assertTrue(stats["lvs"]["match"])
        self.assertTrue(stats["simulation"]["pass"])

    def test_device_level_nand2_is_clean(self):
        bench = os.path.join(HERE, "..", "benchmarks", "nand2.bench")
        with tempfile.TemporaryDirectory() as out_dir:
            stats, _ = run_device_flow(bench, out_dir, Pdk(os.environ["PDK_ROOT"]))
        self.assertEqual(stats["drc"]["routed_total"], 0)
        self.assertTrue(stats["lvs"]["match"])          # includes device sizes
        self.assertFalse(stats["lvs"]["property_errors"])
        self.assertTrue(stats["simulation"]["pass"])

    def test_device_level_nand2_with_shared_guard_ring_is_clean(self):
        bench = os.path.join(HERE, "..", "benchmarks", "nand2.bench")
        with tempfile.TemporaryDirectory() as out_dir:
            stats, _ = run_device_flow(bench, out_dir, Pdk(os.environ["PDK_ROOT"]),
                                       guard="shared")
        self.assertEqual(stats["drc"]["routed_total"], 0)   # includes LU.2 (tap distance)
        self.assertTrue(stats["lvs"]["match"])
        self.assertTrue(stats["simulation"]["pass"])


if __name__ == "__main__":
    unittest.main()

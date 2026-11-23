import unittest

from minipnr.bench import parse_bench
from minipnr.device_netlist import expand_circuit


class DeviceNetlistTest(unittest.TestCase):
    def test_nand2_is_series_stack_plus_resistor(self):
        circuit = parse_bench("INPUT(a)\nINPUT(b)\nOUTPUT(y)\ny = NAND(a, b)\n", "t")
        devices = {d.name: d for d in expand_circuit(circuit)}
        self.assertEqual(devices["gy_r"].nets, {"R1": "VPWR", "R2": "ny", "B": "VGND"})
        self.assertEqual(devices["gy_m1"].nets, {"D": "ny", "G": "na", "S": "gy_x1", "B": "VGND"})
        self.assertEqual(devices["gy_m2"].nets, {"D": "gy_x1", "G": "nb", "S": "VGND", "B": "VGND"})

    def test_nor2_is_parallel(self):
        circuit = parse_bench("INPUT(a)\nINPUT(b)\nOUTPUT(y)\ny = NOR(a, b)\n", "t")
        nfets = [d for d in expand_circuit(circuit) if d.kind == "nfet"]
        self.assertEqual(len(nfets), 2)
        self.assertTrue(all(d.nets["D"] == "ny" and d.nets["S"] == "VGND" for d in nfets))

    def test_c17_device_count(self):
        with open("benchmarks/c17.bench") as handle:
            circuit = parse_bench(handle.read(), "c17")
        devices = expand_circuit(circuit)
        self.assertEqual(sum(d.kind == "nfet" for d in devices), 12)
        self.assertEqual(sum(d.kind == "rload" for d in devices), 6)

    def test_xor_is_rejected(self):
        circuit_text = "INPUT(a)\nINPUT(b)\nOUTPUT(y)\ny = XOR(a, b)\n"
        with self.assertRaises(ValueError):
            expand_circuit(parse_bench(circuit_text, "t"))


if __name__ == "__main__":
    unittest.main()

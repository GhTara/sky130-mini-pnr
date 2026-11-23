import itertools
import os
import unittest

from minipnr.bench import parse_bench, simulate

HERE = os.path.dirname(__file__)


def load(name):
    with open(os.path.join(HERE, "..", "benchmarks", name + ".bench")) as handle:
        return parse_bench(handle.read(), name)


class BenchTest(unittest.TestCase):
    def test_c17_structure(self):
        circuit = load("c17")
        self.assertEqual(circuit.inputs, ["n1", "n2", "n3", "n6", "n7"])
        self.assertEqual(circuit.outputs, ["n22", "n23"])
        self.assertEqual(len(circuit.gates), 6)
        self.assertTrue(all(g.cell == "sky130_fd_sc_hd__nand2_1" for g in circuit.gates))

    def test_c17_function(self):
        """Compare with the c17 equations written out by hand."""
        circuit = load("c17")
        for bits in itertools.product([0, 1], repeat=5):
            x1, x2, x3, x6, x7 = bits
            nand = lambda a, b: 1 - (a & b)
            n10, n11 = nand(x1, x3), nand(x3, x6)
            n16, n19 = nand(x2, n11), nand(n11, x7)
            values = simulate(circuit, dict(zip(circuit.inputs, bits)))
            self.assertEqual(values["n22"], nand(n10, n16))
            self.assertEqual(values["n23"], nand(n16, n19))

    def test_full_adder_adds(self):
        circuit = load("full_adder")
        for a, b, cin in itertools.product([0, 1], repeat=3):
            values = simulate(circuit, {"na": a, "nb": b, "ncin": cin})
            self.assertEqual(values["ns"] + 2 * values["ncout"], a + b + cin)

    def test_unsupported_gate(self):
        with self.assertRaises(ValueError):
            parse_bench("INPUT(a)\nOUTPUT(y)\ny = NAND(a, a, a)\n", "bad")


if __name__ == "__main__":
    unittest.main()

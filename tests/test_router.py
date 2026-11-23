"""Check small routes by measuring the drawn wires and contacts on a test grid."""

import itertools
import random
import unittest

from minipnr import units as u
from minipnr.floorplan import Floorplan
from minipnr.layout import enclosed_holes, hole_fills, route_shapes
from minipnr.router import M1, M2, Router, RoutingGrid, Terminal


def gap(a, b):
    """Largest of the horizontal and vertical gaps, in layout units."""
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return max(dx, dy)


def spacing_violations(routes):
    violations = []
    for layer, items in route_shapes(routes).items():
        if layer not in ("metal1", "metal2"):
            continue
        for (rect_a, net_a), (rect_b, net_b) in itertools.combinations(items, 2):
            if net_a != net_b and gap(rect_a, rect_b) < u.MIN_SPACING:
                violations.append((layer, net_a, net_b, rect_a, rect_b))
    return violations


def mcon_violations(routes):
    """Return contact pairs closer than 0.19 µm (38 layout units, rule mcon.2)."""
    cuts = route_shapes(routes)["viali"]
    return [pair for pair in itertools.combinations(cuts, 2)
            if gap(pair[0][0], pair[1][0]) < 38]


def io(ix, iy):
    return Terminal("PIN", f"p{ix}_{iy}", [(M2, ix, iy)], False)


def pin(ix, iy):
    return Terminal("cell", f"c{ix}_{iy}", [(M1, ix, iy)], True)


class RouterTest(unittest.TestCase):
    def make_router(self, terminals, rows=2, sites=10, obstacles=None):
        plan = Floorplan(rows, sites, margin_x=0, margin_y=0)
        grid = RoutingGrid(plan, obstacles or {})
        return Router(grid, terminals)

    def test_two_point_net_is_shortest(self):
        router = self.make_router({"a": [io(1, 0), io(1, 10)]})
        router.route_all(["a"])
        route = router.routes["a"]
        self.assertEqual(route.wirelength(), 10 * u.TRACK_Y_PITCH)
        self.assertEqual(route.vias, [])

    def test_crossing_nets_use_both_layers(self):
        terminals = {"h": [pin(0, 5), pin(8, 5)], "v": [io(4, 0), io(4, 12)]}
        router = self.make_router(terminals)
        router.route_all(sorted(terminals))
        self.assertEqual(router.find_conflicts(), set())
        self.assertEqual(spacing_violations(router.routes), [])

    def test_obstacle_is_avoided(self):
        # A metal1 bar over x tracks 2..6 at track y=5 forces a detour.
        y = 34 + 68 * 5
        bar = (46 + 92 * 2, y - 14, 46 + 92 * 6, y + 14)
        router = self.make_router({"a": [pin(0, 5), pin(8, 5)]}, obstacles={M1: [(bar, None)]})
        router.route_all(["a"])
        for layer, a, b in router.routes["a"].edges:
            self.assertFalse(layer == M1 and a[1] == 5 and 1 <= a[0] <= 7)

    def test_random_nets_have_no_spacing_errors(self):
        rng = random.Random(7)
        for trial in range(5):
            # Leave a free track between vertically aligned pins to meet mcon.2.
            nodes = []
            while len(nodes) < 12:
                x, y = rng.randrange(12), rng.randrange(16)
                if all(not (x == nx and abs(y - ny) <= 1) for nx, ny in nodes):
                    nodes.append((x, y))
            terminals = {}
            for k in range(4):
                a, b, c = nodes[3 * k:3 * k + 3]
                terminals[f"n{k}"] = [pin(*a), pin(*b), pin(*c)]
            router = self.make_router(terminals, rows=2, sites=12)
            router.route_all(sorted(terminals))
            self.assertEqual(router.find_conflicts(), set())
            self.assertEqual(spacing_violations(router.routes), [], f"trial {trial}")
            self.assertEqual(mcon_violations(router.routes), [], f"trial {trial}")


class HoleFillTest(unittest.TestCase):
    def test_ring_of_two_wires_and_two_patches_is_filled(self):
        # In this c17 layout, two metal pads join parallel wires and enclose a gap.
        ring = [(-10824, -8480, -10704, -8452), (-10824, -8412, -10704, -8384),
                (-10842, -8466, -10778, -8398), (-10750, -8466, -10686, -8398)]
        self.assertEqual(enclosed_holes(ring), [(-10778, -8452, -10750, -8412)])
        fills = hole_fills([(r, "n1") for r in ring])
        self.assertEqual(fills, [((-10778, -8452, -10750, -8412), "n1")])

    def test_hole_with_other_net_inside_is_not_filled(self):
        ring = [(0, 0, 300, 30), (0, 270, 300, 300), (0, 0, 30, 300), (270, 0, 300, 300)]
        other = [((140, 140, 160, 160), "n2")]
        self.assertEqual(hole_fills([(r, "n1") for r in ring] + other), [])

    def test_open_shape_has_no_hole(self):
        u_shape = [(0, 0, 300, 30), (0, 0, 30, 300), (270, 0, 300, 300)]
        self.assertEqual(enclosed_holes(u_shape), [])


if __name__ == "__main__":
    unittest.main()

"""Two-layer A* router with negotiated congestion (PathFinder, McMurchie and Ebeling, 1995).
Metal1 runs horizontally and metal2 vertically. Adjacent rows need extra
clearance for pads and contacts. Magic checks the final layout.
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass, field

from . import units as u
from .floorplan import track_x, track_y

M1, M2 = 1, 2
STEP_COST = {M1: u.TRACK_X_PITCH, M2: u.TRACK_Y_PITCH}
VIA_COST = 300   # 1.5 um of wire; vias add resistance and block tracks
MCON_COST = 100
HISTORY_UNIT = 92


@dataclass
class Terminal:
    owner: str            # instance name, or "PIN" for a top-level I/O pin
    pin: str
    nodes: list           # candidate grid nodes (layer, ix, iy)
    needs_mcon: bool      # True for pins on li1 (an mcon is dropped), else False
    stubs: dict = field(default_factory=dict)   # node -> (layer name, rect, shadow node)


@dataclass
class NetRoute:
    net: str
    nodes: list = field(default_factory=list)   # all grid nodes used
    edges: list = field(default_factory=list)   # (layer, (ix, iy), (ix, iy))
    vias: list = field(default_factory=list)    # (ix, iy)
    mcons: list = field(default_factory=list)   # (ix, iy)
    joint_pads: list = field(default_factory=list)  # nodes joining fixed metal
    stubs: list = field(default_factory=list)       # (layer name, rect) bars to terminals
    shadows: list = field(default_factory=list)     # nodes a stub makes unusable

    def wirelength(self):
        total = 0
        for layer, _, _ in self.edges:
            total += STEP_COST[layer]
        return total


def rects_overlap(a, b):
    """Strict overlap: rectangles that only touch do not overlap."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def spacing_gap(a, b):
    """Return the larger of the x and y gaps, in grid units."""
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return max(dx, dy)


class RoutingGrid:
    """Map fixed metal onto routing nodes and edges. A footprint overlapping
    fixed metal is usable only by that net and must clear other shapes.
    """

    def __init__(self, plan, obstacles):
        self.plan = plan
        self.blocked_nodes = set()
        self.node_owner = {}       # node -> the only net allowed there
        self.blocked_edges = set()
        self.edge_owner = {}
        half_w = u.VIA1_PAD_WIDTH // 2     # largest pad on both metals
        half_h = u.VIA1_PAD_HEIGHT // 2
        half_wire = u.WIRE_WIDTH // 2

        for layer in (M1, M2):
            shapes = obstacles.get(layer, [])
            for ix in range(plan.ix_min, plan.ix_max + 1):
                for iy in range(plan.iy_min, plan.iy_max + 1):
                    x, y = track_x(ix), track_y(iy)
                    node = (layer, ix, iy)
                    pad = (x - half_w, y - half_h, x + half_w, y + half_h)
                    self.classify(pad, shapes, node, self.blocked_nodes, self.node_owner)

                    # Edge to the next node in the preferred direction.
                    if layer == M1:
                        x2, y2 = track_x(ix + 1), y
                        other = (layer, ix + 1, iy)
                    else:
                        x2, y2 = x, track_y(iy + 1)
                        other = (layer, ix, iy + 1)
                    wire = (min(x, x2) - half_wire, min(y, y2) - half_wire,
                            max(x, x2) + half_wire, max(y, y2) + half_wire)
                    self.classify(wire, shapes, (node, other), self.blocked_edges,
                                  self.edge_owner)

    @staticmethod
    def classify(rect, shapes, key, blocked, owner):
        touching = set()
        for shape, net in shapes:
            if spacing_gap(rect, shape) >= u.MIN_SPACING:
                continue
            if net is None or not rects_overlap(rect, shape):
                blocked.add(key)
                return
            touching.add(net)
        if len(touching) > 1:
            blocked.add(key)
        elif touching:
            owner[key] = touching.pop()

    def in_bounds(self, node):
        _, ix, iy = node
        plan = self.plan
        return plan.ix_min <= ix <= plan.ix_max and plan.iy_min <= iy <= plan.iy_max

    def edge_allowed(self, net, a, b):
        for key in ((a, b), (b, a)):
            if key in self.blocked_edges:
                return False
            if self.edge_owner.get(key, net) != net:
                return False
        return True


class Router:
    def __init__(self, grid, net_terminals):
        self.grid = grid
        self.net_terminals = net_terminals      # net -> [Terminal]
        # Pin access points are kept free for the net that owns the pin.
        self.reserved = {}
        for net, terminals in net_terminals.items():
            for terminal in terminals:
                for node in terminal.nodes:
                    self.reserved[node] = net
        self.usage = defaultdict(set)    # node -> nets using it
        self.pads = defaultdict(set)     # node -> nets with a pad there
        self.mcons = defaultdict(set)    # (ix, iy) -> nets with an mcon there
        self.history = defaultdict(int)  # node -> number of past conflicts
        self.present_factor = 0.5
        self.routes = {}
        self.expanded_nodes = 0
        self.conflicts_per_iteration = []

    # ----- costs ------------------------------------------------------

    def node_allowed(self, net, node):
        """Hard limits: grid border, fixed obstacles, other nets' pins."""
        if not self.grid.in_bounds(node) or node in self.grid.blocked_nodes:
            return False
        if self.grid.node_owner.get(node, net) != net:
            return False
        return self.reserved.get(node, net) == net

    def others(self, net, table, key):
        return len(table.get(key, set()) - {net})

    def pad_conflicts(self, net, layer, ix, iy):
        """Other nets with a pad on a vertically neighbouring node."""
        return (self.others(net, self.pads, (layer, ix, iy - 1))
                + self.others(net, self.pads, (layer, ix, iy + 1)))

    def mcon_conflicts(self, net, ix, iy):
        # Any mcon right above or below is too close, even of the same net.
        count = self.pad_conflicts(net, M1, ix, iy)
        count += len(self.mcons.get((ix, iy - 1), set()))
        count += len(self.mcons.get((ix, iy + 1), set()))
        return count

    def cost(self, base, node, conflicts):
        history = HISTORY_UNIT * self.history.get(node, 0)
        return (base + history) * (1 + self.present_factor * conflicts)

    def mcon_cost(self, net, node):
        _, ix, iy = node
        return self.cost(MCON_COST, node, self.mcon_conflicts(net, ix, iy))

    # ----- search -----------------------------------------------------

    def neighbours(self, net, node):
        layer, ix, iy = node
        if layer == M1:
            steps = [(M1, ix - 1, iy), (M1, ix + 1, iy)]
        else:
            steps = [(M2, ix, iy - 1), (M2, ix, iy + 1)]
        for other in steps:
            if self.node_allowed(net, other) and self.grid.edge_allowed(net, node, other):
                conflicts = self.others(net, self.usage, other)
                if other in self.grid.node_owner:      # a pad will be drawn here
                    conflicts += self.pad_conflicts(net, *other)
                yield other, self.cost(STEP_COST[layer], other, conflicts)

        other_layer = M2 if layer == M1 else M1
        via_node = (other_layer, ix, iy)
        if self.node_allowed(net, via_node):
            conflicts = (self.others(net, self.usage, via_node)
                         + self.pad_conflicts(net, M1, ix, iy)
                         + self.pad_conflicts(net, M2, ix, iy))
            yield via_node, self.cost(VIA_COST, via_node, conflicts)

    @staticmethod
    def heuristic(node, targets):
        _, ix, iy = node
        best = None
        for _, tx, ty in targets:
            cost = abs(ix - tx) * u.TRACK_X_PITCH + abs(iy - ty) * u.TRACK_Y_PITCH
            if best is None or cost < best:
                best = cost
        return best

    def astar(self, net, sources, targets):
        """Find a least-cost path including the pin-access costs supplied in sources and targets."""
        sink = ("SINK",)
        came_from = {}
        best_cost = {}
        heap = []
        counter = 0
        for node, start_cost in sources.items():
            best_cost[node] = start_cost
            came_from[node] = None
            heapq.heappush(heap, (start_cost + self.heuristic(node, targets), start_cost,
                                  counter, node))
            counter += 1

        while heap:
            _, cost, _, node = heapq.heappop(heap)
            if node == sink:
                path = [came_from[sink]]
                while came_from[path[-1]] is not None:
                    path.append(came_from[path[-1]])
                return list(reversed(path))
            if cost > best_cost[node]:
                continue
            self.expanded_nodes += 1

            if node in targets:
                finished_cost = cost + targets[node]
                if sink not in best_cost or finished_cost < best_cost[sink]:
                    best_cost[sink] = finished_cost
                    came_from[sink] = node
                    heapq.heappush(heap, (finished_cost, finished_cost, counter, sink))
                    counter += 1

            for other, step in self.neighbours(net, node):
                new_cost = cost + step
                if other not in best_cost or new_cost < best_cost[other]:
                    best_cost[other] = new_cost
                    came_from[other] = node
                    priority = new_cost + self.heuristic(other, targets)
                    heapq.heappush(heap, (priority, new_cost, counter, other))
                    counter += 1
        return None

    # ----- one net ----------------------------------------------------

    def terminal_costs(self, net, terminal):
        costs = {}
        for node in terminal.nodes:
            if self.node_allowed(net, node):
                costs[node] = self.mcon_cost(net, node) if terminal.needs_mcon else 0
        return costs

    def add_mcon(self, route, net, node):
        _, ix, iy = node
        self.mcons[(ix, iy)].add(net)
        self.pads[(M1, ix, iy)].add(net)
        route.mcons.append((ix, iy))

    def reach_terminal(self, route, net, terminal, node):
        """Extra metal where the route lands on a terminal."""
        if terminal.needs_mcon:
            self.add_mcon(route, net, node)
        if node in terminal.stubs:
            layer_name, stub, shadow = terminal.stubs[node]
            route.stubs.append((layer_name, stub))
            route.shadows.append(shadow)
            self.usage[shadow].add(net)

    def add_path(self, route, net, path):
        for node in path:
            self.usage[node].add(net)
            if node not in route.nodes:
                route.nodes.append(node)
            if node in self.grid.node_owner and node not in route.joint_pads:
                self.pads[node].add(net)
                route.joint_pads.append(node)
        for a, b in zip(path, path[1:]):
            if a[0] == b[0]:
                route.edges.append((a[0], a[1:], b[1:]))
            else:
                _, ix, iy = a
                self.pads[(M1, ix, iy)].add(net)
                self.pads[(M2, ix, iy)].add(net)
                route.vias.append((ix, iy))

    def remove_route(self, net):
        route = self.routes.pop(net)
        for node in route.nodes:
            self.usage[node].discard(net)
        for ix, iy in route.vias:
            self.pads[(M1, ix, iy)].discard(net)
            self.pads[(M2, ix, iy)].discard(net)
        for ix, iy in route.mcons:
            self.mcons[(ix, iy)].discard(net)
            self.pads[(M1, ix, iy)].discard(net)
        for node in route.joint_pads:
            self.pads[node].discard(net)
        for node in route.shadows:
            self.usage[node].discard(net)

    def route_net(self, net):
        """Grow a tree from the first terminal, adding the cheapest terminal each time."""
        terminals = list(self.net_terminals[net])
        route = NetRoute(net)
        first = terminals.pop(0)
        sources = self.terminal_costs(net, first)
        first_connection = True

        while terminals:
            targets = {}
            target_terminal = {}
            for index, terminal in enumerate(terminals):
                for node, extra in self.terminal_costs(net, terminal).items():
                    targets[node] = extra
                    target_terminal[node] = index
            path = self.astar(net, sources, targets) if targets and sources else None
            if path is None:
                return None

            self.add_path(route, net, path)
            if first_connection:
                self.reach_terminal(route, net, first, path[0])
            first_connection = False

            reached = terminals.pop(target_terminal[path[-1]])
            self.reach_terminal(route, net, reached, path[-1])
            sources = {node: 0 for node in route.nodes}

        self.routes[net] = route
        return route

    # ----- all nets ---------------------------------------------------

    def find_conflicts(self):
        """Nodes that break a rule: shared by nets, or pads too close."""
        conflicts = set()
        for node, nets in self.usage.items():
            if len(nets) > 1:
                conflicts.add(node)
        for (layer, ix, iy), nets in self.pads.items():
            above = self.pads.get((layer, ix, iy + 1), set())
            if nets and above and (len(nets | above) > 1):
                conflicts.add((layer, ix, iy))
                conflicts.add((layer, ix, iy + 1))
        for (ix, iy), nets in self.mcons.items():
            if nets and self.mcons.get((ix, iy + 1)):
                conflicts.add((M1, ix, iy))
                conflicts.add((M1, ix, iy + 1))
        return conflicts

    def route_all(self, order, max_iterations=100):
        """Reroute nets and return the first conflict-free pass number.

        Raise RuntimeError if a net has no path or conflicts persist at the
        pass limit.
        """
        for iteration in range(1, max_iterations + 1):
            for net in order:
                if net in self.routes:
                    self.remove_route(net)
                if self.route_net(net) is None:
                    raise RuntimeError(f"net {net} cannot be routed (blocked pins?)")
            conflicts = self.find_conflicts()
            self.conflicts_per_iteration.append(len(conflicts))
            if not conflicts:
                return iteration
            for node in conflicts:
                self.history[node] += 1
            self.present_factor *= 1.5
        raise RuntimeError(f"routing did not converge in {max_iterations} iterations")

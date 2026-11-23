"""Write placed standard cells, wires and contacts to a Magic layout."""

import os
from dataclasses import dataclass

from . import units as u
from .floorplan import (FILL_CELL, TAP_CELL, pin_access_offsets, to_absolute,
                        track_x, track_y, transform_rect)
from .router import M1, M2, Terminal, rects_overlap


@dataclass
class Instance:
    name: str
    cell: str
    row: int
    site: int


def build_instances(plan, placement, circuit, macros):
    """Logic cells from the placement, plus taps and fills in every free site."""
    instances = []
    used = set()
    for gate in circuit.gates:
        row, site = placement[gate.name]
        instances.append(Instance(gate.name, gate.cell, row, site))
        for s in range(site, site + macros[gate.cell].width // u.SITE_WIDTH):
            used.add((row, s))

    for row in range(plan.n_rows):
        instances.append(Instance(f"tap_{row}_0", TAP_CELL, row, 0))
        last = plan.row_sites - 1
        instances.append(Instance(f"tap_{row}_{last}", TAP_CELL, row, last))
        used.add((row, 0))
        used.add((row, last))
        for site in range(plan.row_sites):
            if (row, site) not in used:
                instances.append(Instance(f"fill_{row}_{site}", FILL_CELL, row, site))
    return instances


# Power rails

def power_rails(plan):
    """Rail k sits at y = k * ROW_HEIGHT. Even rails are VGND, odd are VPWR."""
    rails = {"VGND": [], "VPWR": []}
    for k in range(plan.n_rows + 1):
        net = "VGND" if k % 2 == 0 else "VPWR"
        rails[net].append(k * u.ROW_HEIGHT)
    return rails


def power_straps(plan):
    """Vertical metal2 straps tying all rails of one net together.

    Returns [(net, x, [rail y, ...])]. VGND uses the track over site 0,
    VPWR the track over site 1. A net with one rail needs no strap.
    """
    straps = []
    rails = power_rails(plan)
    for net, column in (("VGND", 0), ("VPWR", 1)):
        if len(rails[net]) >= 2:
            straps.append((net, track_x(column), rails[net]))
    return straps


def strap_shapes(plan):
    """Shapes of the power straps: {layer name: [rect]}."""
    shapes = {"metal1": [], "metal2": [], "via1": []}
    for _, x, rail_ys in power_straps(plan):
        half = u.STRAP_WIDTH // 2
        shapes["metal2"].append((x - half, min(rail_ys), x + half, max(rail_ys)))
        for y in rail_ys:
            shapes["via1"].append(centered(x, y, u.VIA1_SIZE, u.VIA1_SIZE))
            shapes["metal1"].append(centered(x, y, u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT))
            shapes["metal2"].append(centered(x, y, u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT))
    return shapes


# Router inputs

def obstacles(plan, instances, macros):
    """Fixed metal shapes: {M1/M2: [(rect, net)]}.

    Power pins of the cells are tagged with their net (VPWR/VGND, which the
    router never routes); cell obstructions have net None.
    """
    result = {M1: [], M2: []}
    layer_ids = {"met1": M1, "met2": M2}
    for inst in instances:
        macro = macros[inst.cell]
        shapes = [(layer, rect, None) for layer, rect in macro.obstructions]
        for pin, pin_shapes in macro.pins.items():
            net = pin if pin in ("VPWR", "VGND") else None
            shapes.extend((layer, rect, net) for layer, rect in pin_shapes)
        for layer, rect, net in shapes:
            if layer in layer_ids:
                placed = transform_rect(plan, inst.row, inst.site, rect)
                result[layer_ids[layer]].append((placed, net))

    for net, x, rail_ys in power_straps(plan):
        half = u.STRAP_WIDTH // 2
        result[M2].append(((x - half, min(rail_ys), x + half, max(rail_ys)), net))
        for y in rail_ys:
            pad = centered(x, y, u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT)
            result[M1].append((pad, net))
            result[M2].append((pad, net))
    return result


def node_of_point(x, y):
    ix = (x - u.TRACK_X_OFFSET) // u.TRACK_X_PITCH
    iy = (y - u.TRACK_Y_OFFSET) // u.TRACK_Y_PITCH
    assert track_x(ix) == x and track_y(iy) == y, "point is not on the grid"
    return ix, iy


def build_terminals(plan, circuit, placement, macros):
    """Router terminals for every net with at least two connections."""
    gates = {gate.name: gate for gate in circuit.gates}
    terminals = {}
    for net, connections in circuit.nets().items():
        net_terminals = []
        for owner, pin in connections:
            if owner == "PIN":
                ix, iy = plan.io_nodes[pin]
                net_terminals.append(Terminal(owner, pin, [(M2, ix, iy)], False))
                continue
            gate = gates[owner]
            row, site = placement[owner]
            nodes = []
            for dx, dy in pin_access_offsets(macros[gate.cell], pin):
                x, y = to_absolute(plan, row, site, dx, dy)
                ix, iy = node_of_point(x, y)
                nodes.append((M1, ix, iy))
            net_terminals.append(Terminal(owner, pin, nodes, True))
        if len(net_terminals) >= 2:
            terminals[net] = net_terminals
    return terminals


# Routing shapes

def centered(x, y, width, height):
    return (x - width // 2, y - height // 2, x + width // 2, y + height // 2)


def enclosed_holes(rects):
    """Return rectangles covering gaps enclosed by metal."""
    xs = sorted({r[0] for r in rects} | {r[2] for r in rects})
    ys = sorted({r[1] for r in rects} | {r[3] for r in rects})
    nx, ny = len(xs) - 1, len(ys) - 1
    if nx < 3 or ny < 3:
        return []
    covered = [[False] * ny for _ in range(nx)]
    for r in rects:
        for i in range(xs.index(r[0]), xs.index(r[2])):
            for j in range(ys.index(r[1]), ys.index(r[3])):
                covered[i][j] = True
    outside = set()
    stack = [(i, j) for i in range(nx) for j in (0, ny - 1)] + \
            [(i, j) for i in (0, nx - 1) for j in range(ny)]
    while stack:
        i, j = stack.pop()
        if not (0 <= i < nx and 0 <= j < ny) or (i, j) in outside or covered[i][j]:
            continue
        outside.add((i, j))
        stack += [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
    return [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(nx) for j in range(ny)
            if not covered[i][j] and (i, j) not in outside]


def hole_fills(items, fixed=()):
    """Find fills for metal holes (met1.7, met2.7) that avoid overlap with other nets or unassigned metal."""
    from .devices import connected_groups   # local import: devices imports router
    everything = list(items) + list(fixed)
    fills = []
    for net in {net for _, net in items if net is not None}:
        own = [rect for rect, n in everything if n == net]
        others = [rect for rect, n in everything if n != net]
        for group in connected_groups(own):
            for hole in enclosed_holes(group):
                if not any(rects_overlap(hole, rect) for rect in others):
                    fills.append((hole, net))
    return fills


def route_shapes(routes, fixed=None):
    """Return wired rectangles per layer as (rect, net) pairs. Fixed metal participates in hole checks."""
    shapes = {"metal1": [], "metal2": [], "viali": [], "via1": []}
    half_wire = u.WIRE_WIDTH // 2
    for net, route in routes.items():
        for layer, (ix1, iy1), (ix2, iy2) in route.edges:
            x1, x2 = sorted((track_x(ix1), track_x(ix2)))
            y1, y2 = sorted((track_y(iy1), track_y(iy2)))
            name = "metal1" if layer == M1 else "metal2"
            wire = (x1 - half_wire, y1 - half_wire, x2 + half_wire, y2 + half_wire)
            shapes[name].append((wire, net))

        pad_nodes = {M1: set(), M2: set()}
        for ix, iy in route.vias:
            x, y = track_x(ix), track_y(iy)
            shapes["via1"].append((centered(x, y, u.VIA1_SIZE, u.VIA1_SIZE), net))
            for name in ("metal1", "metal2"):
                pad = centered(x, y, u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT)
                shapes[name].append((pad, net))
            pad_nodes[M1].add((ix, iy))
            pad_nodes[M2].add((ix, iy))
        for ix, iy in route.mcons:
            x, y = track_x(ix), track_y(iy)
            shapes["viali"].append((centered(x, y, u.MCON_SIZE, u.MCON_SIZE), net))
            pad = centered(x, y, u.MCON_PAD_WIDTH, u.MCON_PAD_HEIGHT)
            shapes["metal1"].append((pad, net))
            pad_nodes[M1].add((ix, iy))
        for layer_name, stub in route.stubs:
            shapes.setdefault(layer_name, []).append((stub, net))
        # A full pad joins the wire to existing metal on the same net.
        joint_nodes = set()
        for layer, ix, iy in route.joint_pads:
            name = "metal1" if layer == M1 else "metal2"
            pad = centered(track_x(ix), track_y(iy), u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT)
            shapes[name].append((pad, net))
            pad_nodes[layer].add((ix, iy))
            joint_nodes.add((layer, ix, iy))

        # Two full pads on adjacent rows of the same net leave a 0.08 µm gap.
        # Match the narrower pad to avoid a 0.015 µm notch.
        via_nodes = set(route.vias)
        for layer, nodes in pad_nodes.items():
            name = "metal1" if layer == M1 else "metal2"
            for ix, iy in nodes:
                if (ix, iy + 1) not in nodes:
                    continue
                widths = []
                for node in ((ix, iy), (ix, iy + 1)):
                    full = node in via_nodes or (layer,) + node in joint_nodes
                    widths.append(u.VIA1_PAD_WIDTH if full else u.MCON_PAD_WIDTH)
                patch_half = min(widths) // 2
                x = track_x(ix)
                patch = (x - patch_half, track_y(iy), x + patch_half, track_y(iy + 1))
                shapes[name].append((patch, net))

    fixed = fixed or {}
    for name in ("metal1", "metal2"):
        shapes[name] += hole_fills(shapes[name], fixed.get(name, ()))
    return shapes


# Magic output

def read_mag_header(mag_dir, cell):
    """Timestamp and bounding box of a library cell, used in 'use' records."""
    timestamp, bbox = "0", None
    with open(os.path.join(mag_dir, cell + ".mag")) as handle:
        for line in handle:
            if line.startswith("timestamp") and timestamp == "0":
                timestamp = line.split()[1]
            if line.startswith("string FIXED_BBOX"):
                bbox = line.split()[2:6]
    if bbox is None:
        raise ValueError(f"{cell}.mag has no FIXED_BBOX")
    return timestamp, bbox


def write_mag(path, top_name, plan, instances, routes, mag_dir, circuit,
              include_routing=True):
    paint = {"metal1": [], "metal2": [], "viali": [], "via1": []}
    if include_routing:
        for layer, items in route_shapes(routes).items():
            paint[layer].extend(rect for rect, _ in items)
        for layer, rects in strap_shapes(plan).items():
            paint[layer].extend(rects)

    lines = ["magic", "tech sky130A", "magscale 1 2", "timestamp 0"]
    for layer in ("metal1", "metal2", "viali", "via1"):
        if paint[layer]:
            lines.append(f"<< {layer} >>")
            lines.extend("rect %d %d %d %d" % rect for rect in paint[layer])

    headers = {}
    for inst in instances:
        if inst.cell not in headers:
            headers[inst.cell] = read_mag_header(mag_dir, inst.cell)
        timestamp, bbox = headers[inst.cell]
        x0, y0 = inst.site * u.SITE_WIDTH, inst.row * u.ROW_HEIGHT
        if plan.row_is_flipped(inst.row):
            transform = f"1 0 {x0} 0 -1 {y0 + u.ROW_HEIGHT}"
        else:
            transform = f"1 0 {x0} 0 1 {y0}"
        lines += [f"use {inst.cell}  {inst.name}", f"timestamp {timestamp}",
                  f"transform {transform}", "box " + " ".join(bbox)]

    lines.append("<< labels >>")
    port = 1
    for net in circuit.inputs + circuit.outputs:
        ix, iy = plan.io_nodes[net]
        rect = centered(track_x(ix), track_y(iy), u.WIRE_WIDTH, u.WIRE_WIDTH)
        direction = "input" if net in circuit.inputs else "output"
        lines.append("flabel metal2 s %d %d %d %d 0 FreeSans 60 0 0 0 " % rect + net)
        lines.append(f"port {port} nsew signal {direction}")
        port += 1
    for net, rail_y in (("VPWR", u.ROW_HEIGHT), ("VGND", 0)):
        rect = (20, rail_y - 20, 72, rail_y + 20)
        lines.append("flabel metal1 s %d %d %d %d 0 FreeSans 60 0 0 0 " % rect + net)
        use = "power" if net == "VPWR" else "ground"
        lines.append(f"port {port} nsew {use} bidirectional")
        port += 1

    x1, y1, x2, y2 = plan.die_box()
    lines += ["<< properties >>", f"string FIXED_BBOX {x1} {y1} {x2} {y2}", "<< end >>"]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")

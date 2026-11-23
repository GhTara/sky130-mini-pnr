"""Place and route SKY130 transistors and resistors drawn by Magic."""

import json
import os
from collections import Counter
from dataclasses import replace
import re
import time
from dataclasses import dataclass, field

from . import units as u
from .bench import parse_bench
from .device_netlist import (DEVICE_TYPES, expand_circuit, is_resistor, resistor_kind,
                             write_reference_spice)
from .device_placer import place_devices, required_gap
from .devices import access_points, load_device, shift
from .flow import check_simulation
from .floorplan import spread_columns, track_x, track_y
from .layout import route_shapes
from .plot import plot_device_layout
from .router import M1, M2, Router, RoutingGrid, Terminal
from .substrate_ring import HALF as RING_HALF, ring_rects
from .tools import (read_subckt_ports, run, run_gds_drc, run_lvs, run_magic, run_simulation,
                    write_testbench, find_tool)

GENERATOR_SCRIPT = """\
proc make {{name gen params}} {{
    cellname rename (UNNAMED) $name
    load $name
    set p [sky130::${{gen}}_defaults]
    foreach {{k v}} $params {{ dict set p $k $v }}
    box 0 0 0 0
    sky130::${{gen}}_draw $p
    save $name
    load scratch_empty
}}
{makes}
quit -noprompt
"""

DEVICE_GAP = 120          # 0.6 um between device outlines (guard rings)

# Gaps used for a shared ring on SKY130, in 5 nm units (x, y).
# "tap" means the ring. The resistor gap also allows for poly.2.
SHARED_RING_SPACING = {
    ("nfet", "nfet"): (56, 44),     # diff/tap.3 (0.27 um), poly.2 (0.21 um)
    ("nfet", "rload"): (96, 96),    # poly.9 (0.48 um)
    ("rload", "rload"): (44, 44),   # poly.2 (stacked: see above)
    ("tap", "nfet"): (56, 28),      # diff/tap.3
    ("tap", "rload"): (96, 96),     # poly.9
}
POWER_NETS = ["VPWR", "VGND"]


@dataclass
class GridBounds:
    """Routing grid limits and I/O pin nodes (same fields the router uses)."""
    ix_min: int
    ix_max: int
    iy_min: int
    iy_max: int
    io_nodes: dict = field(default_factory=dict)

    def die_box(self):
        return (track_x(self.ix_min) - u.TRACK_X_PITCH // 2,
                track_y(self.iy_min) - u.TRACK_Y_PITCH // 2,
                track_x(self.ix_max) + u.TRACK_X_PITCH // 2,
                track_y(self.iy_max) + u.TRACK_Y_PITCH // 2)


# ----- device cells ----------------------------------------------------

def run_generator(pdk, work_dir, makes, extra_commands, name):
    """Run Magic with the PDK start-up file (it loads the device generators)."""
    script = GENERATOR_SCRIPT.format(makes="\n".join(makes + extra_commands))
    with open(os.path.join(work_dir, f"{name}.tcl"), "w") as handle:
        handle.write(script)
    env_backup = os.environ.get("PDK_ROOT")
    os.environ["PDK_ROOT"] = pdk.root
    try:
        run([find_tool("magic"), "-dnull", "-noconsole", "-rcfile", pdk.magicrc,
             f"{name}.tcl"], work_dir, f"{name}.log")
    finally:
        if env_backup is not None:
            os.environ["PDK_ROOT"] = env_backup


def make_command(cell, model, params):
    text = " ".join(f"{k} {v}" for k, v in params.items())
    return f"make {cell} {model} {{{text}}}"


def generate_device_cells(pdk, work_dir, generator_params):
    """Draw one cell per device type with Magic's SKY130 generators."""
    makes = [make_command(f"dev_{kind}", DEVICE_TYPES[kind]["model"], params)
             for kind, params in generator_params.items()]
    run_generator(pdk, work_dir, makes, [], "generate_devices")
    layouts = {}
    for kind in generator_params:
        cell = f"dev_{kind}"
        if is_resistor(kind):
            add_rpm_mask_hint(os.path.join(work_dir, cell + ".mag"))
        layouts[kind] = load_device(os.path.join(work_dir, cell + ".mag"), cell)
    return layouts


RPM_ENCLOSURE = 40     # 0.2 um, the growth Magic itself applies to RPM


def add_rpm_mask_hint(path):
    """Give the folded resistor a single implant rectangle with a 0.2 µm margin
    to avoid narrow sections below the 1.27 µm limit (rpm.1).
    """
    with open(path) as handle:
        text = handle.read()
    xs, ys, layer = [], [], None
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "<<":
            layer = words[1]
        elif words[0] == "rect" and layer in ("xpolyres", "xpolycontact"):
            x1, y1, x2, y2 = (int(v) for v in words[1:5])
            xs += [x1, x2]
            ys += [y1, y2]
    box = (min(xs) - RPM_ENCLOSURE, min(ys) - RPM_ENCLOSURE,
           max(xs) + RPM_ENCLOSURE, max(ys) + RPM_ENCLOSURE)
    hint = "string MASKHINTS_RPM {} {} {} {}\n".format(*box)
    if "<< properties >>\n" in text:
        text = text.replace("<< properties >>\n", "<< properties >>\n" + hint, 1)
    else:
        text = text.replace("<< end >>", "<< properties >>\n" + hint + "<< end >>", 1)
    with open(path, "w") as handle:
        handle.write(text)


def extracted_resistor_length(pdk, work_dir, params):
    """Length (um) that Magic extracts for a resistor drawn with `params`."""
    model = DEVICE_TYPES[resistor_kind(2)]["model"]
    extract = ["load cal_res", "extract all", "ext2spice lvs", "ext2spice -o cal_res.spice"]
    run_generator(pdk, work_dir, [make_command("cal_res", model, params)], extract,
                  "calibrate_resistor")
    with open(os.path.join(work_dir, "cal_res.spice")) as handle:
        for line in handle:
            if model in line:
                return float(re.search(r"\bl=([0-9.]+)", line).group(1))
    raise RuntimeError("calibration: no resistor found in the extracted netlist")


SUBSTRATE_TERMINAL = "B"


def best_grid_offset(layout):
    """Choose the track offset that maximises access for the least accessible pin.
    The substrate has a separate ground connection.
    """
    names = [name for name in layout.terminals if name != SUBSTRATE_TERMINAL]
    best = None
    for ox in range(u.TRACK_X_PITCH):
        for oy in range(u.TRACK_Y_PITCH):
            counts = [len(local_access(layout, name, ox, oy)) for name in names]
            score = min(counts)
            if best is None or score > best[0]:
                best = (score, ox, oy)
    if best[0] == 0:
        raise RuntimeError(f"{layout.cell}: no offset gives access to every terminal")
    return best[1], best[2]


def nearby_tracks(lo, hi, offset, pitch, margin=100):
    first = (lo - margin - offset) // pitch
    last = (hi + margin - offset) // pitch + 1
    return [offset + pitch * k for k in range(first, last + 1)
            if lo - margin <= offset + pitch * k <= hi + margin]


def local_access(layout, name, origin_x, origin_y):
    x1, y1, x2, y2 = shift(layout.bbox, origin_x, origin_y)
    xs = nearby_tracks(x1, x2, u.TRACK_X_OFFSET, u.TRACK_X_PITCH)
    ys = nearby_tracks(y1, y2, u.TRACK_Y_OFFSET, u.TRACK_Y_PITCH)
    return access_points(layout, name, origin_x, origin_y, xs, ys)


# ----- placement -------------------------------------------------------

def lattice_at_least(value, offset, pitch):
    """Smallest offset + pitch * k that is >= value."""
    k = -((offset - value) // pitch)
    return offset + pitch * k


def place_in_row(devices, layouts, offsets, device_gap=DEVICE_GAP):
    """Place devices in netlist order at their first legal grid height above or on y = 0."""
    placement = {}
    cursor = 0
    previous = None
    for device in devices:
        layout = layouts[device.kind]
        ox, oy = offsets[device.kind]
        gap_x = 0 if previous is None else required_gap(device_gap, previous, device.kind)[0]
        previous = device.kind
        x = lattice_at_least(cursor + gap_x - layout.bbox[0], ox, u.TRACK_X_PITCH)
        y = lattice_at_least(-layout.bbox[1], oy, u.TRACK_Y_PITCH)
        placement[device.name] = (x, y)
        cursor = x + layout.bbox[2]
    return placement


# ----- routing inputs --------------------------------------------------

def make_bounds(devices, layouts, placement, circuit, margin=3, extra_boxes=()):
    boxes = [shift(layouts[d.kind].bbox, *placement[d.name]) for d in devices]
    boxes += list(extra_boxes)
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    bounds = GridBounds(
        ix_min=(x1 - u.TRACK_X_OFFSET) // u.TRACK_X_PITCH - margin,
        ix_max=(x2 - u.TRACK_X_OFFSET) // u.TRACK_X_PITCH + 1 + margin,
        iy_min=(y1 - u.TRACK_Y_OFFSET) // u.TRACK_Y_PITCH - margin,
        iy_max=(y2 - u.TRACK_Y_OFFSET) // u.TRACK_Y_PITCH + 1 + margin)
    bottom = circuit.inputs + ["VGND"]
    top = circuit.outputs + ["VPWR"]
    first, last = bounds.ix_min + 1, bounds.ix_max - 1
    for net, ix in zip(bottom, spread_columns(len(bottom), first, last)):
        bounds.io_nodes[net] = (ix, bounds.iy_min)
    for net, ix in zip(top, spread_columns(len(top), first, last)):
        bounds.io_nodes[net] = (ix, bounds.iy_max)
    return bounds


def device_obstacles(devices, layouts, placement):
    """metal1 of every device, tagged with the net of its terminal."""
    shapes = {M1: [], M2: []}
    for device in devices:
        layout = layouts[device.kind]
        x, y = placement[device.name]
        terminal_of = {}
        for name, terminal in layout.terminals.items():
            if terminal.layer == "met1":
                for rect in terminal.rects:
                    terminal_of[rect] = name
        for rect in layout.met1_rects:
            name = terminal_of.get(rect)
            net = device.nets[name] if name else None
            shapes[M1].append((shift(rect, x, y), net))
    return shapes


def grid_index_x(x):
    return (x - u.TRACK_X_OFFSET) // u.TRACK_X_PITCH


def grid_index_y(y):
    return (y - u.TRACK_Y_OFFSET) // u.TRACK_Y_PITCH


def shared_ring_box(devices, layouts, placement, spacing):
    """Return the ring centre lines as track indices, allowing the required gap to each device."""
    left = right = bottom = top = None
    for device in devices:
        box = shift(layouts[device.kind].bbox, *placement[device.name])
        gap_x, gap_y = required_gap(spacing, "tap", device.kind)
        candidates = (box[0] - gap_x - RING_HALF, box[2] + gap_x + RING_HALF,
                      box[1] - gap_y - RING_HALF, box[3] + gap_y + RING_HALF)
        left = candidates[0] if left is None else min(left, candidates[0])
        right = candidates[1] if right is None else max(right, candidates[1])
        bottom = candidates[2] if bottom is None else min(bottom, candidates[2])
        top = candidates[3] if top is None else max(top, candidates[3])
    ix1 = (left - u.TRACK_X_OFFSET) // u.TRACK_X_PITCH
    ix2 = -((u.TRACK_X_OFFSET - right) // u.TRACK_X_PITCH)
    iy1 = (bottom - u.TRACK_Y_OFFSET) // u.TRACK_Y_PITCH
    iy2 = -((u.TRACK_Y_OFFSET - top) // u.TRACK_Y_PITCH)
    return (ix1, iy1, ix2, iy2)


def ring_box_units(ring):
    ix1, iy1, ix2, iy2 = ring
    return (track_x(ix1), track_y(iy1), track_x(ix2), track_y(iy2))


def ring_outer_box(ring):
    x1, y1, x2, y2 = ring_box_units(ring)
    return (x1 - RING_HALF, y1 - RING_HALF, x2 + RING_HALF, y2 + RING_HALF)


def ring_nodes(ring):
    """Grid nodes on the ring's li1 strips (an mcon there lands on the ring)."""
    ix1, iy1, ix2, iy2 = ring
    nodes = set()
    for ix in range(ix1, ix2 + 1):
        nodes.add((M1, ix, iy1))
        nodes.add((M1, ix, iy2))
    for iy in range(iy1, iy2 + 1):
        nodes.add((M1, ix1, iy))
        nodes.add((M1, ix2, iy))
    return sorted(nodes)


def device_terminals(devices, layouts, placement, bounds, ring=None):
    """Collect grid access points for each pin. The substrate rings share one ground terminal."""
    terminals = {}
    substrate_nodes, substrate_stubs = [], {}
    for device in devices:
        layout = layouts[device.kind]
        x, y = placement[device.name]
        for name, terminal in layout.terminals.items():
            nodes, stubs = [], {}
            for access in local_access(layout, name, x, y):
                node = (M1, grid_index_x(access.x), grid_index_y(access.y))
                nodes.append(node)
                if access.stub is not None:
                    shadow = (M1, grid_index_x(access.shadow_x), node[2])
                    stubs[node] = (access.stub_layer, access.stub, shadow)
            if name == SUBSTRATE_TERMINAL:
                substrate_nodes.extend(nodes)
                substrate_stubs.update(stubs)
                continue
            net = device.nets[name]
            needs_mcon = terminal.layer == "li1"
            terminals.setdefault(net, []).append(
                Terminal(device.name, name, nodes, needs_mcon, stubs))
    if ring is not None:
        substrate_nodes, substrate_stubs = ring_nodes(ring), {}
    terminals.setdefault("VGND", []).append(
        Terminal("substrate", SUBSTRATE_TERMINAL, substrate_nodes, True, substrate_stubs))
    for net, (ix, iy) in bounds.io_nodes.items():
        terminals.setdefault(net, []).append(Terminal("PIN", net, [(M2, ix, iy)], False))
    return terminals


# ----- output ----------------------------------------------------------

def write_device_mag(path, devices, layouts, placement, routes, bounds, circuit,
                     include_routing=True, ring=None):
    paint = {"psubdiff": [], "locali": [], "psubdiffcont": [], "metal1": [], "metal2": [],
             "viali": [], "via1": []}
    if ring is not None:
        for layer, rects in ring_rects(ring_box_units(ring)).items():
            paint[layer].extend(rects)
    if include_routing:
        fixed = {"metal1": device_obstacles(devices, layouts, placement)[M1]}
        for layer, items in route_shapes(routes, fixed).items():
            paint[layer].extend(rect for rect, _ in items)
    lines = ["magic", "tech sky130A", "magscale 1 2", "timestamp 0"]
    # Magic draws in file order. Draw each contact after the layers it joins.
    for layer in ("psubdiff", "locali", "psubdiffcont", "metal1", "metal2", "viali", "via1"):
        if paint[layer]:
            lines.append(f"<< {layer} >>")
            lines.extend("rect %d %d %d %d" % rect for rect in paint[layer])
    for device in devices:
        layout = layouts[device.kind]
        x, y = placement[device.name]
        lines += [f"use {layout.cell}  {device.name}", "timestamp 0",
                  f"transform 1 0 {x} 0 1 {y}", "box %d %d %d %d" % layout.bbox]
    lines.append("<< labels >>")
    port = 1
    for net in circuit.inputs + circuit.outputs + POWER_NETS:
        ix, iy = bounds.io_nodes[net]
        x, y = track_x(ix), track_y(iy)
        rect = (x - 14, y - 14, x + 14, y + 14)
        lines.append("flabel metal2 s %d %d %d %d 0 FreeSans 60 0 0 0 " % rect + net)
        if net in circuit.inputs:
            kind = "signal input"
        elif net in circuit.outputs:
            kind = "signal output"
        else:
            kind = ("power" if net == "VPWR" else "ground") + " bidirectional"
        lines.append(f"port {port} nsew {kind}")
        port += 1
    x1, y1, x2, y2 = bounds.die_box()
    lines += ["<< properties >>", f"string FIXED_BBOX {x1} {y1} {x2} {y2}", "<< end >>"]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


def die_area_um2(bounds):
    """Area of the whole routed layout, including the I/O and routing margin."""
    x1, y1, x2, y2 = bounds.die_box()
    return round(u.units_to_um(x2 - x1) * u.units_to_um(y2 - y1), 3)


def cluster_area_um2(devices, layouts, placement, ring):
    """Area enclosing all devices and guard rings, in µm²."""
    if ring is None:
        return layout_area_um2(devices, layouts, placement)
    x1, y1, x2, y2 = ring_outer_box(ring)
    return u.units_to_um(x2 - x1) * u.units_to_um(y2 - y1)


def layout_area_um2(devices, layouts, placement):
    """Area of the bounding box of all devices (routing margin excluded)."""
    boxes = [shift(layouts[d.kind].bbox, *placement[d.name]) for d in devices]
    width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    height = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    return u.units_to_um(width) * u.units_to_um(height)


# ----- complete flow ---------------------------------------------------

def run_device_flow(bench_path, out_dir, pdk, period_ns=4.0, placer="row", seed=1,
                    wire_weight=1.0, resistor_folds=(2,), guard="device",
                    given_placement=None):
    """Return measurements and geometry for a placed, routed and checked circuit.
    Row and supplied placements use the first resistor shape in resistor_folds.
    """
    if guard == "shared" and tuple(resistor_folds) != (2,):
        raise ValueError("shared guard ring: spacing is measured for 2-fold resistors only")
    spacing = SHARED_RING_SPACING if guard == "shared" else DEVICE_GAP
    pdk.check()
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(bench_path))[0]
    with open(bench_path) as handle:
        circuit = parse_bench(handle.read(), name)
    top = f"{name}_dev"
    resistor_kinds = [resistor_kind(nx) for nx in resistor_folds]
    devices = [replace(d, kind=resistor_kinds[0]) if is_resistor(d.kind) else d
               for d in expand_circuit(circuit)]

    kinds = ["nfet"] + resistor_kinds
    generator_params = {kind: dict(DEVICE_TYPES[kind]["generator"]) for kind in kinds}
    if guard == "shared":
        for params in generator_params.values():
            params["guard"] = 0
    layouts = generate_device_cells(pdk, out_dir, generator_params)
    extracted_lengths = {kind: extracted_resistor_length(pdk, out_dir, generator_params[kind])
                         for kind in resistor_kinds}
    offsets = {kind: best_grid_offset(layout) for kind, layout in layouts.items()}
    if given_placement is not None:
        placement = dict(given_placement)
        placement_info = {"given": True}
    elif placer == "row":
        placement = place_in_row(devices, layouts, offsets, spacing)
        placement_info = {}
    else:
        variants = {d.name: resistor_kinds for d in devices if is_resistor(d.kind)}
        placement, chosen, placement_info = place_devices(
            devices, layouts, offsets, spacing, placer, seed, wire_weight, variants)
        devices = [replace(d, kind=chosen[d.name]) for d in devices]
    ring = None
    extra_boxes = []
    if guard == "shared":
        ring = shared_ring_box(devices, layouts, placement, spacing)
        extra_boxes = [ring_outer_box(ring)]
    bounds = make_bounds(devices, layouts, placement, circuit, extra_boxes=extra_boxes)

    grid = RoutingGrid(bounds, device_obstacles(devices, layouts, placement))
    terminals = device_terminals(devices, layouts, placement, bounds, ring)
    for net, net_terminals in terminals.items():
        for terminal in net_terminals:
            if not terminal.nodes:
                raise RuntimeError(f"{terminal.owner}/{terminal.pin} has no access point")
    router = Router(grid, terminals)
    started = time.perf_counter()
    iterations = router.route_all(sorted(terminals))
    route_seconds = time.perf_counter() - started

    write_device_mag(os.path.join(out_dir, f"{top}.mag"), devices, layouts, placement,
                     router.routes, bounds, circuit, ring=ring)
    write_device_mag(os.path.join(out_dir, f"{top}_devices_only.mag"), devices, layouts,
                     placement, router.routes, bounds, circuit, include_routing=False,
                     ring=ring)

    title = f"{name}: device-level layout on SKY130 (resistor-load NMOS), placer = {placer}"
    if placer != "row":
        title += f", seed {seed}"
    ring_strips = ()
    if ring is not None:
        title += ", shared guard ring"
        ring_strips = ring_rects(ring_box_units(ring))["psubdiff"]
    plot_device_layout(os.path.join(out_dir, f"{top}_layout.png"), title,
                       devices, layouts, placement, router.routes, bounds, circuit, ring_strips)

    stats = {"design": name, "placer": placer, "seed": seed, "wire_weight": wire_weight,
             "placement": placement_info, "devices": len(devices),
             "nfet": sum(d.kind == "nfet" for d in devices),
             "resistors": sum(is_resistor(d.kind) for d in devices),
             "allowed_resistor_folds": list(resistor_folds),
             "resistor_folds_used": dict(sorted(Counter(
                 DEVICE_TYPES[d.kind]["folds"] for d in devices if is_resistor(d.kind)).items())),
             "grid_offsets": offsets,
             "die_area_um2": die_area_um2(bounds),
             "resistor_segment_um": {k: generator_params[k]["l"] for k in resistor_kinds},
             "resistor_extracted_length_um": extracted_lengths,
             "device_bbox_area_um2": round(layout_area_um2(devices, layouts, placement), 3),
             "guard": guard,
             # devices + every guard ring: the area to compare between guard styles
             "cluster_area_um2": round(cluster_area_um2(devices, layouts, placement, ring), 3),
             "routing_iterations": iterations, "route_seconds": round(route_seconds, 3),
             "via1_count": sum(len(r.vias) for r in router.routes.values()),
             "mcon_count": sum(len(r.mcons) for r in router.routes.values()),
             "routed_wirelength_um": round(u.units_to_um(
                 sum(r.wirelength() for r in router.routes.values())), 3)}

    # Report Magic DRC on the exported GDS and keep the .mag count for comparison.
    drc_mag, _ = run_magic(pdk, out_dir, top, "drc_routed_mag.txt")
    run_magic(pdk, out_dir, f"{top}_devices_only", "drc_devices_only_mag.txt",
              extract=False, write_gds=True)
    drc = run_gds_drc(pdk, out_dir, top, "drc_routed.txt")
    drc_devices = run_gds_drc(pdk, out_dir, f"{top}_devices_only", "drc_devices_only.txt")
    stats["drc"] = {"routed_total": drc["total"], "devices_only_total": drc_devices["total"],
                    "routed_rules": drc["rules"], "devices_only_rules": drc_devices["rules"],
                    "routed_total_mag_hierarchy": drc_mag["total"]}

    reference = f"{top}_reference.spice"
    write_reference_spice(os.path.join(out_dir, reference), circuit, devices, top)
    stats["lvs"] = run_lvs(pdk, out_dir, top, f"{top}_extracted.spice", reference)

    extracted = os.path.join(out_dir, f"{top}_extracted.spice")
    port_order = read_subckt_ports(extracted, [top])[top]
    n_vectors = write_testbench(os.path.join(out_dir, "testbench.spice"), pdk, circuit, top,
                                f"{top}_extracted.spice", port_order, period_ns)
    rows = run_simulation(out_dir, "testbench.spice")
    stats["simulation"] = check_simulation(circuit, rows, period_ns, n_vectors)

    with open(os.path.join(out_dir, "summary.json"), "w") as handle:
        json.dump(stats, handle, indent=2)
    return stats, {"devices": devices, "layouts": layouts, "placement": placement,
                   "routes": router.routes, "bounds": bounds, "circuit": circuit}

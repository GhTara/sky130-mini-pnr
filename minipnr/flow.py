"""Place standard cells, route their connections and check the layout against the input circuit."""

import json
import os
import time

from . import units as u
from .bench import parse_bench, simulate
from .floorplan import FILL_CELL, TAP_CELL, make_floorplan
from .layout import build_instances, build_terminals, obstacles, write_mag
from .lef import parse_lef
from .placer import place
from .plot import plot_layout, plot_waveforms
from .router import M1, Router, RoutingGrid
from .tools import (read_subckt_ports, run_lvs, run_magic, run_simulation,
                    write_schematic, write_testbench)


def load_design(bench_path, pdk):
    name = os.path.splitext(os.path.basename(bench_path))[0]
    with open(bench_path) as handle:
        circuit = parse_bench(handle.read(), name)
    cells = {gate.cell for gate in circuit.gates} | {TAP_CELL, FILL_CELL}
    macros = parse_lef(pdk.lef, cells)
    return circuit, macros


def net_order(terminals):
    """Order nets by pin span using the first access point for each pin."""
    def size(net):
        nodes = [terminal.nodes[0] for terminal in terminals[net]]
        xs = [n[1] for n in nodes]
        ys = [n[2] for n in nodes]
        return (max(xs) - min(xs)) * u.TRACK_X_PITCH + (max(ys) - min(ys)) * u.TRACK_Y_PITCH
    return sorted(terminals, key=lambda net: (size(net), net))


def place_and_route(circuit, macros, seed, utilization=0.6, use_annealing=True):
    """Place the cells and route the nets on the grid. Return geometry and measurements."""
    started = time.perf_counter()
    plan = make_floorplan(circuit, macros, utilization)
    placement, place_info = place(circuit, macros, plan, seed, use_annealing)
    place_seconds = time.perf_counter() - started

    instances = build_instances(plan, placement, circuit, macros)
    terminals = build_terminals(plan, circuit, placement, macros)
    grid = RoutingGrid(plan, obstacles(plan, instances, macros))
    router = Router(grid, terminals)
    started = time.perf_counter()
    iterations = router.route_all(net_order(terminals))
    route_seconds = time.perf_counter() - started

    m1, m2 = 0, 0
    for route in router.routes.values():
        for layer, _, _ in route.edges:
            if layer == M1:
                m1 += u.TRACK_X_PITCH
            else:
                m2 += u.TRACK_Y_PITCH
    die = plan.die_box()
    stats = {
        "design": circuit.name,
        "seed": seed,
        "gates": len(circuit.gates),
        "nets_routed": len(router.routes),
        "rows": plan.n_rows,
        "sites_per_row": plan.row_sites,
        "core_area_um2": round(u.units_to_um(plan.row_sites * u.SITE_WIDTH)
                               * u.units_to_um(plan.n_rows * u.ROW_HEIGHT), 3),
        "die_area_um2": round(u.units_to_um(die[2] - die[0])
                              * u.units_to_um(die[3] - die[1]), 3),
        "annealing": use_annealing,
        "hpwl_random_um": round(place_info["hpwl_random_um"], 3),
        "hpwl_final_um": round(place_info["hpwl_final_um"], 3),
        "routed_wirelength_um": round(u.units_to_um(m1 + m2), 3),
        "metal1_um": round(u.units_to_um(m1), 3),
        "metal2_um": round(u.units_to_um(m2), 3),
        "via1_count": sum(len(r.vias) for r in router.routes.values()),
        "mcon_count": sum(len(r.mcons) for r in router.routes.values()),
        "routing_iterations": iterations,
        "router_expanded_nodes": router.expanded_nodes,
        "place_seconds": round(place_seconds, 3),
        "route_seconds": round(route_seconds, 3),
    }
    return {"plan": plan, "placement": placement, "instances": instances,
            "routes": router.routes, "stats": stats}


def check_simulation(circuit, rows, period_ns, n_vectors):
    """Compare simulated outputs with the logic simulation of the .bench file."""
    vdd = 1.8
    mismatches = []
    worst_high, worst_low = vdd, 0.0
    for k in range(n_vectors):
        sample_time = (k + 0.9) * period_ns * 1e-9
        row = min(rows, key=lambda r: abs(r[0] - sample_time))
        inputs = {net: (k >> bit) & 1 for bit, net in enumerate(circuit.inputs)}
        expected = simulate(circuit, inputs)
        for column, net in enumerate(circuit.outputs, start=1):
            voltage = row[column]
            if expected[net] == 1:
                worst_high = min(worst_high, voltage)
            else:
                worst_low = max(worst_low, voltage)
            if (voltage > vdd / 2) != (expected[net] == 1):
                mismatches.append({"vector": k, "net": net, "voltage": voltage})
    return {"vectors": n_vectors, "mismatches": mismatches,
            "worst_high_v": round(worst_high, 4), "worst_low_v": round(worst_low, 4),
            "pass": not mismatches}


def run_flow(bench_path, out_dir, pdk, seed=1, utilization=0.6, use_annealing=True,
             period_ns=4.0):
    """Write the layout and return its measurements, rule checks and simulation results."""
    pdk.check()
    os.makedirs(out_dir, exist_ok=True)
    circuit, macros = load_design(bench_path, pdk)
    result = place_and_route(circuit, macros, seed, utilization, use_annealing)
    plan, instances, routes = result["plan"], result["instances"], result["routes"]
    stats = result["stats"]
    top = circuit.name

    write_mag(os.path.join(out_dir, f"{top}.mag"), top, plan, instances, routes,
              pdk.mag_dir, circuit)
    write_mag(os.path.join(out_dir, f"{top}_cells_only.mag"), top, plan, instances,
              routes, pdk.mag_dir, circuit, include_routing=False)
    plot_layout(os.path.join(out_dir, f"{top}_layout.png"),
                f"{top} on SKY130 (sky130_fd_sc_hd), seed {seed}",
                plan, instances, macros, routes, circuit)

    # Compare rule counts on the same placement before and after adding wires.
    started = time.perf_counter()
    drc, _ = run_magic(pdk, out_dir, top, "drc_routed.txt")
    drc_cells, _ = run_magic(pdk, out_dir, f"{top}_cells_only", "drc_cells_only.txt",
                             extract=False)
    added = {}
    for rule, count in drc["rules"].items():
        extra = count - drc_cells["rules"].get(rule, 0)
        if extra != 0:
            added[rule] = extra
    stats["drc"] = {"routed_total": drc["total"], "cells_only_total": drc_cells["total"],
                    "routed_rules": drc["rules"], "cells_only_rules": drc_cells["rules"],
                    "added_by_pnr": added}
    stats["magic_seconds"] = round(time.perf_counter() - started, 3)

    # Compare the connections read from the layout with the input circuit.
    schematic = f"{top}_schematic.spice"
    write_schematic(os.path.join(out_dir, schematic), pdk, circuit, top)
    stats["lvs"] = run_lvs(pdk, out_dir, top, f"{top}_extracted.spice", schematic)

    # Test every input combination against the logic in the .bench file.
    extracted = os.path.join(out_dir, f"{top}_extracted.spice")
    port_order = read_subckt_ports(extracted, [top])[top]
    n_vectors = write_testbench(os.path.join(out_dir, "testbench.spice"), pdk, circuit,
                                top, f"{top}_extracted.spice", port_order, period_ns)
    rows = run_simulation(out_dir, "testbench.spice")
    stats["simulation"] = check_simulation(circuit, rows, period_ns, n_vectors)
    expected = {net: [] for net in circuit.outputs}
    for k in range(n_vectors):
        values = simulate(circuit, {net: (k >> bit) & 1 for bit, net in enumerate(circuit.inputs)})
        for net in circuit.outputs:
            expected[net].append(values[net])
    plot_waveforms(os.path.join(out_dir, f"{top}_simulation.png"),
                   f"{top}: transistor-level simulation of the extracted layout "
                   f"(SKY130 tt models, no parasitics)",
                   circuit, rows, period_ns, expected)

    with open(os.path.join(out_dir, "summary.json"), "w") as handle:
        json.dump(stats, handle, indent=2)
    return stats

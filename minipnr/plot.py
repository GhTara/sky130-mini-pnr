"""Plot the routed layouts and simulated output voltages."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from . import units as u  # noqa: E402
from .floorplan import FILL_CELL, TAP_CELL, track_x, track_y  # noqa: E402
from .layout import route_shapes, strap_shapes  # noqa: E402

# Opaque colours, drawn bottom to top: metal1, metal2, via1, mcon.
LAYER_STYLE = {
    "metal1": dict(color="#7fa3e8", zorder=2),
    "metal2": dict(color="#e89a92", zorder=3),
    "via1": dict(color="#333333", zorder=4),
    "viali": dict(color="#2a9d4b", zorder=5),
}


def add_rect(ax, rect, **style):
    x1, y1, x2, y2 = (u.units_to_um(v) for v in rect)
    style.setdefault("linewidth", 0)
    ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, **style))


def plot_layout(path, title, plan, instances, macros, routes, circuit):
    die = plan.die_box()
    width_um = u.units_to_um(die[2] - die[0])
    height_um = u.units_to_um(die[3] - die[1])
    fig, ax = plt.subplots(figsize=(1.0 * width_um + 2, 1.0 * height_um + 1.5))

    add_rect(ax, die, facecolor="none", edgecolor="black", linewidth=1.0)
    for inst in instances:
        macro = macros[inst.cell]
        rect = (inst.site * u.SITE_WIDTH, inst.row * u.ROW_HEIGHT,
                inst.site * u.SITE_WIDTH + macro.width, (inst.row + 1) * u.ROW_HEIGHT)
        physical_only = inst.cell in (TAP_CELL, FILL_CELL)
        face = "#f1f1f1" if physical_only else "#fff3c4"
        x1, y1, x2, y2 = (u.units_to_um(v) for v in rect)
        ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, facecolor=face,
                               edgecolor="#999999", linewidth=0.6))
        if not physical_only:
            kind = inst.cell.replace("sky130_fd_sc_hd__", "")
            ax.text((x1 + x2) / 2, (y1 + y2) / 2, f"{inst.name}\n{kind}", ha="center",
                    va="center", fontsize=7, zorder=6,
                    bbox=dict(facecolor="white", alpha=0.7, linewidth=0, pad=1))

    for layer, rects in strap_shapes(plan).items():
        for rect in rects:
            add_rect(ax, rect, **LAYER_STYLE[layer])
    for layer, items in route_shapes(routes).items():
        for rect, _ in items:
            add_rect(ax, rect, **LAYER_STYLE[layer])

    for net, (ix, iy) in plan.io_nodes.items():
        x, y = u.units_to_um(track_x(ix)), u.units_to_um(track_y(iy))
        below = net in circuit.inputs
        ax.plot(x, y, marker="^" if below else "v", color="black", markersize=6)
        ax.text(x, y - 0.25 if below else y + 0.25, net, ha="center",
                va="top" if below else "bottom", fontsize=8)

    for name, style in (("metal1", "metal1 (horizontal)"), ("metal2", "metal2 (vertical)"),
                        ("via1", "via1"), ("viali", "mcon (li1-met1)")):
        ax.add_patch(Rectangle((0, 0), 0, 0, label=style, **LAYER_STYLE[name]))
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)

    ax.set_xlim(u.units_to_um(die[0]) - 0.3, u.units_to_um(die[2]) + 0.3)
    ax.set_ylim(u.units_to_um(die[1]) - 0.8, u.units_to_um(die[3]) + 0.8)
    ax.set_aspect("equal")
    ax.set_xlabel("x [um]")
    ax.set_ylabel("y [um]")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def plot_waveforms(path, title, circuit, rows, period_ns, expected):
    """Simulated output voltages, with the expected logic level as a dashed line.

    expected: {output net: [0/1 for each input vector]}
    """
    times_ns = [row[0] * 1e9 for row in rows]
    n_outputs = len(circuit.outputs)
    fig, axes = plt.subplots(n_outputs, 1, figsize=(10, 1.8 * n_outputs + 0.8), sharex=True)
    if n_outputs == 1:
        axes = [axes]
    for column, (ax, net) in enumerate(zip(axes, circuit.outputs), start=1):
        ax.plot(times_ns, [row[column] for row in rows], color="#3b6fd8", linewidth=1.0,
                label="extracted layout (ngspice)")
        step_t, step_v = [], []
        for k, bit in enumerate(expected[net]):
            step_t += [k * period_ns, (k + 1) * period_ns]
            step_v += [1.8 * bit, 1.8 * bit]
        ax.plot(step_t, step_v, color="black", linestyle="--", linewidth=0.8,
                label="expected (logic simulation)")
        ax.set_ylabel(f"{net} [V]")
        ax.set_ylim(-0.2, 2.0)
    axes[0].legend(loc="upper right", fontsize=8, ncol=2)
    axes[-1].set_xlabel("time [ns]  (one input vector every %.0f ns)" % period_ns)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_device_layout(path, title, devices, layouts, placement, routes, bounds, circuit,
                       ring_strips=()):
    """Device-level layout: device outlines, their metal1, and the routing."""
    die = bounds.die_box()
    width_um = u.units_to_um(die[2] - die[0])
    height_um = u.units_to_um(die[3] - die[1])
    scale = min(1.0, 16.0 / max(width_um, height_um))
    fig, ax = plt.subplots(figsize=(width_um * scale + 2.5, height_um * scale + 1.5))
    add_rect(ax, die, facecolor="none", edgecolor="black", linewidth=1.0)

    colours = {"nfet": "#fff3c4", "rload": "#e3f1df"}
    for rect in ring_strips:
        add_rect(ax, rect, color="#b0a090", zorder=1)
    for device in devices:
        layout = layouts[device.kind]
        x, y = placement[device.name]
        x1, y1, x2, y2 = (u.units_to_um(v) for v in
                          (layout.bbox[0] + x, layout.bbox[1] + y,
                           layout.bbox[2] + x, layout.bbox[3] + y))
        colour = colours["rload"] if device.kind.startswith("rload") else colours[device.kind]
        ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, facecolor=colour,
                               edgecolor="#999999", linewidth=0.6, zorder=1))
        for rect in layout.met1_rects:
            add_rect(ax, (rect[0] + x, rect[1] + y, rect[2] + x, rect[3] + y),
                     color="#9db3d9", zorder=2)
        ax.text((x1 + x2) / 2, y1 - 0.15, device.name, ha="center", va="top", fontsize=6,
                zorder=6)

    styles = dict(LAYER_STYLE)
    styles["locali"] = dict(color="#8c6bb1", zorder=2)
    for layer, items in route_shapes(routes).items():
        for rect, _ in items:
            add_rect(ax, rect, **styles[layer])

    for net, (ix, iy) in bounds.io_nodes.items():
        x, y = u.units_to_um(track_x(ix)), u.units_to_um(track_y(iy))
        below = iy == bounds.iy_min
        ax.plot(x, y, marker="^" if below else "v", color="black", markersize=5, zorder=7)
        ax.text(x, y - 0.3 if below else y + 0.3, net, ha="center",
                va="top" if below else "bottom", fontsize=7)

    legend = [("nfet", dict(facecolor=colours["nfet"], edgecolor="#999999")),
              ("poly resistor", dict(facecolor=colours["rload"], edgecolor="#999999")),
              ("device metal1", dict(color="#9db3d9")),
              ("metal1 route", LAYER_STYLE["metal1"]), ("metal2 route", LAYER_STYLE["metal2"]),
              ("via1", LAYER_STYLE["via1"]), ("mcon", LAYER_STYLE["viali"]),
              ("li1 stub", styles["locali"])]
    if ring_strips:
        legend.append(("shared substrate ring", dict(color="#b0a090")))
    for label, style in legend:
        ax.add_patch(Rectangle((0, 0), 0, 0, label=label, **style))
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=7, frameon=False)
    ax.set_xlim(u.units_to_um(die[0]) - 0.3, u.units_to_um(die[2]) + 0.3)
    ax.set_ylim(u.units_to_um(die[1]) - 0.9, u.units_to_um(die[3]) + 0.9)
    ax.set_aspect("equal")
    ax.set_xlabel("x [um]")
    ax.set_ylabel("y [um]")
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)

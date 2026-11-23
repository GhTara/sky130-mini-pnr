"""Rows, sites, routing tracks and I/O pin locations."""

import math
from dataclasses import dataclass, field

from . import units as u

TAP_CELL = "sky130_fd_sc_hd__tapvpwrvgnd_1"
FILL_CELL = "sky130_fd_sc_hd__fill_1"

# Sites 0 and 1 of every row are reserved: site 0 holds a tap cell and the
# VGND strap, site 1 holds a fill cell and the VPWR strap. The last site
# of every row holds a second tap cell.
RESERVED_SITES_LEFT = 2
RESERVED_SITES_RIGHT = 1


def track_x(ix):
    return u.TRACK_X_OFFSET + u.TRACK_X_PITCH * ix


def track_y(iy):
    return u.TRACK_Y_OFFSET + u.TRACK_Y_PITCH * iy


@dataclass
class Floorplan:
    n_rows: int
    row_sites: int
    margin_x: int            # extra routing tracks left/right of the rows
    margin_y: int            # extra routing tracks below/above the rows
    io_nodes: dict = field(default_factory=dict)   # net -> (ix, iy)

    # Routing grid limits (inclusive).
    @property
    def ix_min(self):
        return -self.margin_x

    @property
    def ix_max(self):
        return self.row_sites - 1 + self.margin_x

    @property
    def iy_min(self):
        return -self.margin_y

    @property
    def iy_max(self):
        tracks_per_row = u.ROW_HEIGHT // u.TRACK_Y_PITCH   # 8
        return self.n_rows * tracks_per_row - 1 + self.margin_y

    @property
    def first_free_site(self):
        return RESERVED_SITES_LEFT

    @property
    def end_free_site(self):
        return self.row_sites - RESERVED_SITES_RIGHT

    def row_is_flipped(self, row):
        """Odd rows are mirrored so neighbouring rows share a power rail."""
        return row % 2 == 1

    def die_box(self):
        """Outline of the routing area, in units."""
        half_pitch_x = u.TRACK_X_PITCH // 2
        half_pitch_y = u.TRACK_Y_PITCH // 2
        return (track_x(self.ix_min) - half_pitch_x,
                track_y(self.iy_min) - half_pitch_y,
                track_x(self.ix_max) + half_pitch_x,
                track_y(self.iy_max) + half_pitch_y)


def make_floorplan(circuit, macros, utilization=0.6, margin_x=3, margin_y=3):
    """Choose rows so the core is roughly square at the given utilization."""
    cell_sites = 0
    for gate in circuit.gates:
        cell_sites += macros[gate.cell].width // u.SITE_WIDTH
    core_sites = math.ceil(cell_sites / utilization)

    # Square core: n_rows * ROW_HEIGHT ~= sites_per_row * SITE_WIDTH.
    n_rows = max(1, round(math.sqrt(core_sites * u.SITE_WIDTH / u.ROW_HEIGHT)))
    reserved = RESERVED_SITES_LEFT + RESERVED_SITES_RIGHT
    row_sites = math.ceil(core_sites / n_rows) + reserved

    widest = max(macros[gate.cell].width // u.SITE_WIDTH for gate in circuit.gates)
    row_sites = max(row_sites, widest + reserved)

    plan = Floorplan(n_rows, row_sites, margin_x, margin_y)
    place_io_pins(plan, circuit)
    return plan


def spread_columns(count, first, last):
    """Return `count` distinct integer columns spread evenly in [first, last]."""
    if count > last - first + 1:
        raise ValueError("not enough tracks for the I/O pins")
    if count == 1:
        return [(first + last) // 2]
    step = (last - first) / (count - 1)
    return [first + round(i * step) for i in range(count)]


def place_io_pins(plan, circuit):
    """Inputs on the bottom edge, outputs on the top edge (on metal2)."""
    first = plan.first_free_site
    last = plan.end_free_site - 1
    for net, ix in zip(circuit.inputs, spread_columns(len(circuit.inputs), first, last)):
        plan.io_nodes[net] = (ix, plan.iy_min)
    for net, ix in zip(circuit.outputs, spread_columns(len(circuit.outputs), first, last)):
        plan.io_nodes[net] = (ix, plan.iy_max)


def pin_access_offsets(macro, pin):
    """Cell-local grid points where the full contact fits inside a pin rectangle, before row mirroring."""
    half = u.MCON_SIZE // 2
    points = []
    for layer, (x1, y1, x2, y2) in macro.pins[pin]:
        if layer != "li1":
            continue
        for dx in range(u.TRACK_X_OFFSET, macro.width, u.TRACK_X_PITCH):
            for dy in range(u.TRACK_Y_OFFSET, macro.height, u.TRACK_Y_PITCH):
                inside_x = x1 <= dx - half and dx + half <= x2
                inside_y = y1 <= dy - half and dy + half <= y2
                if inside_x and inside_y and (dx, dy) not in points:
                    points.append((dx, dy))
    if not points:
        raise ValueError(f"{macro.name}/{pin}: no on-grid access point")
    return points


def cell_origin(row, site):
    return site * u.SITE_WIDTH, row * u.ROW_HEIGHT


def to_absolute(plan, row, site, dx, dy):
    """Cell-local point -> absolute point, taking row flipping into account."""
    x0, y0 = cell_origin(row, site)
    if plan.row_is_flipped(row):
        return x0 + dx, y0 + u.ROW_HEIGHT - dy
    return x0 + dx, y0 + dy


def transform_rect(plan, row, site, rect):
    x1, y1, x2, y2 = rect
    ax1, ay1 = to_absolute(plan, row, site, x1, y1)
    ax2, ay2 = to_absolute(plan, row, site, x2, y2)
    return (min(ax1, ax2), min(ay1, ay2), max(ax1, ax2), max(ay1, ay2))

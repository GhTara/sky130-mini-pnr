"""SKY130 dimensions on Magic’s 5 nm grid (200 units per µm)."""

UNITS_PER_UM = 200


def um_to_units(value_um):
    return int(round(value_um * UNITS_PER_UM))


def units_to_um(value_units):
    return value_units / UNITS_PER_UM


# Standard-cell row (sky130_fd_sc_hd "unithd" site).
SITE_WIDTH = 92          # 0.46 um
ROW_HEIGHT = 544         # 2.72 um

# Routing grid. These are the usual hd routing tracks:
#   met2 vertical tracks   at x = 0.23 + 0.46 * k
#   met1 horizontal tracks at y = 0.17 + 0.34 * k
TRACK_X_OFFSET = 46
TRACK_X_PITCH = 92
TRACK_Y_OFFSET = 34
TRACK_Y_PITCH = 68

# Metal rules (met1.1, met1.2, met2.1, met2.2): width and spacing 0.14 um.
WIRE_WIDTH = 28
MIN_SPACING = 28

# li1 -> met1 contact (mcon.1: 0.17 um square). Metal1 must cover it by
# 0.03 um on all sides (met1.4) and 0.06 um in one direction (met1.5).
MCON_SIZE = 34
MCON_PAD_WIDTH = 58      # 0.17 + 2 * 0.06  (long side along x)
MCON_PAD_HEIGHT = 46     # 0.17 + 2 * 0.03

# met1 -> met2 via. In Magic the via1 layer is drawn 0.26 um
# (0.15 um cut + 2 * 0.055 um enclosure). Both metals need 0.03 um more
# in one direction (via.5a, met2.5). Both pads extend along x.
VIA1_SIZE = 52
VIA1_PAD_WIDTH = 64      # 0.26 + 2 * 0.03
VIA1_PAD_HEIGHT = 52     # 0.26

# Width of the metal2 power straps that tie the rails of all rows.
STRAP_WIDTH = 52

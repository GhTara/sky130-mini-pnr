"""Draw a shared substrate tap ring. The box follows the strip centre lines in 5 nm units."""

STRIP = 34            # 0.17 um strip width
HALF = STRIP // 2
CORNER = 96           # 0.48 um without contacts at each corner (as the generator)


def strip_rects(x1, y1, x2, y2, horizontal):
    """One straight strip from (x1, y1) to (x2, y2) (outer extent)."""
    shapes = {"psubdiff": [(x1, y1, x2, y2)], "locali": [(x1, y1, x2, y2)],
              "psubdiffcont": []}
    if horizontal and x2 - x1 > 2 * CORNER + STRIP:
        shapes["psubdiffcont"].append((x1 + CORNER, y1, x2 - CORNER, y2))
    if not horizontal and y2 - y1 > 2 * CORNER + STRIP:
        shapes["psubdiffcont"].append((x1, y1 + CORNER, x2, y2 - CORNER))
    return shapes


def ring_rects(box):
    """{layer: [rect]} of a ring whose strips are centred on `box`."""
    x1, y1, x2, y2 = box
    outer = (x1 - HALF, y1 - HALF, x2 + HALF, y2 + HALF)
    sides = [
        strip_rects(outer[0], y1 - HALF, outer[2], y1 + HALF, True),      # bottom
        strip_rects(outer[0], y2 - HALF, outer[2], y2 + HALF, True),      # top
        strip_rects(x1 - HALF, outer[1], x1 + HALF, outer[3], False),     # left
        strip_rects(x2 - HALF, outer[1], x2 + HALF, outer[3], False),     # right
    ]
    shapes = {"psubdiff": [], "locali": [], "psubdiffcont": []}
    for side in sides:
        for layer, rects in side.items():
            shapes[layer].extend(rects)
    return shapes


def ring_li1_rects(box):
    """The li1 strips of the ring (where the router may drop an mcon)."""
    return ring_rects(box)["locali"]

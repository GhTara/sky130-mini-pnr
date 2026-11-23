"""Read library cell sizes, pin shapes and routing obstacles from LEF files.

Coordinates are stored as (x1, y1, x2, y2) in integer Magic units.
"""

from dataclasses import dataclass, field

from .units import um_to_units

ROUTING_LAYERS = ("li1", "met1", "met2")


@dataclass
class Macro:
    name: str
    width: int = 0
    height: int = 0
    pins: dict = field(default_factory=dict)  # pin -> [(layer, rect)]
    obstructions: list = field(default_factory=list)  # [(layer, rect)]


def parse_lef(path, wanted_cells):
    """Read only the macros listed in wanted_cells."""
    macros = {}
    current = None
    shapes = None        # list that RECT lines are appended to
    layer = None
    in_pin = None

    with open(path) as handle:
        for raw_line in handle:
            words = raw_line.split()
            if not words:
                continue
            keyword = words[0]

            if keyword == "MACRO":
                name = words[1]
                current = Macro(name) if name in wanted_cells else None
                if current is not None:
                    macros[name] = current
                continue
            if current is None:
                continue

            if keyword == "END" and len(words) > 1 and words[1] == current.name:
                current = None
            elif keyword == "SIZE":
                current.width = um_to_units(float(words[1]))
                current.height = um_to_units(float(words[3]))
            elif keyword == "PIN":
                in_pin = words[1]
                shapes = current.pins.setdefault(in_pin, [])
            elif keyword == "END" and len(words) > 1 and words[1] == in_pin:
                in_pin = None
                shapes = None
            elif keyword == "OBS":
                shapes = current.obstructions
            elif keyword == "LAYER":
                layer = words[1]
            elif keyword == "RECT" and shapes is not None:
                if layer in ROUTING_LAYERS:
                    rect = tuple(um_to_units(float(v)) for v in words[1:5])
                    shapes.append((layer, rect))

    missing = set(wanted_cells) - set(macros)
    if missing:
        raise ValueError(f"cells not found in LEF: {sorted(missing)}")
    return macros

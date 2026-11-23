"""Read Magic device outlines and pin shapes for placement and routing."""

from dataclasses import dataclass, field

from . import units as u
from .router import rects_overlap

# Magic contact types also contain the layers they connect.
LI1_TYPES = {"locali", "viali", "ndiffc", "pdiffc", "polycont", "psubdiffcont",
             "nsubdiffcont", "xpolycontact"}
MET1_TYPES = {"metal1", "viali", "via1"}


@dataclass
class DeviceTerminal:
    name: str
    layer: str                 # "met1" or "li1"
    rects: list                # shapes of the terminal on that layer


@dataclass
class DeviceLayout:
    cell: str
    bbox: tuple                # outline of all layers, cell coordinates
    terminals: dict = field(default_factory=dict)   # name -> DeviceTerminal
    met1_rects: list = field(default_factory=list)  # all metal1 of the cell
    mcon_rects: list = field(default_factory=list)  # all mcons of the cell


def read_mag(path):
    """Return ({layer: [rect]}, [(label layer, x, y, text)]) of a .mag file."""
    rects, labels = {}, []
    layer = None
    with open(path) as handle:
        lines = handle.read().splitlines()
    if "magscale 1 2" not in lines:
        raise ValueError(f"{path}: expected 'magscale 1 2' (5 nm units)")
    for line in lines:
        words = line.split()
        if not words:
            continue
        if words[0] == "<<":
            layer = words[1]
        elif words[0] == "rect" and layer not in ("checkpaint", "labels", "properties"):
            rects.setdefault(layer, []).append(tuple(int(v) for v in words[1:5]))
        elif words[0] == "rlabel":
            labels.append((words[1], int(words[2]), int(words[3]), words[-1]))
    return rects, labels


def connected_groups(rects):
    """Group rectangles that touch or overlap (simple union-find)."""
    parent = list(range(len(rects)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            touching = a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
            if touching:
                parent[find(i)] = find(j)
    groups = {}
    for i, rect in enumerate(rects):
        groups.setdefault(find(i), []).append(rect)
    return list(groups.values())


def merge_solid_groups(rects):
    """Merge touching metal tiles only when they fill their bounding box."""
    merged = []
    for group in connected_groups(rects):
        box = (min(r[0] for r in group), min(r[1] for r in group),
               max(r[2] for r in group), max(r[3] for r in group))
        tile_area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in group)
        if tile_area == (box[2] - box[0]) * (box[3] - box[1]):
            merged.append(box)
        else:
            merged.extend(group)
    return merged


def contains_point(rect, x, y):
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def load_device(path, cell):
    rects, labels = read_mag(path)
    all_rects = [r for layer_rects in rects.values() for r in layer_rects]
    bbox = (min(r[0] for r in all_rects), min(r[1] for r in all_rects),
            max(r[2] for r in all_rects), max(r[3] for r in all_rects))

    li1 = merge_solid_groups([r for layer in LI1_TYPES for r in rects.get(layer, [])])
    met1 = merge_solid_groups([r for layer in MET1_TYPES for r in rects.get(layer, [])])
    li1_groups = connected_groups(li1)
    met1_groups = connected_groups(met1)
    mcons = rects.get("viali", [])

    device = DeviceLayout(cell, bbox, met1_rects=met1, mcon_rects=mcons)
    for _, x, y, name in labels:
        li1_group = next((g for g in li1_groups if any(contains_point(r, x, y) for r in g)), None)
        if li1_group is None:
            raise ValueError(f"{cell}: label {name} is not on li1")
        # metal1 groups joined to this li1 group by an mcon
        met1_shapes = []
        for group in met1_groups:
            for mcon in mcons:
                on_li1 = any(rects_overlap(mcon, r) for r in li1_group)
                if on_li1 and any(rects_overlap(mcon, r) for r in group):
                    met1_shapes.extend(group)
                    break
        if met1_shapes:
            terminal = DeviceTerminal(name, "met1", met1_shapes)
        else:
            terminal = DeviceTerminal(name, "li1", li1_group)
        if name in device.terminals:
            device.terminals[name].rects.extend(terminal.rects)
        else:
            device.terminals[name] = terminal
    return device


def shift(rect, dx, dy):
    return (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)


def gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return max(dx, dy)


def overlap_size(a, b):
    """Smaller side of the overlap of two rectangles (0 if they do not overlap)."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, min(width, height))


def centered(x, y, width, height):
    return (x - width // 2, y - height // 2, x + width // 2, y + height // 2)


def li1_stub(own, cut):
    """Join an off-grid guard ring to a nearby contact. The strip must meet the
    0.0561 µm² minimum area (li.6) without counting metal inside the device.
    """
    min_length = 66
    ring_left = min(r[0] for r in own)
    ring_right = max(r[2] for r in own)
    for r in own:
        if not (r[1] <= cut[1] and cut[3] <= r[3]):
            continue
        if r[0] == ring_left and cut[2] <= r[0] and r[0] - cut[2] <= u.TRACK_X_PITCH:
            return (min(cut[0], r[2] - min_length), cut[1], r[2], cut[3])
        if r[2] == ring_right and cut[0] >= r[2] and cut[0] - r[2] <= u.TRACK_X_PITCH:
            return (r[0], cut[1], max(cut[2], r[0] + min_length), cut[3])
    return None


@dataclass
class Access:
    """Pin access at a grid point. A stub reaches an off-grid pin. shadow_x marks
    the adjacent column requiring clearance.
    """
    x: int
    y: int
    stub: tuple = None
    shadow_x: int = None
    stub_layer: str = "metal1"


def access_points(device, name, origin_x, origin_y, x_range, y_range):
    """Return grid access points with clearance for the pad, contact and any stub."""
    terminal = device.terminals[name]
    own = [shift(r, origin_x, origin_y) for r in terminal.rects]
    others = [shift(r, origin_x, origin_y) for r in device.met1_rects
              if terminal.layer == "li1" or r not in terminal.rects]
    other_mcons = [shift(r, origin_x, origin_y) for r in device.mcon_rects]
    half_wire = u.WIRE_WIDTH // 2

    def clear(shapes):
        return all(gap(s, r) >= u.MIN_SPACING for s in shapes for r in others)

    options = []
    for x in x_range:
        for y in y_range:
            pad = centered(x, y, u.VIA1_PAD_WIDTH, u.VIA1_PAD_HEIGHT)
            if terminal.layer == "li1":
                cut = centered(x, y, u.MCON_SIZE, u.MCON_SIZE)
                # Two contacts need 38 units (0.19 µm) of clearance under mcon.2.
                if any(gap(cut, m) < 38 for m in other_mcons) or not clear([pad]):
                    continue
                inside = any(r[0] <= cut[0] and cut[2] <= r[2] and r[1] <= cut[1]
                             and cut[3] <= r[3] for r in own)
                if inside:
                    options.append(Access(x, y))
                    continue
                stub = li1_stub(own, cut)
                if stub is not None:
                    step = u.TRACK_X_PITCH if stub[0] < x - u.MCON_SIZE // 2 else -u.TRACK_X_PITCH
                    options.append(Access(x, y, stub, x + step, "locali"))
                continue

            if any(overlap_size(pad, r) >= u.WIRE_WIDTH for r in own):
                if clear([pad]):
                    options.append(Access(x, y))
                continue
            for r in own:
                inside_height = r[1] <= y - half_wire and y + half_wire <= r[3]
                centre_x = (r[0] + r[2]) // 2
                close = abs(centre_x - x) <= u.TRACK_X_PITCH
                if not (inside_height and close and r[2] - r[0] >= 2 * u.WIRE_WIDTH):
                    continue
                stub = (min(x, centre_x), y - half_wire, max(x, centre_x), y + half_wire)
                if clear([pad, stub]):
                    step = u.TRACK_X_PITCH if centre_x > x else -u.TRACK_X_PITCH
                    options.append(Access(x, y, stub, x + step))
                    break
    return options

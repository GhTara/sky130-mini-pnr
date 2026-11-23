"""Build the reference circuit for resistor-load NMOS gates. Device bodies connect to VGND."""

from dataclasses import dataclass, field

# Segment lengths target a 5 µm resistor with SKY130's Magic generator.
# The extracted length includes the bends.
RESISTOR_MODEL = "sky130_fd_pr__res_xhigh_po_0p35"
RESISTOR_LENGTH_UM = 5.0
# A single 0.35 µm bar violates the minimum implant width (rpm.1).
RESISTOR_FOLDS = {2: 1.745, 3: 1.18, 4: 0.765, 5: 0.635}   # nx -> segment [um]
DEFAULT_FOLDS = 2


def resistor_kind(nx):
    """Device type name of the load resistor folded nx times ("rload" is nx = 2)."""
    return "rload" if nx == DEFAULT_FOLDS else f"rload_nx{nx}"


def is_resistor(kind):
    return kind.startswith("rload")


# Magic uses "generator" to draw each shape. "spice" supplies the device
# size for the reference circuit. botc=0 keeps only the top gate contact.
DEVICE_TYPES = {
    "nfet": {"model": "sky130_fd_pr__nfet_01v8", "spice_ports": ["D", "G", "S", "B"],
             "generator": {"w": 1.0, "l": 0.15, "botc": 0},
             "spice": {"w": 1.0, "l": 0.15}},
}
for _nx, _segment in RESISTOR_FOLDS.items():
    DEVICE_TYPES[resistor_kind(_nx)] = {
        "model": RESISTOR_MODEL, "spice_ports": ["R1", "R2", "B"], "folds": _nx,
        "generator": {"l": _segment, "nx": _nx, "snake": 1},
        "spice": {"l": RESISTOR_LENGTH_UM}}


@dataclass
class Device:
    name: str
    kind: str                                   # key of DEVICE_TYPES
    nets: dict = field(default_factory=dict)    # terminal -> net


def expand_gate(gate):
    """Devices of one gate. Internal series nodes are named <gate>_x1, ..."""
    out = gate.output
    devices = [Device(f"{gate.name}_r", "rload", {"R1": "VPWR", "R2": out, "B": "VGND"})]
    if gate.kind in ("NOT", "NAND"):
        top = out
        for k, net in enumerate(gate.inputs, start=1):
            last = k == len(gate.inputs)
            bottom = "VGND" if last else f"{gate.name}_x{k}"
            devices.append(Device(f"{gate.name}_m{k}", "nfet",
                                  {"D": top, "G": net, "S": bottom, "B": "VGND"}))
            top = bottom
    elif gate.kind == "NOR":
        for k, net in enumerate(gate.inputs, start=1):
            devices.append(Device(f"{gate.name}_m{k}", "nfet",
                                  {"D": out, "G": net, "S": "VGND", "B": "VGND"}))
    else:
        raise ValueError(f"{gate.kind} is not supported in resistor-load logic")
    return devices


def expand_circuit(circuit):
    devices = []
    for gate in circuit.gates:
        devices.extend(expand_gate(gate))
    return devices


def write_reference_spice(path, circuit, devices, top):
    """Reference netlist for LVS, built from the gate netlist only."""
    ports = circuit.inputs + circuit.outputs + ["VPWR", "VGND"]
    lines = [f"* device-level reference netlist of {circuit.name} (resistor-load NMOS)",
             f".subckt {top} " + " ".join(ports)]
    for device in devices:
        info = DEVICE_TYPES[device.kind]
        nets = " ".join(device.nets[t] for t in info["spice_ports"])
        params = " ".join(f"{k}={v}" for k, v in info["spice"].items())
        lines.append(f"X{device.name} {nets} {info['model']} {params}")
    lines += [".ends", ".end"]
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")

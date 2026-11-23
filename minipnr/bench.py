"""Read .bench netlists and map their gates to SKY130 standard cells."""

from dataclasses import dataclass, field


# gate type -> (cell name, input pin names, output pin name)
CELL_MAP = {
    "NOT": ("sky130_fd_sc_hd__inv_1", ["A"], "Y"),
    "BUFF": ("sky130_fd_sc_hd__buf_1", ["A"], "X"),
    "NAND": ("sky130_fd_sc_hd__nand2_1", ["A", "B"], "Y"),
    "NOR": ("sky130_fd_sc_hd__nor2_1", ["A", "B"], "Y"),
    "AND": ("sky130_fd_sc_hd__and2_1", ["A", "B"], "X"),
    "OR": ("sky130_fd_sc_hd__or2_1", ["A", "B"], "X"),
    "XOR": ("sky130_fd_sc_hd__xor2_1", ["A", "B"], "X"),
}


@dataclass
class Gate:
    name: str            # instance name, e.g. "g10"
    kind: str            # NAND, NOR, ...
    inputs: list         # input net names (in pin order A, B)
    output: str          # output net name
    cell: str = ""
    pin_nets: dict = field(default_factory=dict)   # pin name -> net


@dataclass
class Circuit:
    name: str
    inputs: list
    outputs: list
    gates: list

    def nets(self):
        """Return {net: [(instance or 'PIN', pin name), ...]}."""
        nets = {}
        for net in self.inputs + self.outputs:
            nets.setdefault(net, []).append(("PIN", net))
        for gate in self.gates:
            for pin, net in gate.pin_nets.items():
                nets.setdefault(net, []).append((gate.name, pin))
        return nets


def net_name(bench_name):
    """Prefix every signal name with n so numeric names such as 10 are valid."""
    return "n" + bench_name


def parse_bench(text, name):
    inputs, outputs, gates = [], [], []
    for raw_line in text.splitlines():
        line = raw_line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("INPUT(") or line.startswith("OUTPUT("):
            signal = line[line.index("(") + 1:line.index(")")].strip()
            if line.startswith("INPUT("):
                inputs.append(net_name(signal))
            else:
                outputs.append(net_name(signal))
            continue
        left, right = line.split("=")
        kind = right[:right.index("(")].strip().upper()
        args = right[right.index("(") + 1:right.rindex(")")].split(",")
        gate_inputs = [net_name(arg.strip()) for arg in args]
        output = net_name(left.strip())
        gates.append(Gate("g" + left.strip(), kind, gate_inputs, output))

    for gate in gates:
        map_gate_to_cell(gate)
    return Circuit(name, inputs, outputs, gates)


def map_gate_to_cell(gate):
    if gate.kind not in CELL_MAP:
        raise ValueError(f"gate type {gate.kind} is not supported")
    cell, input_pins, output_pin = CELL_MAP[gate.kind]
    if len(gate.inputs) != len(input_pins):
        raise ValueError(
            f"{gate.name}: {gate.kind} needs {len(input_pins)} inputs, "
            f"got {len(gate.inputs)}")
    gate.cell = cell
    gate.pin_nets = dict(zip(input_pins, gate.inputs))
    gate.pin_nets[output_pin] = gate.output


def gate_function(kind, values):
    if kind == "NOT":
        return 1 - values[0]
    if kind == "BUFF":
        return values[0]
    if kind == "NAND":
        return 1 - (values[0] & values[1])
    if kind == "NOR":
        return 1 - (values[0] | values[1])
    if kind == "AND":
        return values[0] & values[1]
    if kind == "OR":
        return values[0] | values[1]
    if kind == "XOR":
        return values[0] ^ values[1]
    raise ValueError(kind)


def simulate(circuit, input_values):
    """Logic simulation. input_values: {input net: 0/1}. Returns all net values."""
    values = dict(input_values)
    pending = list(circuit.gates)
    while pending:
        progress = False
        for gate in list(pending):
            if all(net in values for net in gate.inputs):
                gate_inputs = [values[net] for net in gate.inputs]
                values[gate.output] = gate_function(gate.kind, gate_inputs)
                pending.remove(gate)
                progress = True
        if not progress:
            raise ValueError("combinational loop or undriven net")
    return values

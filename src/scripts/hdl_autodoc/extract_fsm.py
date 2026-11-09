#!/usr/bin/env python3
"""
extract_fsm.py
--------------
Parses VHDL or SystemVerilog source, extracts the FSM next-state block,
and writes <module>.dot and <module>.rst into the output directory.

Usage:
    python scripts/extract_fsm.py <file.vhd|file.sv> <module_name> <output_dir>
"""

import re
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# State colour palette
# ─────────────────────────────────────────────────────────────────────────────

STATE_COLOURS = {
    "red":       "salmon",
    "red_amber": "lightyellow",
    "green":     "lightgreen",
    "amber":     "orange",
    "idle":      "lightblue",
    "counting":  "lightyellow",
    "done":      "lightgreen",
    "on_state":  "lightgreen",
    "fade_state":"lightyellow",
    "off_state": "lightgray",
}
DEFAULT_COLOUR = "white"


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

from hdl_scan import matching_paren
from hdl_text import strip_sv_comments, strip_vhdl_comments  # noqa: F401  (re-exported)

def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


# ─────────────────────────────────────────────────────────────────────────────
# VHDL extraction
# ─────────────────────────────────────────────────────────────────────────────

def _vhdl_fsm_case(text: str) -> tuple[str, str] | None:
    """(case body, state signal) of the first `case` whose arms assign one signal to the
    names of the arms themselves, whatever the selector and the signal are called, or None.
    `text` is normalised (comments stripped, lower case)."""
    for m in re.finditer(r"case\s+\w+\s+is\s+(.*?)\s+end\s+case", text, re.DOTALL):
        body = m.group(1)
        names = {a.group(1) for c in re.split(r"\bwhen\b", body)
                 if (a := re.match(r"\s*(\w+)\s*=>", c))} - {"others"}
        for a in re.finditer(r"(\w+)\s*<=\s*(\w+)\s*;", body):
            if a.group(2) in names:
                return body, a.group(1)
    return None


def _vhdl_arm_transitions(from_state: str, clause: str, sig: str) -> list[tuple[str, str, str]]:
    """The transitions one case arm makes.  The condition is the enclosing `if`/`elsif`
    conditions joined by `and`; an `else` branch is `not (...)` of the branches before it."""
    found: list[tuple[str, str, str]] = []
    stack: list[dict] = []                       # one frame per open `if`
    token = re.compile(
        r"\bif\s+(?P<if>(?:(?!\bthen\b).)+?)\s+then\b|\belsif\s+(?P<elsif>(?:(?!\bthen\b).)+?)\s+then\b"
        rf"|\b(?P<else>else)\b|\b(?P<end>end\s+if)\b|\b{sig}\s*<=\s*(?P<to>\w+)", re.DOTALL)
    for m in token.finditer(clause):
        if m["if"]:
            stack.append({"prior": [], "cur": m["if"].strip()})
        elif m["elsif"] and stack:
            stack[-1]["prior"].append(stack[-1]["cur"])
            stack[-1]["cur"] = m["elsif"].strip()
        elif m["else"] and stack:
            f = stack[-1]
            f["cur"] = "not (" + " or ".join(f["prior"] + [f["cur"]]) + ")"
            f["prior"] = []
        elif m["end"] and stack:
            stack.pop()
        elif m["to"]:
            to, cond = m["to"].upper(), " and ".join(f["cur"] for f in stack)
            if cond or to != from_state:
                found.append((from_state, to, cond))
    return found


def extract_transitions_vhdl(vhd_text: str) -> list[tuple[str,str,str]]:
    found = _vhdl_fsm_case(normalise(strip_vhdl_comments(vhd_text)))
    if not found:
        return []  # No FSM in this file
    body, sig = found

    transitions = []
    for clause in re.split(r"\bwhen\b", body):
        clause = clause.strip()
        if not clause or clause.startswith("others"):
            continue
        state_m = re.match(r"(\w+)\s*=>", clause)
        if not state_m:
            continue
        from_state = state_m.group(1).upper()
        transitions += _vhdl_arm_transitions(from_state, clause, sig)
    return transitions


def extract_outputs_vhdl(vhd_text: str) -> dict:
    ASSIGN_RE = re.compile(r"(\w+)\s*<=\s*'([01])'")
    WHEN_RE   = re.compile(r"^\s*when\s+(\w+)\s*=>", re.IGNORECASE)
    found = _vhdl_fsm_case(normalise(strip_vhdl_comments(vhd_text)))
    state_assign = re.compile(rf"\b{found[1]}\s*<=", re.IGNORECASE) if found else None
    processes = re.findall(
        r":\s*process\b.*?end\s+process", vhd_text, re.IGNORECASE | re.DOTALL
    )
    for proc in processes:
        if state_assign and state_assign.search(proc):
            continue
        defaults = {}
        case_start = re.search(r"\bcase\b", proc, re.IGNORECASE)
        if case_start:
            for m in ASSIGN_RE.finditer(proc[:case_start.start()]):
                defaults[m.group(1)] = m.group(2)
        if not defaults:
            continue
        state_outputs = {}
        current = None
        for line in proc.splitlines():
            wm = WHEN_RE.match(line)
            if wm:
                current = wm.group(1).upper()
                state_outputs[current] = dict(defaults)
                for m in ASSIGN_RE.finditer(line[wm.end():]):
                    state_outputs[current][m.group(1)] = m.group(2)
                continue
            if current:
                for m in ASSIGN_RE.finditer(line):
                    state_outputs[current][m.group(1)] = m.group(2)
        if state_outputs:
            return state_outputs
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# SystemVerilog extraction
# ─────────────────────────────────────────────────────────────────────────────

_SV_DEPTH_TOKENS = re.compile(r"\bbegin\b|\bend\b|\bcase[xz]?\b|\bendcase\b|[()]", re.IGNORECASE)
_SV_ARM_LABEL = re.compile(r"(?<![\w:])(\w+(?:\s*,\s*\w+)*)\s*:(?!:)")


def _sv_case_body(text: str) -> tuple[str, str] | None:
    """(body, state signal) of the first `case` whose arms assign one signal to the names of
    the arms themselves, whatever the selector and the signal are called (nested cases and
    parentheses in the selector are handled), or None."""
    for m in re.finditer(r"\bcase[xz]?\s*\(", text, re.IGNORECASE):
        close = matching_paren(text, m.end() - 1)
        if close is None:
            continue
        depth = 1
        for t in re.finditer(r"\bcase[xz]?\b|\bendcase\b", text[close + 1:], re.IGNORECASE):
            depth += 1 if t.group(0).lower() != "endcase" else -1
            if depth == 0:
                body = text[close + 1:close + 1 + t.start()]
                names = {n.upper() for labels, _ in _sv_arms(body)
                         for n in re.split(r"\s*,\s*", labels)} - {"DEFAULT"}
                for a in re.finditer(r"(\w+)\s*(?:<=|=)(?!=)\s*([^;]+);", body):
                    value = a.group(2).strip()          # a name, or `cond ? NAME : NAME`
                    tern = re.fullmatch(r".+?\?\s*(\w+)\s*:\s*(\w+)", value, re.DOTALL)
                    if {w.upper() for w in (tern.groups() if tern else [value])} & names:
                        return body, a.group(1)
                break
    return None


def _sv_arms(body: str) -> list[tuple[str, str]]:
    """[(label, arm text)] for the arms of a case body.  A label is `NAME:` at the start
    of a statement outside any begin/end, nested case or parentheses, so the colon of a
    ternary (`a ? B : C`) is not one."""
    depth_at: list[tuple[int, int]] = []          # (position, depth after the token)
    depth = 0
    for t in _SV_DEPTH_TOKENS.finditer(body):
        word = t.group(0).lower()
        depth += -1 if word in ("end", "endcase", ")") else 1
        depth_at.append((t.start(), depth))

    def depth_before(pos: int) -> int:
        d = 0
        for p, after in depth_at:
            if p >= pos:
                break
            d = after
        return d

    starts = []
    for m in _SV_ARM_LABEL.finditer(body):
        before = body[:m.start()].rstrip()
        at_statement_start = (not before or before.endswith(";")
                              or re.search(r"\bend$", before, re.IGNORECASE) is not None)
        if at_statement_start and depth_before(m.start()) == 0:
            starts.append(m)
    return [(m.group(1), body[m.end():starts[i + 1].start() if i + 1 < len(starts) else len(body)])
            for i, m in enumerate(starts)]


def _sv_arm_transitions(from_state: str, arm: str, sig: str) -> list[tuple[str, str, str]]:
    """The transitions one case arm makes: `if (cond) state = X`, a ternary
    assignment, and bare assignments."""
    found: list[tuple[str, str, str]] = []
    consumed: list[tuple[int, int]] = []
    assign = re.compile(rf"\b{sig}\s*(?:<=|=)\s*([^;]+);", re.IGNORECASE)

    for m in re.finditer(r"\bif\s*\(", arm, re.IGNORECASE):
        close = matching_paren(arm, m.end() - 1)
        if close is None:
            continue
        cond = arm[m.end():close].strip()
        rest = re.match(r"\s*(?:begin\b\s*)?", arm[close + 1:], re.IGNORECASE)
        a = assign.match(arm, close + 1 + rest.end())
        if a:
            found.append((from_state, a.group(1).strip().upper(), cond))
            consumed.append((m.start(), a.end()))

    for m in assign.finditer(arm):
        if any(s <= m.start() < e for s, e in consumed):
            continue
        value = m.group(1).strip()
        tern = re.fullmatch(r"(.+?)\s*\?\s*(\w+)\s*:\s*(\w+)", value, re.DOTALL)
        if tern:
            cond, yes, no = tern.group(1).strip(), tern.group(2).upper(), tern.group(3).upper()
            if yes != from_state:
                found.append((from_state, yes, cond))
            if no != from_state:
                found.append((from_state, no, f"!({cond})"))
        elif re.fullmatch(r"\w+", value) and value.upper() != from_state:
            found.append((from_state, value.upper(), ""))
    return found


def extract_transitions_sv(sv_text: str) -> list[tuple[str,str,str]]:
    # Work on comment-stripped text only — do NOT normalise (normalise lowercases
    # state names which breaks the label splitter)
    found = _sv_case_body(strip_sv_comments(sv_text))
    if found is None:
        return []  # No FSM in this file
    body, sig = found

    transitions: list[tuple[str, str, str]] = []
    for labels, arm in _sv_arms(body):
        for label in re.split(r"\s*,\s*", labels):
            if label.lower() == "default":
                continue
            transitions += _sv_arm_transitions(label.upper(), arm, sig)
    return transitions


def extract_outputs_sv(sv_text: str) -> dict:
    """Extract Moore outputs from always_comb output decode block."""
    ASSIGN_RE = re.compile(r"(\w+)\s*=\s*1'b([01])")
    CASE_RE   = re.compile(r"case\s*\(\s*\w+\s*\)(.*?)endcase", re.DOTALL | re.IGNORECASE)
    WHEN_RE   = re.compile(r"([A-Z_][A-Z0-9_]*)\s*:", re.IGNORECASE)

    found = _sv_case_body(strip_sv_comments(sv_text))
    state_assign = re.compile(rf"\b{found[1]}\s*(?:<=|=)", re.IGNORECASE) if found else None
    blocks = re.findall(
        r"always_comb\b.*?(?=always_|endmodule)", sv_text, re.DOTALL | re.IGNORECASE
    )
    for block in blocks:
        if state_assign and state_assign.search(block):
            continue
        case_m = CASE_RE.search(block)
        if not case_m:
            continue

        # Collect defaults before case
        defaults = {}
        pre = block[:case_m.start()]
        for m in ASSIGN_RE.finditer(pre):
            defaults[m.group(1)] = m.group(2)
        if not defaults:
            continue

        state_outputs = {}
        current = None
        for line in case_m.group(1).splitlines():
            wm = WHEN_RE.match(line.strip())
            if wm:
                current = wm.group(1).upper()
                state_outputs[current] = dict(defaults)
                for m in ASSIGN_RE.finditer(line[wm.end():]):
                    state_outputs[current][m.group(1)] = m.group(2)
                continue
            if current:
                for m in ASSIGN_RE.finditer(line):
                    state_outputs[current][m.group(1)] = m.group(2)

        if state_outputs:
            return state_outputs
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Shared dot + RST writers
# ─────────────────────────────────────────────────────────────────────────────

def collect_states(transitions):
    states = []
    for frm, to, _ in transitions:
        for s in (frm, to):
            if s not in states:
                states.append(s)
    return states


def write_dot(transitions, states, module_name):
    lines = [
        f"digraph {module_name} {{",
        "    rankdir=LR;",
        "    node [shape=circle, style=filled, fontname=Helvetica, fontsize=12];",
        "    edge [fontname=Helvetica, fontsize=10];",
        "",
        "    __start [shape=point, width=0.2, fillcolor=black];",
        "",
    ]
    for s in states:
        colour = STATE_COLOURS.get(s.lower(), DEFAULT_COLOUR)
        lines.append(f"    {s} [fillcolor={colour}];")
    lines.append("")
    if states:
        lines.append(f"    __start -> {states[0]} [label=\"reset\"];")
    lines.append("")
    for frm, to, cond in transitions:
        label = (cond if cond else "always").replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f"    {frm} -> {to} [label=\"{label}\"];")
    lines.append("}")
    return "\n".join(lines)


def write_rst(dot_filename, states, outputs, transitions, module_name, src_filename):
    title = f"{module_name} State Machine"
    out = [title, "=" * len(title), "",
           f"Auto-extracted from ``{src_filename}``.", "",
           "State Transition Diagram", "------------------------", "",
           f".. graphviz:: {dot_filename}", ""]

    if outputs:
        all_sigs = []
        for sig_map in outputs.values():
            for sig in sig_map:
                if sig not in all_sigs:
                    all_sigs.append(sig)
        out += ["State Output Table", "------------------", "",
                ".. list-table::", "   :header-rows: 1", "",
                "   * - State"]
        for sig in all_sigs:
            out.append(f"     - ``{sig}``")
        out.append("")
        for state in states:
            sig_map = outputs.get(state, {})
            out.append(f"   * - ``{state}``")
            for sig in all_sigs:
                out.append(f"     - {sig_map.get(sig, '—')}")
            out.append("")
        out.append("")

    out += ["Transitions", "-----------", "",
            ".. list-table::", "   :header-rows: 1", "",
            "   * - From", "     - To", "     - Condition", ""]
    for frm, to, cond in transitions:
        out.append(f"   * - ``{frm}``")
        out.append(f"     - ``{to}``")
        out.append(f"     - ``{cond}``" if cond else "     - (always)")
        out.append("")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("Usage: extract_fsm.py <file.vhd|file.sv> <module_name> <output_dir>")

    src_path    = Path(sys.argv[1])
    module_name = sys.argv[2]
    output_dir  = Path(sys.argv[3])
    output_dir.mkdir(parents=True, exist_ok=True)

    src_text = src_path.read_text()
    ext      = src_path.suffix.lower()

    if ext in (".vhd", ".vhdl"):
        transitions = extract_transitions_vhdl(src_text)
        outputs     = extract_outputs_vhdl(src_text)
    elif ext in (".sv", ".svh"):
        transitions = extract_transitions_sv(src_text)
        outputs     = extract_outputs_sv(src_text)
    else:
        sys.exit(f"ERROR: Unsupported file type '{ext}'. Use .vhd or .sv")

    if not transitions:
        print(f"  (no FSM found in {src_path.name} — skipping)")
        sys.exit(0)

    states       = collect_states(transitions)
    dot_filename = f"{module_name}.dot"

    (output_dir / dot_filename).write_text(
        write_dot(transitions, states, module_name)
    )
    print(f"  → {output_dir / dot_filename}")

    (output_dir / f"{module_name}.rst").write_text(
        write_rst(dot_filename, states, outputs, transitions, module_name, src_path.name)
    )
    print(f"  → {output_dir / f'{module_name}.rst'}")

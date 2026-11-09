"""
autodoc_json.py
---------------
One JSON document for one VHDL or SystemVerilog source file: the module, its generics
and ports, instances, processes (with their WaveDrom and Mermaid blocks), state machine,
and clock- and reset-domain analysis.  It calls the same extractors the site build calls,
so the VS Code extension's preview and the generated site cannot disagree.

    python autodoc_json.py --language vhdl|systemverilog [--support FILE ...]
                           [--bus-groups bus_groups.toml]  < source  > document.json

The source text is read from standard input (so an editor buffer that is not saved can
be documented).  Standard library only.  A section that cannot be extracted is left empty
and described in "warnings"; the exit status is 0 unless the command line is wrong.

Document shape:

    language   "vhdl" | "systemverilog"
    module     {name, line, description, wavedrom: [str], mermaid: [{caption, source}]} | null
    generics   [{name, type, default, description, line}]            (parameters for SV)
    ports      [{name, direction, type, range, description, line, kind, interface,
                 modport, group}]       kind "port" | "interface"; group = bus-group label
    block      {dot} | null
    instances  [{instance, component, kind, line}]                   kind "module" | "interface"
    processes  [{label, kind, line, sensitivity, description, wavedrom: [str], mermaid: [...]}]
    fsm        {states, line, state_lines: {state: line}, transitions: [{from, to, condition, line}], dot} | null
    cdc        {domains: {clock: [process]}, crossings, dual_clock_instances, lines: {name: line}} | null
    reset      {processes, domains: {reset: [process]}, crossings, lines: {name: line}} | null
    warnings   [str]
"""

import argparse
import json
import re
import sys
from pathlib import Path

import extract_block
import extract_cdc
import extract_fsm
import extract_processes
import extract_reset
from detect_buses import group_ports
from generate_rst import ENTITY_RE, MODULE_RE
from hdl_scan import scan_sv_instances, scan_vhdl_instances
from hdl_text import blank_sv_comments, blank_vhdl_comments, safe_id, split_lines
from sv_interfaces import collect_interfaces

LANGUAGES = ("vhdl", "systemverilog")


def _text_parts(tokens: list[dict]) -> tuple[str, list[str], list[dict]]:
    """(description, wavedrom sources, mermaid diagrams) from comment tokens."""
    description = "\n\n".join("\n".join(t["lines"]) for t in tokens if t["type"] == "text")
    wavedrom = ["\n".join(t["lines"]) for t in tokens if t["type"] == "wavedrom"]
    mermaid = []
    for t in tokens:
        if t["type"] != "mermaid":
            continue
        body, caption = list(t["lines"]), None
        while body and re.match(r"^\s*:\w[\w-]*:", body[0]):
            option = body.pop(0)
            m = re.match(r"^\s*:caption:\s*(.*)$", option)
            if m:
                caption = m.group(1).strip()
        mermaid.append({"caption": caption, "source": "\n".join(body).strip("\n")})
    return description, wavedrom, mermaid


def _module(text: str, blanked: str, lines: list[str], vhdl: bool, warnings: list[str]):
    m = (ENTITY_RE if vhdl else MODULE_RE).search(blanked)
    if not m:
        warnings.append("No entity found." if vhdl else "No module found.")
        return None
    line = blanked.count("\n", 0, m.start(1)) + 1
    prefix = "--" if vhdl else "//"
    _, wavedrom, mermaid = _text_parts(extract_processes.extract_comment_tokens(lines, line - 1, prefix))
    return {"name": m.group(1), "line": line, "description": _header_text(lines, line - 1, prefix),
            "wavedrom": wavedrom, "mermaid": mermaid}


def _header_text(lines: list[str], idx: int, prefix: str) -> str:
    """The comment block directly above line `idx` as prose: paragraph breaks kept, banner
    lines dropped, ending where the first reStructuredText directive (a diagram) begins."""
    block: list[str] = []
    i = idx - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith(prefix):
            block.insert(0, re.sub(rf"^{re.escape(prefix)}\s?", "", stripped).rstrip())
        elif stripped:
            break
        i -= 1
    prose: list[str] = []
    for text in block:
        if re.match(r"^\.\.\s+\w", text):
            break
        prose.append("" if text and not re.search(r"\w", text) else text)
    return "\n".join(prose).strip()


def _ports(text: str, vhdl: bool, interface_names: set[str], bus_groups: Path | None) -> list[dict]:
    raw = (extract_block.extract_ports_vhdl(text) if vhdl
           else extract_block.extract_ports_sv(text, interface_names or None))
    flat = [p for p in raw if p.get("kind") != "interface"]
    group_of: dict[str, str] = {}
    if flat:
        groups, _ = group_ports(flat, bus_groups)
        group_of = {p["name"]: g.bus_type for g in groups for p in g.ports}
    return [{"name": p["name"], "direction": p["dir"], "type": p["type"], "range": p.get("range", ""),
             "description": p["comment"], "line": p["line"], "kind": p.get("kind", "port"),
             "interface": p.get("interface"), "modport": p.get("modport"),
             "group": group_of.get(p["name"])} for p in raw]


def _generics(text: str, vhdl: bool) -> list[dict]:
    raw = extract_block.extract_generics_vhdl(text) if vhdl else extract_block.extract_params_sv(text)
    return [{"name": g["name"], "type": g["type"], "default": g["default"],
             "description": g["comment"], "line": g["line"]} for g in raw]


def _instances(text: str, blanked: str, vhdl: bool, interface_names: set[str]) -> list[dict]:
    def line_at(index: int) -> int:
        return blanked.count("\n", 0, index) + 1

    found = [{"instance": i["instance"], "component": i["component"], "kind": "module",
              "line": line_at(i["start"])}
             for i in (scan_vhdl_instances(blanked) if vhdl else scan_sv_instances(blanked))
             if i["component"] not in interface_names]       # listed below, as interfaces
    if not vhdl and interface_names:
        found += [{"instance": i["name"], "component": i["interface"], "kind": "interface",
                   "line": i["line"]}
                  for i in extract_block.extract_interface_instances_sv(text, interface_names)]
    return sorted(found, key=lambda i: i["line"])


def _processes(lines: list[str], vhdl: bool) -> list[dict]:
    found = (extract_processes.find_processes_vhdl(lines) if vhdl
             else extract_processes.find_processes_sv(lines))
    out = []
    for p in found:
        description, wavedrom, mermaid = _text_parts(p["tokens"])
        out.append({"label": p["label"], "kind": p["kind"], "line": p["line"],
                    "sensitivity": p["sensitivity"], "description": description,
                    "wavedrom": wavedrom, "mermaid": mermaid})
    return out


def _fsm(text: str, vhdl: bool, name: str) -> dict | None:
    transitions = (extract_fsm.extract_transitions_vhdl(text) if vhdl
                   else extract_fsm.extract_transitions_sv(text))
    if not transitions:
        return None
    states = extract_fsm.collect_states(transitions)
    where = extract_fsm.locate_fsm(text, vhdl)
    return {"states": states, "line": where["case"], "state_lines": where["states"],
            "transitions": [{"from": a, "to": b, "condition": c, "line": n}
                            for (a, b, c), n in zip(transitions, where["transitions"])],
            "dot": extract_fsm.write_dot(transitions, states, safe_id(name))}


def _block(text: str, vhdl: bool, name: str, interface_names: set[str]) -> dict | None:
    """The site's block diagram (Graphviz source) for the module, drawn by the panel."""
    if vhdl:
        ports, generics, instances = (extract_block.extract_ports_vhdl(text),
                                      extract_block.extract_generics_vhdl(text), [])
    else:
        ports = extract_block.extract_ports_sv(text, interface_names or None)
        generics = extract_block.extract_params_sv(text)
        instances = extract_block.extract_interface_instances_sv(text, interface_names)
    if not ports and not generics:
        return None
    return {"dot": extract_block.write_dot_block(name, ports, generics, instances)}


def _first_lines(blanked: str, names) -> dict[str, int]:
    """{name: line of its first mention outside comments}: normally the port or declaration,
    since a declaration comes before use.  Names that do not appear are left out."""
    found = {}
    for name in sorted(names):               # sorted: the same file always gives the same JSON
        m = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", blanked)
        if m:
            found[name] = blanked.count("\n", 0, m.start()) + 1
    return found


def _cdc(text: str, blanked: str, vhdl: bool) -> dict | None:
    analysis = extract_cdc.analyse_cdc(text, vhdl)
    if not analysis["domains"]:
        return None
    names = set(analysis["domains"]) | {x["signal"] for x in analysis["crossings"]}
    return {**analysis, "lines": _first_lines(blanked, names)}


def _reset(text: str, blanked: str, vhdl: bool) -> dict | None:
    analysis = extract_reset.analyse_reset(text, vhdl)
    if not analysis["processes"]:
        return None
    names = ({p["clock"] for p in analysis["processes"]} | {p["reset"] for p in analysis["processes"]}
             | {x["signal"] for x in analysis["crossings"]})
    return {"processes": [{"label": p["label"], "clock": p["clock"], "reset": p["reset"],
                           "style": p["style"]} for p in analysis["processes"]],
            "domains": {key: [p["label"] for p in procs] for key, procs in analysis["domains"].items()},
            "crossings": analysis["crossings"],
            "lines": _first_lines(blanked, {n for n in names if n})}


def build_document(text: str, language: str, support_files=(), bus_groups: Path | None = None) -> dict:
    """The document for one source file (see the module docstring)."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}, not {language!r}")
    vhdl = language == "vhdl"
    warnings: list[str] = []
    blanked = blank_vhdl_comments(text) if vhdl else blank_sv_comments(text)
    lines = split_lines(text)

    def section(name: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # a broken section must not take the others with it
            warnings.append(f"{name}: {type(exc).__name__}: {exc}")
            return default

    interface_names: set[str] = set()
    if not vhdl and support_files:
        interface_names = set(section("interfaces", lambda: collect_interfaces(list(support_files)), {}))

    module = section("module", lambda: _module(text, blanked, lines, vhdl, warnings), None)
    return {
        "language": language,
        "module": module,
        "generics": section("generics", lambda: _generics(text, vhdl), []),
        "ports": section("ports", lambda: _ports(text, vhdl, interface_names, bus_groups), []),
        "block": section("block", lambda: _block(text, vhdl, module["name"], interface_names) if module else None, None),
        "instances": section("instances", lambda: _instances(text, blanked, vhdl, interface_names), []),
        "processes": section("processes", lambda: _processes(lines, vhdl), []),
        "fsm": section("fsm", lambda: _fsm(text, vhdl, module["name"] if module else "fsm"), None),
        "cdc": section("cdc", lambda: _cdc(text, blanked, vhdl), None),
        "reset": section("reset", lambda: _reset(text, blanked, vhdl), None),
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSON document for one HDL source file (read from stdin).")
    parser.add_argument("--language", required=True, choices=LANGUAGES)
    parser.add_argument("--support", nargs="*", default=[], metavar="FILE",
                        help="SystemVerilog files that define interfaces used by the module")
    parser.add_argument("--bus-groups", metavar="FILE", help="bus_groups.toml with custom port groups")
    args = parser.parse_args(argv)

    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    bus_groups = Path(args.bus_groups) if args.bus_groups else None
    document = build_document(text, args.language, [Path(f) for f in args.support], bus_groups)
    sys.stdout.write(json.dumps(document) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

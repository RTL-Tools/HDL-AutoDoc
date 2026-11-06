"""
doc_stats.py
------------
How well a design is documented: per module, how many ports, generics and processes carry a
description or a diagram, how much of the file is comment, and how many registers it has.
Built on autodoc_json.build_document, so the numbers describe exactly what the site shows.

A port or generic counts as described when its comment is not empty (length is not judged).
Standard library only.
"""

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from autodoc_json import build_document
from hdl_text import blank_sv_comments, blank_vhdl_comments, split_lines


@dataclass
class ModuleStats:
    name: str
    ports: int = 0
    ports_described: int = 0
    generics: int = 0
    generics_described: int = 0
    processes: int = 0
    wavedrom: int = 0              # processes with at least one WaveDrom diagram
    mermaid: int = 0               # processes with at least one Mermaid diagram
    has_header: bool = False
    comment_lines: int = 0
    code_lines: int = 0
    registers: int = 0

    @property
    def comment_ratio(self) -> float | None:
        """Comment lines per code line; None for a file with no code."""
        return self.comment_lines / self.code_lines if self.code_lines else None

    def as_dict(self) -> dict:
        return {**asdict(self), "comment_ratio": self.comment_ratio}


def _described(items: list[dict]) -> int:
    return sum(1 for i in items if (i.get("description") or "").strip())


def _line_counts(text: str, vhdl: bool) -> tuple[int, int]:
    """(comment-only lines, lines with code); blank lines count as neither."""
    blanked = blank_vhdl_comments(text) if vhdl else blank_sv_comments(text)
    comment = code = 0
    for original, bare in zip(split_lines(text), split_lines(blanked)):
        if bare.strip():
            code += 1
        elif original.strip():
            comment += 1
    return comment, code


def _register_count(name: str, registers_dir: Path) -> int:
    toml = Path(registers_dir) / f"regs_{name}.toml"
    try:
        data = tomllib.loads(toml.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return 0
    return sum(1 for v in data.values() if isinstance(v, dict) and "mode" in v)


def module_stats(name: str, file: str, support_files, registers_dir, bus_groups=None) -> ModuleStats:
    """Statistics for one module; a file that cannot be read gives an empty result."""
    stats = ModuleStats(name=name, registers=_register_count(name, registers_dir))
    path = Path(file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return stats
    vhdl = path.suffix.lower() in (".vhd", ".vhdl")
    doc = build_document(text, "vhdl" if vhdl else "systemverilog", support_files, bus_groups)
    stats.ports, stats.ports_described = len(doc["ports"]), _described(doc["ports"])
    stats.generics, stats.generics_described = len(doc["generics"]), _described(doc["generics"])
    stats.processes = len(doc["processes"])
    stats.wavedrom = sum(1 for p in doc["processes"] if p["wavedrom"])
    stats.mermaid = sum(1 for p in doc["processes"] if p["mermaid"])
    stats.has_header = bool(doc["module"] and (doc["module"]["description"] or "").strip())
    stats.comment_lines, stats.code_lines = _line_counts(text, vhdl)
    return stats


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def summarise(stats: list[ModuleStats]) -> dict:
    """Project totals, with percentages (None where there is nothing to divide)."""
    total = lambda attr: sum(getattr(s, attr) for s in stats)  # noqa: E731
    headers = sum(1 for s in stats if s.has_header)
    return {
        "modules": len(stats),
        "ports": total("ports"), "ports_described": total("ports_described"),
        "ports_percent": _percent(total("ports_described"), total("ports")),
        "generics": total("generics"), "generics_described": total("generics_described"),
        "generics_percent": _percent(total("generics_described"), total("generics")),
        "processes": total("processes"), "wavedrom": total("wavedrom"), "mermaid": total("mermaid"),
        "headers": headers, "headers_percent": _percent(headers, len(stats)),
        "comment_lines": total("comment_lines"), "code_lines": total("code_lines"),
        "registers": total("registers"),
    }

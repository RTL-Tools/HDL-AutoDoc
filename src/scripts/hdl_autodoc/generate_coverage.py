#!/usr/bin/env python3
"""
generate_coverage.py
--------------------
Inspects extraction output for each module and produces:
  1. A compact terminal coverage table (stdout)
  2. docs/hdl_autodoc/coverage.rst (Sphinx HTML page)

Usage:
    python scripts/hdl_autodoc/generate_coverage.py \\
        docs/hdl_autodoc/hierarchy.json docs/hdl_autodoc
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from doc_stats import ModuleStats, module_stats, summarise
from parse_testbenches import discover_testbenches


@dataclass
class CoverageResult:
    name: str
    fsm: bool
    process_count: int
    cdc: bool
    reset: bool
    port_count: int


_PORT_SECTION_RE = re.compile(r"^Ports\n[-]+", re.MULTILINE)
_DATA_ROW_RE = re.compile(r"^\s+\* - ``", re.MULTILINE)
_NEXT_SECTION_RE = re.compile(r"\n\n[^\n]+\n[-=]+")


def _count_ports(content: str) -> int:
    """Count port data rows in the Ports section of a _block.rst file."""
    m = _PORT_SECTION_RE.search(content)
    if not m:
        return 0
    section = content[m.end():]
    nxt = _NEXT_SECTION_RE.search(section)
    if nxt:
        section = section[:nxt.start()]
    return len(_DATA_ROW_RE.findall(section))


def detect_coverage(name: str, docs_dir: Path) -> CoverageResult:
    """Inspect a module's extraction output directory and return coverage signals."""
    mod_dir = docs_dir / "modules" / name

    fsm = (mod_dir / f"{name}.dot").exists()

    procs_dir = mod_dir / "processes"
    process_count = len(list(procs_dir.glob("p_*.rst"))) if procs_dir.exists() else 0

    cdc_rst = mod_dir / f"{name}_cdc.rst"
    cdc = cdc_rst.exists() and "Signal Crossings" in cdc_rst.read_text()

    reset_rst = mod_dir / f"{name}_reset.rst"
    reset = reset_rst.exists() and bool(
        re.search(r"crossing", reset_rst.read_text(), re.IGNORECASE)
    )

    block_rst = mod_dir / f"{name}_block.rst"
    port_count = _count_ports(block_rst.read_text()) if block_rst.exists() else 0

    return CoverageResult(
        name=name,
        fsm=fsm,
        process_count=process_count,
        cdc=cdc,
        reset=reset,
        port_count=port_count,
    )


def format_terminal_table(results: list[CoverageResult]) -> str:
    """Format a compact terminal coverage table from a list of results."""
    n = len(results)
    if not results:
        return "Coverage Report\nNo modules found.\n"

    name_w = max(len(r.name) for r in results)
    name_w = max(name_w, 6)  # minimum width of "Module" header

    # Column widths: FSM=5, Processes=11, CDC=5, Reset=7, Ports=9
    total_w = name_w + 2 + 5 + 2 + 11 + 2 + 5 + 2 + 7 + 2 + 9
    sep = "─" * total_w

    def fmt_bool(v: bool) -> str:
        return "✓" if v else "–"

    def fmt_proc(count: int) -> str:
        return f"{count} procs" if count > 0 else "–"

    def fmt_port(count: int) -> str:
        return f"{count} ports" if count > 0 else "–"

    def row(name: str, fsm: str, proc: str, cdc: str, rst: str, port: str) -> str:
        return (
            f"{name:<{name_w}}  {fsm:>5}  {proc:>11}  "
            f"{cdc:>5}  {rst:>7}  {port:>9}"
        )

    lines = [
        "Coverage Report",
        sep,
        row("Module", "FSM", "Processes", "CDC", "Reset", "Ports"),
        sep,
    ]

    for r in results:
        lines.append(row(
            r.name,
            fmt_bool(r.fsm),
            fmt_proc(r.process_count),
            fmt_bool(r.cdc),
            fmt_bool(r.reset),
            fmt_port(r.port_count),
        ))

    fsm_tot   = sum(1 for r in results if r.fsm)
    proc_tot  = sum(1 for r in results if r.process_count > 0)
    cdc_tot   = sum(1 for r in results if r.cdc)
    reset_tot = sum(1 for r in results if r.reset)
    port_tot  = sum(1 for r in results if r.port_count > 0)

    lines.append(sep)
    lines.append(row(
        "Totals",
        f"{fsm_tot}/{n}",
        f"{proc_tot}/{n}",
        f"{cdc_tot}/{n}",
        f"{reset_tot}/{n}",
        f"{port_tot}/{n}",
    ))

    return "\n".join(lines)


def _depth_first_order(hierarchy: dict) -> list[str]:
    """Return module names in depth-first order starting from the top module."""
    top = hierarchy["top"]
    modules = hierarchy["modules"]
    visited: list[str] = []

    def visit(name: str) -> None:
        if name not in visited:
            visited.append(name)
            for child in modules[name]["children"]:
                visit(child)

    visit(top)
    for name in modules:
        if name not in visited:
            visited.append(name)
    return visited


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}%"


def check_thresholds(summary: dict, *, ports=None, generics=None, headers=None) -> list[str]:
    """One message per documented-percentage that is below its minimum.  A percentage over
    nothing (no ports at all) cannot fall short."""
    problems = []
    for label, minimum in (("ports", ports), ("generics", generics), ("headers", headers)):
        actual = summary[f"{label}_percent"]
        if minimum is not None and actual is not None and actual < minimum:
            problems.append(f"{label} described: {actual:.1f}% is below the required {minimum:g}%")
    return problems


def stats_rst(stats: list[ModuleStats], summary: dict, *, testbenches: int, tests: int, doc_pages: int) -> str:
    """The 'Documentation statistics' section appended to coverage.rst."""
    def ratio(s) -> str:
        return "–" if s.comment_ratio is None else f"{s.comment_ratio:.2f}"

    def frac(done: int, total: int) -> str:
        return f"{done}/{total}" if total else "–"

    rows = "\n".join(
        "       <tr>"
        f'<td><a href="modules/{s.name}/index.html">{s.name}</a></td>'
        f"<td>{frac(s.ports_described, s.ports)}</td>"
        f"<td>{frac(s.generics_described, s.generics)}</td>"
        f"<td>{'✓' if s.has_header else '–'}</td>"
        f"<td>{s.processes}</td><td>{s.wavedrom}</td><td>{s.mermaid}</td>"
        f"<td>{s.comment_lines}/{s.code_lines} ({ratio(s)})</td>"
        f"<td>{s.registers or '–'}</td></tr>"
        for s in stats)
    return (
        "\n"
        "Documentation statistics\n"
        "------------------------\n"
        "\n"
        f"* {summary['modules']} modules, {summary['headers']} with a header description "
        f"({_pct(summary['headers_percent'])})\n"
        f"* Ports described: {summary['ports_described']} of {summary['ports']} ({_pct(summary['ports_percent'])})\n"
        f"* Generics and parameters described: {summary['generics_described']} of {summary['generics']} "
        f"({_pct(summary['generics_percent'])})\n"
        f"* {summary['processes']} processes, {summary['wavedrom']} with a WaveDrom diagram, "
        f"{summary['mermaid']} with a Mermaid diagram\n"
        f"* {summary['comment_lines']} comment lines to {summary['code_lines']} code lines\n"
        f"* {summary['registers']} registers\n"
        f"* {testbenches} testbenches ({tests} tests)\n"
        f"* {doc_pages} generated pages\n"
        "\n"
        ".. raw:: html\n"
        "\n"
        '   <table class="coverage-table">\n'
        "     <thead>\n"
        "       <tr><th>Module</th><th>Ports described</th><th>Generics described</th><th>Header</th>"
        "<th>Processes</th><th>WaveDrom</th><th>Mermaid</th><th>Comment/code lines</th><th>Registers</th></tr>\n"
        "     </thead>\n"
        "     <tbody>\n"
        f"{rows}\n"
        "     </tbody>\n"
        "   </table>\n"
    )


def main(hierarchy_json: Path, docs_dir: Path, *, stats: bool = True, registers_dir: Path | None = None,
         tb_dir: Path | None = None, min_ports=None, min_generics=None, min_headers=None) -> int:
    """Run coverage detection and write terminal output, coverage.rst and (with `stats`)
    coverage.json.  Returns 1 when a minimum is not met, else 0."""
    with open(hierarchy_json) as f:
        hierarchy = json.load(f)

    if "top" not in hierarchy or "modules" not in hierarchy:
        sys.exit(f"Error: {hierarchy_json} missing 'top' or 'modules' keys")
    if hierarchy["top"] not in hierarchy["modules"]:
        sys.exit(f"Error: top module '{hierarchy['top']}' not found in modules")

    names = _depth_first_order(hierarchy)
    results = [detect_coverage(name, docs_dir) for name in names]

    print(format_terminal_table(results))

    rst = coverage_rst(results)
    problems: list[str] = []
    if stats:
        registers = registers_dir if registers_dir is not None else Path(os.environ.get("AUTODOC_REGISTERS_DIR", "registers"))
        support = hierarchy.get("support_files", [])
        bus_groups = Path(docs_dir) / "bus_groups.toml"
        per_module = [module_stats(n, hierarchy["modules"][n].get("file", ""), support, registers,
                                   bus_groups if bus_groups.is_file() else None) for n in names]
        summary = summarise(per_module)
        benches = discover_testbenches(Path(tb_dir if tb_dir is not None else os.environ.get("AUTODOC_TB_DIR", "tb")),
                                       set(names))
        tests = sum(len(b.tests) for b in benches)
        pages = sum(1 for p in Path(docs_dir).rglob("*.rst") if p.name != "coverage.rst")
        summary.update(testbenches=len(benches), tests=tests, doc_pages=pages)
        rst += stats_rst(per_module, summary, testbenches=len(benches), tests=tests, doc_pages=pages)
        (Path(docs_dir) / "coverage.json").write_text(json.dumps(
            {"summary": summary, "modules": [s.as_dict() for s in per_module]}, indent=2) + "\n")
        problems = check_thresholds(summary, ports=min_ports, generics=min_generics, headers=min_headers)
    out = docs_dir / "coverage.rst"
    out.write_text(rst)
    print(f"\nWrote {out}")
    for p in problems:
        print(f"Coverage check failed: {p}", file=sys.stderr)
    return 1 if problems else 0


def coverage_rst(results: list[CoverageResult]) -> str:
    """Generate a coverage.rst Sphinx page with a coloured HTML table."""
    n = len(results)

    def bool_cell(v: bool) -> str:
        cls = "cov-yes" if v else "cov-no"
        txt = "✓" if v else "–"
        return f'         <td class="{cls}">{txt}</td>'

    def count_cell(v: int) -> str:
        cls = "cov-count" if v > 0 else "cov-no"
        txt = str(v) if v > 0 else "–"
        return f'         <td class="{cls}">{txt}</td>'

    rows = []
    for r in results:
        cells = [
            f'         <td><a href="modules/{r.name}/index.html">{r.name}</a></td>',
            bool_cell(r.fsm),
            count_cell(r.process_count),
            bool_cell(r.cdc),
            bool_cell(r.reset),
            count_cell(r.port_count),
        ]
        rows.append("       <tr>\n" + "\n".join(cells) + "\n       </tr>")

    fsm_tot   = sum(1 for r in results if r.fsm)
    proc_tot  = sum(1 for r in results if r.process_count > 0)
    cdc_tot   = sum(1 for r in results if r.cdc)
    reset_tot = sum(1 for r in results if r.reset)
    port_tot  = sum(1 for r in results if r.port_count > 0)

    body = "\n".join(rows)

    return (
        "Documentation Coverage\n"
        "======================\n"
        "\n"
        ".. raw:: html\n"
        "\n"
        '   <table class="coverage-table">\n'
        "     <thead>\n"
        "       <tr>\n"
        "         <th>Module</th><th>FSM</th><th>Processes</th>\n"
        "         <th>CDC</th><th>Reset</th><th>Ports</th>\n"
        "       </tr>\n"
        "     </thead>\n"
        "     <tbody>\n"
        f"{body}\n"
        "     </tbody>\n"
        "     <tfoot>\n"
        "       <tr>\n"
        "         <td>Totals</td>\n"
        f"         <td>{fsm_tot}/{n}</td>\n"
        f"         <td>{proc_tot}/{n}</td>\n"
        f"         <td>{cdc_tot}/{n}</td>\n"
        f"         <td>{reset_tot}/{n}</td>\n"
        f"         <td>{port_tot}/{n}</td>\n"
        "       </tr>\n"
        "     </tfoot>\n"
        "   </table>\n"
    )


def _minimum(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Documentation coverage page and statistics.")
    ap.add_argument("hierarchy_json", type=Path)
    ap.add_argument("docs_dir", type=Path)
    ap.add_argument("--no-stats", action="store_true", help="only the original table; no statistics, JSON or minimums")
    ap.add_argument("--registers-dir", type=Path)
    ap.add_argument("--tb-dir", type=Path)
    ap.add_argument("--min-ports", type=_minimum, help="fail below this %% of ports described")
    ap.add_argument("--min-generics", type=_minimum, help="fail below this %% of generics described")
    ap.add_argument("--min-headers", type=_minimum, help="fail below this %% of modules with a header description")
    a = ap.parse_args()
    sys.exit(main(a.hierarchy_json, a.docs_dir, stats=not a.no_stats, registers_dir=a.registers_dir, tb_dir=a.tb_dir,
                  min_ports=a.min_ports, min_generics=a.min_generics, min_headers=a.min_headers))

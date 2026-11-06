"""Documentation statistics: how well a design is documented, not just what was extracted."""

import textwrap

from doc_stats import ModuleStats, module_stats, summarise

SV = textwrap.dedent("""\
    // Counter with enable.
    //
    // Counts clock cycles.
    module counter #(
        // Width of the count.
        parameter int W = 8,
        parameter int RESET_VALUE = 0
    ) (
        // System clock.
        input  logic         clk,
        input  logic         rst,
        // Count output.
        output logic [W-1:0] q
    );
        // p_count: Counts up.
        //
        // .. wavedrom::
        //
        //    { "signal": [{ "name": "clk", "wave": "p." }] }
        always_ff @(posedge clk) begin : p_count
            if (rst) q <= RESET_VALUE;
            else     q <= q + 1;  // trailing comment
        end

        // p_other: Another.
        //
        // .. mermaid::
        //
        //    flowchart LR
        //      A --> B
        always_ff @(posedge clk) begin : p_other
            q <= q;
        end
    endmodule
""")


def _stats(tmp_path, text=SV, name="counter", suffix=".sv", **kw):
    src = tmp_path / f"{name}{suffix}"
    src.write_text(text)
    return module_stats(name, str(src), [], tmp_path / "registers", **kw)


def test_ports_total_and_described(tmp_path):
    s = _stats(tmp_path)
    assert (s.ports, s.ports_described) == (3, 2)


def test_parameters_total_and_described(tmp_path):
    s = _stats(tmp_path)
    assert (s.generics, s.generics_described) == (2, 1)


def test_processes_and_their_diagrams(tmp_path):
    s = _stats(tmp_path)
    assert (s.processes, s.wavedrom, s.mermaid) == (2, 1, 1)


def test_header_description_is_detected(tmp_path):
    assert _stats(tmp_path).has_header is True
    bare = _stats(tmp_path, "module m (input logic a);\nendmodule\n", name="m")
    assert bare.has_header is False


def test_comment_and_code_lines_and_the_ratio(tmp_path):
    text = "// one\n// two\n\nmodule m (input logic a);\n  assign x = a; // trailing\n/* block\n   comment */\nendmodule\n"
    s = _stats(tmp_path, text, name="m")
    assert s.comment_lines == 4          # // one, // two, and the two block-comment lines
    assert s.code_lines == 3             # module, assign (with its trailing comment), endmodule
    assert round(s.comment_ratio, 2) == 1.33


def test_a_file_without_code_has_no_ratio(tmp_path):
    s = _stats(tmp_path, "// only a comment\n", name="m")
    assert s.code_lines == 0 and s.comment_ratio is None


def test_vhdl_is_read_too(tmp_path):
    text = ("-- Adder.\nentity add is\n    port (\n        -- Operand.\n        a : in std_logic;\n        b : in std_logic\n    );\n"
            "end entity add;\n")
    s = _stats(tmp_path, text, name="add", suffix=".vhd")
    assert (s.ports, s.ports_described, s.has_header) == (2, 1, True)


def test_registers_are_counted_from_the_modules_toml(tmp_path):
    regs = tmp_path / "registers"
    regs.mkdir()
    (regs / "regs_counter.toml").write_text(
        '[ctrl]\nmode = "r_w"\n[ctrl.en]\ntype = "bit"\n[status]\nmode = "r"\n[other]\nmode = "w"\n')
    assert _stats(tmp_path).registers == 3
    assert _stats(tmp_path, name="m", text="module m; endmodule\n").registers == 0


def test_a_file_that_cannot_be_read_gives_empty_statistics_not_an_exception(tmp_path):
    s = module_stats("ghost", str(tmp_path / "missing.sv"), [], tmp_path / "registers")
    assert s.ports == 0 and s.has_header is False


def _m(name, **over):
    base = dict(name=name, ports=0, ports_described=0, processes=0, wavedrom=0, mermaid=0, generics=0,
                generics_described=0, has_header=False, comment_lines=0, code_lines=0, registers=0)
    return ModuleStats(**{**base, **over})


def test_summary_adds_up_and_gives_percentages():
    summary = summarise([_m("a", ports=4, ports_described=3, processes=2, wavedrom=1, has_header=True, registers=3),
                         _m("b", ports=6, ports_described=2, mermaid=1, generics=2, generics_described=1)])
    assert summary["modules"] == 2
    assert (summary["ports"], summary["ports_described"]) == (10, 5) and summary["ports_percent"] == 50.0
    assert (summary["processes"], summary["wavedrom"], summary["mermaid"]) == (2, 1, 1)
    assert summary["generics_percent"] == 50.0 and summary["registers"] == 3
    assert (summary["headers"], summary["headers_percent"]) == (1, 50.0)


def test_a_percentage_over_nothing_is_none_not_a_division_error():
    summary = summarise([_m("a")])
    assert summary["ports_percent"] is None and summary["generics_percent"] is None
    assert summarise([])["headers_percent"] is None


def test_example_full_numbers_are_consistent(tmp_path):
    from pathlib import Path
    root = Path(__file__).parents[4] / "example_full"
    s = module_stats("sample_ctrl", str(root / "src" / "sample_ctrl.vhd"), [], root / "registers")
    assert s.ports == 7 and s.ports_described == 7 and s.has_header and s.processes == 3
    assert s.mermaid == 1 and s.wavedrom == 2
    assert s.comment_lines > 0 and s.code_lines > 0
    regs = module_stats("axil_regs", str(root / "src" / "axil_regs.sv"), [], root / "registers")
    assert regs.registers == 5


# ── page, coverage.json and thresholds (generate_coverage) ───────────────────

import json  # noqa: E402

from generate_coverage import check_thresholds, main, stats_rst  # noqa: E402


def _project(tmp_path, described=True):
    """A one-module project: hierarchy.json, its source and a docs dir."""
    src = tmp_path / "counter.sv"
    src.write_text(SV if described else SV.replace("// System clock.\n", "").replace("// Count output.\n", ""))
    docs = tmp_path / "docs"
    (docs / "modules" / "counter").mkdir(parents=True)
    hier = tmp_path / "hierarchy.json"
    hier.write_text(json.dumps({"top": "counter", "support_files": [],
                                "modules": {"counter": {"file": str(src), "children": [], "parents": []}}}))
    return hier, docs


def test_page_gains_a_statistics_section_and_keeps_the_old_table(tmp_path):
    hier, docs = _project(tmp_path)
    assert main(hier, docs, registers_dir=tmp_path / "registers") == 0
    page = (docs / "coverage.rst").read_text()
    assert 'class="coverage-table"' in page and "<th>Module</th><th>FSM</th>" in page   # unchanged
    assert "Documentation statistics" in page and "66.7%" in page                        # 2 of 3 ports


def test_stats_can_be_switched_off_and_the_page_is_then_the_old_one(tmp_path):
    hier, docs = _project(tmp_path)
    main(hier, docs, stats=False, registers_dir=tmp_path / "registers")
    assert "Documentation statistics" not in (docs / "coverage.rst").read_text()
    assert not (docs / "coverage.json").exists()


def test_coverage_json_is_written_with_modules_and_summary(tmp_path):
    hier, docs = _project(tmp_path)
    main(hier, docs, registers_dir=tmp_path / "registers")
    data = json.loads((docs / "coverage.json").read_text())
    assert data["summary"]["ports"] == 3 and data["summary"]["ports_percent"] == 66.7
    assert data["modules"][0]["name"] == "counter" and data["modules"][0]["ports_described"] == 2
    assert data["summary"]["doc_pages"] >= 0


def test_a_threshold_that_is_met_exits_zero_and_one_that_is_missed_does_not(tmp_path):
    hier, docs = _project(tmp_path)
    assert main(hier, docs, min_ports=60, registers_dir=tmp_path / "registers") == 0
    assert main(hier, docs, min_ports=80, registers_dir=tmp_path / "registers") == 1


def test_check_thresholds_names_each_shortfall():
    summary = summarise([_m("a", ports=4, ports_described=1, generics=2, generics_described=2, has_header=False)])
    problems = check_thresholds(summary, ports=80, generics=80, headers=100)
    assert len(problems) == 2
    assert "ports" in problems[0] and "25.0%" in problems[0] and "80" in problems[0]
    assert "headers" in problems[1]


def test_a_threshold_over_nothing_is_not_a_failure():
    assert check_thresholds(summarise([_m("a")]), ports=80, generics=80, headers=None) == []


def test_stats_rst_lists_the_project_summary_and_each_module(tmp_path):
    rst = stats_rst([_m("a", ports=4, ports_described=3, comment_lines=10, code_lines=20)],
                    summarise([_m("a", ports=4, ports_described=3, comment_lines=10, code_lines=20)]),
                    testbenches=2, tests=5, doc_pages=7)
    assert "75.0%" in rst and "0.50" in rst
    assert "2 testbenches (5 tests)" in rst and "7 generated pages" in rst

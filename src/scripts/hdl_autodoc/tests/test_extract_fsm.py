"""
test_extract_fsm.py
-------------------
Tests for extract_fsm.py.

Covers:
  - FSM transition extraction from VHDL case blocks.
  - FSM transition extraction from SystemVerilog case blocks.
  - Files with no FSM return an empty transition list.
  - Graphviz dot output contains the expected state nodes and edges.
  - RST output contains the expected sections and transition data.
"""


from extract_fsm import (
    collect_states,
    extract_outputs_sv,
    extract_outputs_vhdl,
    locate_fsm,
    extract_transitions_sv,
    extract_transitions_vhdl,
    write_dot,
    write_rst,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — minimal HDL snippets
# ─────────────────────────────────────────────────────────────────────────────

VHDL_FSM = """\
architecture rtl of traffic_light is
  type t_state is (RED, GREEN, AMBER);
  signal state, next_state : t_state;
begin
  p_next_state : process(state, timer_exp) is
  begin
    next_state <= state;
    case state is
      when RED =>
        if timer_exp = '1' then next_state <= GREEN; end if;
      when GREEN =>
        if timer_exp = '1' then next_state <= AMBER; end if;
      when AMBER =>
        next_state <= RED;
    end case;
  end process p_next_state;
end architecture rtl;
"""

SV_FSM = """\
module pwm_ctrl (input logic clk);
  typedef enum logic [1:0] { IDLE, COUNTING, DONE } t_state;
  t_state state, next_state;

  always_comb begin : p_next_state
    next_state = state;
    case (state)
      IDLE:     if (en)           next_state = COUNTING;
      COUNTING: if (count == 8'hFF) next_state = DONE;
      DONE:                       next_state = IDLE;
    endcase
  end
endmodule
"""


# ─────────────────────────────────────────────────────────────────────────────
# VHDL transition extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_vhdl_extracts_conditional_transitions():
    """Conditional transitions (if timer_exp = '1' then next_state <= X) are detected."""
    transitions = extract_transitions_vhdl(VHDL_FSM)
    pairs = {(frm, to) for frm, to, _ in transitions}
    assert ("RED", "GREEN") in pairs
    assert ("GREEN", "AMBER") in pairs


def test_vhdl_extracts_unconditional_transition():
    """Unconditional assignments (next_state <= X without an if) are detected."""
    transitions = extract_transitions_vhdl(VHDL_FSM)
    pairs = {(frm, to) for frm, to, _ in transitions}
    assert ("AMBER", "RED") in pairs


def test_vhdl_no_fsm_returns_empty():
    """Source with no case/next_state block returns an empty list."""
    text = "entity foo is port (); end entity foo;\n"
    assert extract_transitions_vhdl(text) == []


def test_vhdl_transition_condition_captured():
    """The condition string is preserved in the transition tuple."""
    transitions = extract_transitions_vhdl(VHDL_FSM)
    cond_transitions = [(frm, to, cond) for frm, to, cond in transitions if cond]
    assert len(cond_transitions) > 0


# ─────────────────────────────────────────────────────────────────────────────
# SystemVerilog transition extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_sv_extracts_conditional_transitions():
    """Conditional SV transitions inside always_comb are detected."""
    transitions = extract_transitions_sv(SV_FSM)
    pairs = {(frm, to) for frm, to, _ in transitions}
    assert ("IDLE", "COUNTING") in pairs
    assert ("COUNTING", "DONE") in pairs


def test_sv_extracts_unconditional_transition():
    """Unconditional SV transition (DONE -> IDLE) is detected."""
    transitions = extract_transitions_sv(SV_FSM)
    pairs = {(frm, to) for frm, to, _ in transitions}
    assert ("DONE", "IDLE") in pairs


def test_sv_no_fsm_returns_empty():
    """SV source with no FSM case block returns an empty list."""
    text = "module foo (input logic clk); assign out = in; endmodule\n"
    assert extract_transitions_sv(text) == []


# ─────────────────────────────────────────────────────────────────────────────
# FSMs that do not use `next_state`: any selector and target name, any process order
# ─────────────────────────────────────────────────────────────────────────────

_VHDL_LOGIC = """\
  p_logic : process(clk) is
  begin
    if rising_edge(clk) then
      valid <= '0';
      case curr_st is
        when S_IDLE => null;
        when S_RUN  => valid <= '1';
        when S_DONE => null;
      end case;
    end if;
  end process p_logic;
"""

_VHDL_FSM_ONE_PROCESS = """\
  p_fsm : process(clk) is
  begin
    if rising_edge(clk) then
      case curr_st is
        when S_IDLE => if go = '1' then curr_st <= S_RUN; end if;
        when S_RUN  => if ready = '1' then curr_st <= S_DONE; end if;
        when S_DONE => curr_st <= S_IDLE;
      end case;
    end if;
  end process p_fsm;
"""

_EXPECTED = {("S_IDLE", "S_RUN"), ("S_RUN", "S_DONE"), ("S_DONE", "S_IDLE")}


def _vhdl(*processes):
    return "architecture rtl of x is\nbegin\n" + "".join(processes) + "end architecture;\n"


def test_vhdl_state_assigned_directly_in_one_process():
    pairs = {(f, t) for f, t, _ in extract_transitions_vhdl(_vhdl(_VHDL_FSM_ONE_PROCESS))}
    assert pairs == _EXPECTED


def test_vhdl_process_order_does_not_matter():
    for order in ((_VHDL_LOGIC, _VHDL_FSM_ONE_PROCESS), (_VHDL_FSM_ONE_PROCESS, _VHDL_LOGIC)):
        pairs = {(f, t) for f, t, _ in extract_transitions_vhdl(_vhdl(*order))}
        assert pairs == _EXPECTED


def test_vhdl_target_signal_may_have_any_name():
    text = _vhdl(_VHDL_FSM_ONE_PROCESS.replace("curr_st <=", "nxt_st <="))
    assert {(f, t) for f, t, _ in extract_transitions_vhdl(text)} == _EXPECTED


def test_vhdl_outputs_come_from_the_logic_process_not_the_fsm_process():
    outputs = extract_outputs_vhdl(_vhdl(_VHDL_FSM_ONE_PROCESS, _VHDL_LOGIC))
    assert outputs["S_RUN"]["valid"] == "1" and outputs["S_IDLE"]["valid"] == "0"


_SV_LOGIC = """\
  always_comb begin
    valid = 1'b0;
    case (curr_st)
      S_RUN: valid = 1'b1;
      default: ;
    endcase
  end
"""

_SV_FSM_ONE_PROCESS = """\
  always_ff @(posedge clk) begin
    case (curr_st)
      S_IDLE: if (go) curr_st <= S_RUN;
      S_RUN:  if (ready) curr_st <= S_DONE;
      S_DONE: curr_st <= S_IDLE;
      default: curr_st <= S_IDLE;
    endcase
  end
"""


def test_sv_state_assigned_directly_in_any_process_order():
    for order in ((_SV_LOGIC, _SV_FSM_ONE_PROCESS), (_SV_FSM_ONE_PROCESS, _SV_LOGIC)):
        pairs = {(f, t) for f, t, _ in extract_transitions_sv("module m;\n" + "".join(order) + "endmodule\n")}
        assert _EXPECTED <= pairs


def test_sv_outputs_come_from_the_logic_process():
    outputs = extract_outputs_sv("module m;\n" + _SV_FSM_ONE_PROCESS + _SV_LOGIC + "endmodule\n")
    assert outputs["S_RUN"]["valid"] == "1"


def test_vhdl_state_assignments_inside_nested_if_else_are_all_found():
    text = _vhdl("""\
  p_fsm : process(clk) is
  begin
    case curr_st is
      when S_IDLE =>
        if go = '1' then
          if wr = '1' then curr_st <= S_RUN; else curr_st <= S_DONE; end if;
        end if;
      when S_RUN =>
        if ready = '1' then
          data <= x;
          curr_st <= S_DONE;
        elsif abort = '1' then curr_st <= S_IDLE;
        end if;
      when S_DONE => curr_st <= S_IDLE;
    end case;
  end process p_fsm;
""")
    got = set(extract_transitions_vhdl(text))
    assert ("S_IDLE", "S_RUN", "go = '1' and wr = '1'") in got
    assert ("S_IDLE", "S_DONE", "go = '1' and not (wr = '1')") in got
    assert ("S_RUN", "S_DONE", "ready = '1'") in got
    assert ("S_RUN", "S_IDLE", "abort = '1'") in got
    assert ("S_DONE", "S_IDLE", "") in got


def test_vhdl_fsm_lines_point_into_the_fsm_process_whatever_the_order():
    text = _vhdl(_VHDL_LOGIC, _VHDL_FSM_ONE_PROCESS)
    lines = text.split("\n")
    where = locate_fsm(text, vhdl=True)
    assert lines[where["case"] - 1].strip() == "case curr_st is" and where["case"] > len(_VHDL_LOGIC.split("\n"))
    assert lines[where["states"]["S_RUN"] - 1].strip().startswith("when S_RUN")
    for (frm, to, _), line in zip(extract_transitions_vhdl(text), where["transitions"]):
        assert f"<= {to}" in lines[line - 1], (frm, to, lines[line - 1])


def test_vhdl_fsm_lines_ignore_commented_out_code():
    text = _vhdl("  -- case curr_st is when S_RUN => curr_st <= S_DONE;\n", _VHDL_FSM_ONE_PROCESS)
    where = locate_fsm(text, vhdl=True)
    assert text.split("\n")[where["case"] - 1].strip() == "case curr_st is"


def test_sv_fsm_lines_point_at_the_arms_and_assignments():
    text = "module m;\n" + _SV_LOGIC + _SV_FSM_ONE_PROCESS + "endmodule\n"
    lines = text.split("\n")
    where = locate_fsm(text, vhdl=False)
    assert lines[where["case"] - 1].strip() == "case (curr_st)" and "always_ff" in lines[where["case"] - 2]
    assert lines[where["states"]["S_DONE"] - 1].strip().startswith("S_DONE:")
    for (frm, to, _), line in zip(extract_transitions_sv(text), where["transitions"]):
        assert f"<= {to}" in lines[line - 1]


def test_locate_fsm_is_none_without_an_fsm():
    assert locate_fsm("entity foo is port (); end entity foo;\n", vhdl=True) is None


def test_a_case_that_assigns_no_state_is_not_an_fsm():
    text = _vhdl(_VHDL_LOGIC)
    assert extract_transitions_vhdl(text) == []


# ─────────────────────────────────────────────────────────────────────────────
# Dot writer
# ─────────────────────────────────────────────────────────────────────────────

def test_write_dot_contains_states():
    """Dot output includes a node declaration for each state."""
    transitions = [("RED", "GREEN", "timer_exp"), ("GREEN", "AMBER", "")]
    states = collect_states(transitions)
    dot = write_dot(transitions, states, "traffic_light")
    assert "RED" in dot
    assert "GREEN" in dot
    assert "AMBER" in dot


def test_write_dot_contains_edges():
    """Dot output includes directed edges for each transition."""
    transitions = [("RED", "GREEN", "timer_exp")]
    states = collect_states(transitions)
    dot = write_dot(transitions, states, "my_fsm")
    assert "RED -> GREEN" in dot


def test_write_dot_has_start_node():
    """Dot output includes the invisible start node pointing to the first state."""
    transitions = [("IDLE", "ACTIVE", "")]
    states = collect_states(transitions)
    dot = write_dot(transitions, states, "my_fsm")
    assert "__start" in dot


# ─────────────────────────────────────────────────────────────────────────────
# RST writer
# ─────────────────────────────────────────────────────────────────────────────

def test_write_rst_contains_graphviz_directive():
    """RST output contains a '.. graphviz::' directive referencing the dot file."""
    transitions = [("RED", "GREEN", "timer_exp")]
    states = collect_states(transitions)
    rst = write_rst("fsm.dot", states, {}, transitions, "traffic_light", "traffic_light.vhd")
    assert ".. graphviz::" in rst
    assert "fsm.dot" in rst


def test_write_rst_contains_transitions_table():
    """RST output includes a transitions list-table with From/To/Condition columns."""
    transitions = [("RED", "GREEN", "timer_exp = '1'")]
    states = collect_states(transitions)
    rst = write_rst("fsm.dot", states, {}, transitions, "traffic_light", "traffic_light.vhd")
    assert "Transitions" in rst
    assert "RED" in rst
    assert "GREEN" in rst


# ── conditions with parentheses, ternaries, begin/end arms ───────────────────

SV_HARD = """\
always_comb begin : p_next_state
    next_state = state;
    case (state)
        IDLE: begin
            if (start && (mode == 2'd1 || force_go)) begin
                next_state = RUN;
            end
        end
        RUN: next_state = done ? IDLE : ERR;
        ERR: if (clear) next_state = IDLE;
        default: next_state = IDLE;
    endcase
end
"""


def test_sv_condition_with_nested_parentheses_is_kept_whole():
    from extract_fsm import extract_transitions_sv
    got = extract_transitions_sv(SV_HARD)
    assert ("IDLE", "RUN", "start && (mode == 2'd1 || force_go)") in got


def test_sv_ternary_gives_a_transition_for_each_outcome():
    from extract_fsm import extract_transitions_sv
    got = extract_transitions_sv(SV_HARD)
    assert ("RUN", "IDLE", "done") in got
    assert ("RUN", "ERR", "!(done)") in got


def test_sv_ternary_does_not_invent_states_or_split_on_its_colon():
    from extract_fsm import extract_transitions_sv
    froms = {t[0] for t in extract_transitions_sv(SV_HARD)}
    assert froms == {"IDLE", "RUN", "ERR"}


def test_sv_simple_arms_are_unchanged():
    from extract_fsm import extract_transitions_sv
    assert ("ERR", "IDLE", "clear") in extract_transitions_sv(SV_HARD)
    assert len(extract_transitions_sv(SV_HARD)) == 4


VHDL_HARD = """\
case state is
  when IDLE =>
     if start = '1' and (mode = "01" or force_go = '1') then next_state <= RUN; end if;
  when RUN =>
     if done = '1' then next_state <= IDLE;
     elsif err = '1' then next_state <= ERR; end if;
  when ERR => next_state <= IDLE;
end case;
"""


def test_vhdl_condition_with_parentheses_is_kept_whole():
    from extract_fsm import extract_transitions_vhdl
    got = extract_transitions_vhdl(VHDL_HARD)
    assert ("IDLE", "RUN", "start = '1' and (mode = \"01\" or force_go = '1')") in got
    assert ("RUN", "IDLE", "done = '1'") in got and ("RUN", "ERR", "err = '1'") in got
    assert ("ERR", "IDLE", "") in got


def test_dot_escapes_double_quotes_in_conditions():
    from extract_fsm import write_dot
    dot = write_dot([("A", "B", 'mode = "01"')], ["A", "B"], "m")
    assert 'label="mode = \\"01\\""' in dot

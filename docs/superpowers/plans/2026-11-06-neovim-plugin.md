# HDL AutoDoc for Neovim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Neovim plugin (`nvim/`) that shows the documentation of the current VHDL/SystemVerilog buffer in a live split inside the terminal, with the VS Code extension's content: header, generics, ports, instances, FSM, processes with waveforms and Mermaid, CDC, reset, and Markdown export.

**Architecture:** Lua does the Neovim work (commands, split, refresh, jump, drawing); `src/scripts/hdl_autodoc/autodoc_json.py` does the reading, unchanged. Pure Lua modules (`wave`, `mermaid`, `render`, `markdown`) turn the decoded JSON document into lines, highlight spans and a jump table; `runner`/`session`/`panel` are the thin Neovim-facing layer with injectable dependencies so they are testable headless.

**Tech Stack:** Neovim ≥ 0.10 (`vim.system`, `vim.json`, `vim.uv`), Lua, Python ≥ 3.11 standard library (already required), tests in headless Neovim with a small in-repo runner. No plenary, no other plugin.

**Spec:** `docs/superpowers/specs/2026-11-06-neovim-plugin-design.md`

## Global Constraints

- Neovim 0.10+; Python 3.11+; no third-party Lua or Python packages; no change to the `autodoc_json.py` JSON contract (shape in that file's docstring, mirrored in `vscode/src/types.ts`).
- Module namespace `hdl-autodoc` under `nvim/lua/hdl-autodoc/`. Commands: `:HdlDoc`, `:HdlDocRefresh`, `:HdlDocExportMarkdown [file]`. Panel keys: `<CR>` jump, `q` close. Filetype of the panel buffer: `hdldoc`.
- Options (`setup(opts)`): `python=""`, `refresh_delay_ms=400`, `width=80`, `side="right"`.
- Commands are defined in `setup()`, not in a `plugin/` file (subdirectory install).
- Tests: `nvim --headless -u NONE -l nvim/test/run.lua [filter]` from the repository root, wrapped by `make -C nvim test`. Every commit leaves `make -C nvim test`, `cd src && python -m pytest -q` and `cd vscode && npm test` green.
- Source-line numbers in the document are 1-based; panel highlight spans are `{line=1-based, col=0-based byte start, end_col=0-based byte end (exclusive), group=string}`.
- Commit messages end with the `Co-Authored-By` line the session supplies.

## Review Focus

Inputs the spec implies but no happy-path test would cover; each has a test in the task that owns the code.

1. Authors' WaveDrom comments are often not strict JSON (single quotes, unquoted keys) or use features the drawing lacks (`phase`, `period`, nested groups, `u`/`d`, edges): the signal must show its source, never raise (Task 2).
2. Descriptions with multi-byte characters (µ, →, CJK): highlight columns are bytes, truncation is by display width, so a span must never split a character (Task 4).
3. A refresh that finishes after the buffer was closed, switched, or the panel window closed by the user: dropped silently, no error (Tasks 6, 8).
4. A document that is nearly empty (no module, zero ports, `fsm=null`, `cdc=null`) and one with every list large: no crash, sections omitted when empty (Tasks 4, 5).
5. Python missing, too old, or hanging: the panel says what to set, once, and the next refresh retries (Task 5).
6. A 40-column split: names stay whole, descriptions truncate with `…` (Task 4).

---

### Task 1: Scaffold, test runner, CI job

**Files:**
- Create: `nvim/test/run.lua`, `nvim/test/harness_spec.lua`, `nvim/Makefile`, `nvim/lua/hdl-autodoc/init.lua` (stub returning `{ setup = function() end }`)
- Modify: `.github/workflows/ci.yml` (add job `neovim`)

**Interfaces:**
- Produces: `run.lua` loads every `nvim/test/*_spec.lua` (or those whose filename contains the CLI filter), exposes globals `describe(name, fn)`, `it(name, fn)`, `eq(expected, actual)` (deep compare, error message shows both), `truthy(v)`, `raises(fn, pattern)`; sets `package.path` to `nvim/lua/?.lua;nvim/lua/?/init.lua`; prints `N passed, M failed`; exits non-zero on failure or on an error thrown outside a test.

- [ ] **Step 1: Write `harness_spec.lua`** with: a passing `it`, `eq` on nested tables, `raises` matching a pattern, and a test that `require("hdl-autodoc")` loads.
- [ ] **Step 2: Run** `nvim --headless -u NONE -l nvim/test/run.lua` — Expected: FAIL (`run.lua` missing).
- [ ] **Step 3: Implement `run.lua`** per the Interfaces block; `nvim/Makefile` has `test:` running it from the repo root, passing `$(FILTER)` (so `make -C nvim test FILTER=wave` runs `wave_spec.lua`).
- [ ] **Step 4: Run** `make -C nvim test` — Expected: `4 passed, 0 failed`, exit 0. Temporarily add a failing `it` and confirm exit ≠ 0, then remove it.
- [ ] **Step 5: CI** — job `neovim` on `ubuntu-latest`, matrix `neovim: [stable, nightly]` using `rhysd/action-setup-vim@v1` (`neovim: true`, `version: ${{ matrix.neovim }}`), `actions/setup-python@v5` 3.13, then `make -C nvim test`.
- [ ] **Step 6: Commit** `feat: Neovim plugin scaffold and test runner`.

### Task 2: `wave.lua` — WaveDrom as Unicode

**Files:** Create `nvim/lua/hdl-autodoc/wave.lua`, `nvim/test/wave_spec.lua`

**Interfaces:**
- Produces: `wave.render(source: string) -> lines: string[]|nil, reason: string|nil`. `source` is the WaveDrom text from the document. Returns `nil, reason` when the whole source cannot be drawn (invalid JSON, no `signal`); a signal that cannot be drawn becomes the line `name  (cannot draw: reason)` and the rest still render.

Glyph contract (2 columns per wave character, `STEP = 2`): `0`/`l` → `▁▁`; `1`/`h` → `▔▔`; `x` → `▒▒`; `z` → `┄┄`; a 0→1 transition → `╱▔`, a 1→0 transition → `╲▁`; `p`/`P` → `╱╲`, `n`/`N` → `╲╱`; `.` repeats the previous step's steady form (a clock pulse repeats the pulse, a level repeats the level, not the transition); `=` and `2`–`9` start a data cell spanning itself plus following `.`, drawn `[label]` padded with spaces to `2*n` columns and truncated with `…`, label taken from `data` in order (missing → `=`); `|` → two spaces; anything else (`u`, `d`, …) makes that signal unsupported. Names are padded to the longest name, then two spaces. Groups (nested arrays whose first element is a string) are flattened with `group.name`. A top-level `config`, `head`, `foot`, `edge`, `assign` is ignored.

- [ ] **Step 1: Write the tests** (exact strings):
  - `{"signal":[{"name":"s","wave":"01.0"}]}` → `{ "s  ▁▁╱▔▔▔╲▁" }`
  - `{"signal":[{"name":"clk","wave":"p..."},{"name":"a","wave":"0.1."}]}` → `{ "clk  ╱╲╱╲╱╲╱╲", "a    ▁▁▁▁╱▔▔▔" }`
  - `{"signal":[{"name":"d","wave":"x=.x","data":["ab"]}]}` → `{ "d  ▒▒[ab]▒▒" }` (the data cell is 4 columns)
  - signal with `"wave":"0u1"` → its line is `u  (cannot draw: unsupported 'u')`, a sibling signal still draws.
  - `{'signal': [...]}` (single quotes) and `""` → `nil` and a reason starting `not valid JSON`.
  - nested group `["grp", {"name":"a","wave":"01"}]` → name `grp.a`.
  - `data` label wider than its cell → ends with `…`.
- [ ] **Step 2: Run** `make -C nvim test FILTER=wave` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `wave.render` with `vim.json.decode` inside `pcall`; one function per concern (parse step list, draw step, assemble row).
- [ ] **Step 4: Run** — Expected: all pass.
- [ ] **Step 5: Commit** `feat: draw WaveDrom waveforms as Unicode`.

### Task 3: `mermaid.lua` — flowchart edge list

**Files:** Create `nvim/lua/hdl-autodoc/mermaid.lua`, `nvim/test/mermaid_spec.lua`

**Interfaces:** Produces `mermaid.edges(source: string) -> lines: string[]|nil`. Returns lines only for `flowchart`/`graph` sources (first non-blank line, any direction); each edge `A --> B`, `A --- B`, `A -.-> B`, `A ==> B`, `A -->|label| B`, `A -- label --> B` becomes `A → B` or `A → B (label)`; node text `A[Start]`, `B(Round)`, `C{Choice}`, `D((Circle))` is shown as its label (`Start`). Chained edges (`A --> B --> C`) and `;` separated statements are split. `subgraph`, `style`, `classDef`, `click` lines are skipped. Any other diagram type, or a flowchart with no edge, returns `nil` (the caller shows the source).

- [ ] **Step 1: Write tests**: `flowchart LR\n  A[Start] --> B{Ok?}\n  B -->|yes| C[Done]\n  B -- no --> A` → `{"Start → Ok?", "Ok? → Done (yes)", "Ok? → Start (no)"}`; chained edges; `sequenceDiagram` → `nil`; `graph TD` without edges → `nil`; `style A fill:#f00` ignored.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** with Lua patterns: split statements, normalise node text, find arrows by a small table of patterns.
- [ ] **Step 4: Run** — Expected: pass.
- [ ] **Step 5: Commit** `feat: Mermaid flowcharts as an edge list`.

### Task 4: `render.lua` — header, generics, ports, instances

**Files:**
- Create: `nvim/lua/hdl-autodoc/render.lua`, `nvim/test/render_spec.lua`, `fixtures/markdown/sample_ctrl.json`, `fixtures/markdown/sample_capture.json`
- Generated once by: `python src/scripts/hdl_autodoc/autodoc_json.py --language vhdl < example_full/src/sample_ctrl.vhd` and `--language systemverilog < example_full/src/sample_capture.sv`

**Interfaces:**
- Produces: `render.render(doc: table, opts: {width: integer}) -> {lines: string[], spans: Span[], jumps: table<integer, integer>}`; `render.message(text: string) -> same shape` (an error or note panel). `doc` is the decoded JSON (see `vscode/src/types.ts`).
- Layout: title line `# <module name>` (group `Title`), description paragraphs wrapped to `width`, then sections `## Generics`/`## Parameters` (VHDL/SV), `## Ports`, `## Instances`, each omitted when empty; bus groups under `### <group>`. A port row is `name  dir  type(range)  description` with columns aligned per table; descriptions truncated with `…` to fit `width` by display width (`vim.fn.strdisplaywidth`); names never truncated. `jumps[panel_line] = row.line`. Highlight groups: `HdlDocTitle`, `HdlDocSection`, `HdlDocName`, `HdlDocType`, `HdlDocDirection`, `HdlDocDim` (defined by `render.define_highlights()` with `default` links to `Title`, `Statement`, `Identifier`, `Type`, `Keyword`, `Comment`).
- Later tasks add sections by appending to the same builder; `render.lua` keeps an internal `Builder` with `add(line, group?)`, `add_row(cells, source_line)`.

- [ ] **Step 1: Generate the two fixture JSON files** (commands above) and commit nothing yet.
- [ ] **Step 2: Write tests** on small hand-built docs and the fixtures: title and description present; empty generics/instances omit their section; `Parameters` for `systemverilog`; port rows align (all `type` columns start at the same byte offset); `jumps` maps a port row to its `line`; groups produce `### <group>` headings; `width=40` truncates a long description with `…` and keeps names whole; a description containing `µ→` is never cut inside a character (decode with `vim.str_utf_pos` and check validity); highlight spans cover the module name; `doc.module == nil` still renders the sections; `message("No Python found")` gives lines and no jumps.
- [ ] **Step 3: Run** — Expected: FAIL.
- [ ] **Step 4: Implement** `render.lua` (Builder, wrap, truncate by display width, table alignment by `strdisplaywidth`).
- [ ] **Step 5: Run** — Expected: pass.
- [ ] **Step 6: Commit** `feat: render the document header, generics, ports and instances`.

### Task 5: `render.lua` — FSM, processes, CDC, reset, warnings

**Files:** Modify `render.lua`, `render_spec.lua`

**Interfaces:** Consumes `wave.render`, `mermaid.edges`, and the Builder from Task 4. Adds sections in this order after Instances: `## State machine` (states line, then rows `from → to  when|always`), `## Processes` (each `### label (kind)`, `Sensitivity: …`, description, then per WaveDrom block its drawn lines inside an indented block or the source under `wavedrom:` when `wave.render` returned `nil`; per Mermaid block the edge list or the source under `mermaid:`, caption first when present), `## Clock domains` (domains table, `### Crossings`, `### Dual-clock instances`), `## Reset domains` (processes table, `### Crossings`; the reset value `__none__`/null shows `none`), `## Warnings`. Module-level WaveDrom/Mermaid diagrams go under the description. Process rows jump to `process.line`.

- [ ] **Step 1: Write tests** on `sample_ctrl.json` (FSM with transitions, processes with waveform and Mermaid) and `sample_capture.json` (CDC): the FSM table lists every transition; a process with a waveform contains a line starting with its signal name followed by waveform glyphs; an unsupported waveform shows `wavedrom:` and its source; Mermaid flowchart shows `→` lines, a `sequenceDiagram` shows its source; `cdc == nil` and `reset == nil` omit their sections; crossings show `not synchronised` / `two-flop`; `jumps` has an entry for every process heading.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** the sections.
- [ ] **Step 4: Run** — Expected: pass; also print `render.render(fixture, {width=80}).lines` once and read it for sense.
- [ ] **Step 5: Commit** `feat: render FSM, processes, CDC and reset analysis`.

### Task 6: `runner.lua` and `session.lua`

**Files:** Create `nvim/lua/hdl-autodoc/runner.lua`, `session.lua`, `nvim/test/pyrunner_spec.lua`, `nvim/test/session_spec.lua`, `nvim/test/fixtures/fake-python` (shell script)

**Interfaces:**
- `runner.resolve(opts: {python: string, candidates?: string[][], probe_timeout_ms?: integer}, cb: fun(result))` → `result = {ok=true, command=string[]} | {ok=false, kind="no-python"|"old-python", message=string}`. Empty `python` tries `python3`, `python`, `python3.13`, `python3.12`, `python3.11`, `/opt/homebrew/bin/python3`, `/usr/local/bin/python3` (+ `py -3` on Windows) and takes the first ≥ 3.11; `~` and `$VAR` are expanded (`vim.fn.expand`); a probe (`-c 'import sys; print("%d.%d" % sys.version_info[:2])'`) is killed after `probe_timeout_ms` (default 5000); a successful version is cached, a failure is not. The message names `python` and the versions found, as the VS Code runner's does.
- `runner.run(req: {python_command: string[], script: string, language: "vhdl"|"systemverilog", text: string, support: string[], bus_groups: string|nil, timeout_ms: integer}, cb: fun(result)) -> {cancel: fun()}` → `result = {ok=true, doc=table} | {ok=false, kind="timeout"|"aborted"|"failed"|"bad-json", message=string}`; stdin is the text; `cancel()` kills the process and calls `cb` once with `aborted`.
- `runner.script_path() -> string`: `<plugin>/python/hdl_autodoc/autodoc_json.py` if it exists, else `<plugin>/../src/scripts/hdl_autodoc/autodoc_json.py`; `nil` if neither.
- `session.new(deps)`; `deps = {run(req, cb)->handle, post(result), delay_ms()->integer, set_timer(cb, ms)->handle, clear_timer(handle)}`; `session:update(snapshot)` with `snapshot = {bufnr, language|nil, text, name}`: a non-HDL snapshot posts a note and keeps the last document; otherwise debounces, cancels an older in-flight run, and posts `{kind="doc", doc, bufnr}` or `{kind="error", message}` only for the newest request; `session:flush(snapshot)` runs at once (save, open).

- [ ] **Step 1: Write runner tests** with `fake-python` wrappers (copy the idea of `vscode/test/fixtures/fake-python`): old Python (3.10) is skipped for a newer one; only old → `old-python` message listing versions; nothing → `no-python`; a probe that hangs gives up after the timeout (use 1000–1500 ms; Neovim starts the wrapper in ~0.3 s); a run returns the parsed document; exit ≠ 0 → `failed` with stderr; non-JSON output → `bad-json`; `cancel()` yields exactly one `aborted`; a never-ending run → `timeout`.
- [ ] **Step 2: Write session tests** with fake deps: three rapid updates → one run for the newest text; an older result arriving after a newer request is not posted; a non-HDL snapshot posts a note and does not run; an error is posted, and the next update retries; `flush` skips the delay.
- [ ] **Step 3: Run** — Expected: FAIL.
- [ ] **Step 4: Implement** both modules (`vim.system`, `vim.uv.new_timer`); `session` has no `vim` call except through `deps`.
- [ ] **Step 5: Run** — Expected: pass.
- [ ] **Step 6: Commit** `feat: Python runner and refresh session`.

### Task 7: `support.lua` — interface definitions and bus groups

**Files:** Create `nvim/lua/hdl-autodoc/support.lua`, `nvim/test/support_spec.lua`

**Interfaces:** Produces `support.find(root: string, deps?) -> string[]` (absolute paths of `.sv`/`.svh` files under `root` that define an `interface`, ignoring comments, the same test as `vscode/src/support.ts` `definesInterface`), capped at 500 files and depth 8, skipping `.git`, `node_modules`, `.venv`, `_build`; results cached per path until the file's mtime changes. `support.project_root(path) -> string` is the nearest ancestor holding `.git` or `docs/hdl_autodoc`, else the file's directory. `support.bus_groups(root) -> string|nil` is `<root>/docs/hdl_autodoc/bus_groups.toml` when it exists.

- [ ] **Step 1: Write tests** in a temp tree: a file defining `interface axis_if;` is found; one with `// interface x` only in a comment is not; `.git` and `node_modules` skipped; cache returns the same answer without re-reading until the mtime changes; `project_root` finds `.git`; `bus_groups` returns nil when absent.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** with `vim.fs.dir`/`vim.uv.fs_stat`.
- [ ] **Step 4: Run** — Expected: pass.
- [ ] **Step 5: Commit** `feat: find interface definitions and bus groups`.

### Task 8: `panel.lua`, `init.lua` — the working plugin

**Files:** Create `nvim/lua/hdl-autodoc/panel.lua`; replace the stub `init.lua`; create `nvim/test/e2e_spec.lua`

**Interfaces:**
- `panel.open(opts)`, `panel.close()`, `panel.is_open()`, `panel.show(result: {lines, spans, jumps})` (writes the scratch buffer `nomodifiable`, `buftype=nofile`, `filetype=hdldoc`, applies spans via `nvim_buf_set_extmark` in a namespace, keeps the cursor line where the content allows), `panel.jump_target(panel_line) -> integer|nil`. `<CR>` in the panel jumps to the source window of the buffer that was documented and sets the cursor to the line; `q` closes. Opening never steals the source window's focus.
- `init.setup(opts)` merges options with defaults, defines the highlights, the commands `HdlDoc` (toggle), `HdlDocRefresh`, `HdlDocExportMarkdown`, and autocmds: `TextChanged`/`TextChangedI` (debounced update), `BufWritePost`/`BufEnter` (flush) on HDL buffers while the panel is open; `VimLeavePre` kills runs. Language by `filetype` (`vhdl`→vhdl; `systemverilog`/`verilog`→systemverilog) with the file extension (`vhd vhdl sv svh v vh`) as the fallback, as `languageOf` does in the extension. `init.export_markdown(path)` is wired in Task 9.

- [ ] **Step 1: Write the e2e test**: `setup({})`; `:edit example_full/src/sample_ctrl.vhd`; `:HdlDoc`; wait (`vim.wait(5000, …)`) until the panel buffer has a line starting `# sample_ctrl`; assert a port name from the file is present; put the cursor on a port row, trigger the `<CR>` mapping, assert the current window is the source window and the line is the port's source line; `:HdlDoc` again closes it; modify the buffer without saving, `:HdlDocRefresh`, assert the new text shows (unsaved text is used); a `no-python` configuration (`python="/nonexistent"`) puts a message in the panel; closing the panel window while a run is in flight raises no error; opening a non-HDL buffer leaves the last document and adds the note line.
- [ ] **Step 2: Run** `make -C nvim test FILTER=e2e` — Expected: FAIL.
- [ ] **Step 3: Implement** panel and init, wiring `runner`, `session`, `support`, `render`.
- [ ] **Step 4: Run all** `make -C nvim test` — Expected: pass. Then open the plugin by hand in Neovim on `example_full/src/sample_ctrl.vhd` and `sample_capture.sv` in Ghostty and look at it; note anything illegible at 80 and 40 columns.
- [ ] **Step 5: Commit** `feat: the HdlDoc panel`.

### Task 9: `markdown.lua` and the golden test

**Files:**
- Create: `nvim/lua/hdl-autodoc/markdown.lua`, `nvim/test/markdown_spec.lua`, `vscode/test/markdown.golden.test.mjs`, `fixtures/markdown/sample_ctrl.md`, `fixtures/markdown/sample_capture.md`
- Modify: `nvim/lua/hdl-autodoc/init.lua` (`export_markdown`)

**Interfaces:** Produces `markdown.render(doc) -> string`, byte-for-byte what `renderMarkdown` in `vscode/src/markdown.ts` returns for the same document (same sections, table escaping `\|`, fence length longer than any backtick run, `__none__` → `none`, trailing newline). `:HdlDocExportMarkdown [file]` writes it for the current HDL buffer (default `<buffer path without extension>.md`, asking before overwriting an existing file) and reports the path.

- [ ] **Step 1: Generate the expected files with the TypeScript renderer** (the reference): a throw-away `node --experimental-strip-types` call of `renderMarkdown` on each fixture JSON, written to `fixtures/markdown/*.md`.
- [ ] **Step 2: Write `vscode/test/markdown.golden.test.mjs`** asserting `renderMarkdown(fixture) === expected` for both fixtures (fails if either side changes alone); run `cd vscode && npm test` — Expected: pass.
- [ ] **Step 3: Write `markdown_spec.lua`**: `markdown.render(fixture) == expected` for both; plus hand-built cases: a `|` in a description is escaped; a description with a run of four backticks gets a five-backtick fence; empty sections omitted; `reset=null` shows `none`.
- [ ] **Step 4: Run** `make -C nvim test FILTER=markdown` — Expected: FAIL.
- [ ] **Step 5: Implement** `markdown.lua` mirroring `markdown.ts` function by function; add the command.
- [ ] **Step 6: Run** — Expected: pass; e2e adds one check that the command writes the file.
- [ ] **Step 7: Commit** `feat: Markdown export, pinned to the VS Code output by a golden test`.

### Task 10: Packaging, README, release notes

**Files:**
- Modify: `nvim/Makefile` (`tarball`), `nvim/README.md`, `README.md`, `RELEASE.md`, `vscode/package.json` + `vscode/package-lock.json` (version), `.github/workflows/ci.yml` (upload the tarball as an artifact), `.gitignore`
- Create: none

**Interfaces:** `make -C nvim tarball` writes `nvim/hdl-autodoc-nvim-<version>.tar.gz` containing `lua/`, `python/hdl_autodoc/*.py` (top-level scripts only, as `vscode/Makefile` stages them), `README.md`; `<version>` is read from `vscode/package.json` (one version for the project). `runner.script_path()` finds the staged copy.

- [ ] **Step 1: Test** — `nvim/test/package_spec.lua` builds the tarball into a temp dir, extracts it, runs headless Neovim with only the extracted tree on the runtime path and asserts `:HdlDoc` works on `example_full/src/sample_ctrl.vhd`. Run — Expected: FAIL.
- [ ] **Step 2: Implement** the Makefile target and ignore rules (`nvim/python/`, `nvim/*.tar.gz`); run — Expected: pass.
- [ ] **Step 3: Docs** — `nvim/README.md`: remove the "planned" banner, add the real install snippet verified by hand, a screenshot-free description of the view, the tarball install, troubleshooting (Python not found, tmux), and the Differences section; main `README.md`: replace "(planned)" section with a short real one and a features-table row; `RELEASE.md`: new top entry `v3.19.0` (the plugin, Markdown golden test, compatibility: nothing existing changes); bump `vscode/package.json` and the lockfile to `3.19.0` so `version.test.mjs` passes.
- [ ] **Step 4: Run everything** — `make -C nvim test`, `cd src && python -m pytest -q`, `ruff check scripts`, `cd vscode && npx tsc --noEmit && npm test`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: package the Neovim plugin; README and v3.19.0 notes`.

### After the last task

Merge, push, tag `v3.19.0` and the GitHub release (with the `.vsix` and the Neovim tarball attached) only on the user's explicit go-ahead.

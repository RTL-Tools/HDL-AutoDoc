# HDL AutoDoc for Neovim — design

## Intent

People who write VHDL and SystemVerilog in Neovim, in a terminal such as Ghostty, should get what the
VS Code extension gives: the documentation of the file they are editing, beside it, live. Today they
would have to run `make doc-html` and open a browser. The point is to see how a module reads as
documentation while writing its comments, without leaving the terminal.

## Decisions already made (by the user)

- **Same functionality as the VS Code extension**, as far as a terminal allows.
- **Inside the terminal**: no browser, no GUI window.
- **As few extra tools as possible.** Python 3.11+ is already required by the project and is the only
  hard requirement. Anything beyond that is optional.
- **Diagrams in tiers.** By default WaveDrom waveforms are drawn as Unicode art, the state machine
  is a transitions table, and Mermaid is shown as source plus (for flowcharts) an edge list. Real
  images through the Kitty graphics protocol (Ghostty supports it) are an optional later extra.
- **The Lua side does the Neovim work; Python does the reading.** The extractors are not rewritten
  in Lua: `autodoc_json.py` stays the single source of truth, so the site, the VS Code panel and
  Neovim cannot disagree.
- Developed on the branch `neovim-plugin`. A separate `nvim/README.md` is written, next to the
  existing `vscode/README.md`.

## Goals

1. `:HdlDoc` opens a read-only documentation split for the current `.vhd`, `.vhdl`, `.sv`, `.svh`,
   `.v` or `.vh` buffer. It shows: header description, generics or parameters, ports (bus groups and
   interface ports), instances, the state machine, every process with its description, waveforms
   and Mermaid diagrams, and clock- and reset-domain analysis.
2. It refreshes as you type (debounced, newest result wins), on save, and when you switch HDL
   buffers. It works on unsaved text. A non-HDL buffer leaves the last document in place and says so.
3. `<CR>` on a port, generic, instance or process jumps to its source line, like clicking a row in VS Code.
4. `:HdlDocExportMarkdown` writes the same Markdown the VS Code extension writes (diagrams as fenced
   `mermaid`, `wavedrom` and `dot` blocks).
5. Colours come from highlight groups that link to the user's colour scheme: no theme settings.
6. SystemVerilog interface ports are resolved from interface definitions found in the project, and
   custom port groups from `docs/hdl_autodoc/bus_groups.toml`, as the site build and the extension do.
7. Settings: Python path (empty means find one: 3.11+, `~` and `$VAR` expanded, version probes with a
   timeout), refresh delay, split width and side.

## Non-goals (v1)

- HTML and PDF export. They need the browser-rendered page; Neovim users have the Markdown export and
  `make doc-html`. *(My call: the user asked for the same functionality, but these two need a
  JavaScript engine, which contradicts "as few extra tools as possible".)*
- Inline images. Planned as an optional phase after v1, only if image support and `dot` or a headless
  browser are found.
- Editing the documentation from the panel; a Telescope picker; hover or go-to-definition.
- A Lua reimplementation of the extractors, or a tree-sitter parser.
- Supporting Neovim older than 0.10 (needs `vim.system`) or Vim.

## Architecture

```
nvim/
  lua/hdl-autodoc/
    init.lua        setup(opts), commands, autocmds
    runner.lua      finds Python 3.11+, runs autodoc_json.py with the buffer text on stdin
    session.lua     debounce, newest-wins, "which buffer is shown", error/note states
    render.lua      Document (decoded JSON) -> lines + highlight spans + jump targets
    wave.lua        WaveDrom JSON -> Unicode lines
    mermaid.lua     flowchart edge list; everything else stays as source
    markdown.lua    Document -> Markdown (same output as vscode/src/markdown.ts)
    panel.lua       scratch buffer, split window, <CR> jump, highlights
  README.md
  test/             headless-Neovim tests and fixtures
```

- **Data.** `autodoc_json.py --language vhdl|systemverilog [--support FILE ...] [--bus-groups FILE]`
  reads the text on stdin and prints the JSON document (its shape is in that file's docstring and does
  not change). The plugin runs it with `vim.system`; a newer edit kills the older run.
- **Where the scripts are.** A plugin manager clones the whole repository, so the plugin finds
  `src/scripts/hdl_autodoc/` relative to its own path. A release tarball carries a staged copy in
  `nvim/python/hdl_autodoc/`, as the `.vsix` does. The `python` option only names the interpreter.
- **Rendering.** `render.lua` returns plain lines plus a list of `{line, col_start, col_end, group}`
  highlight spans and a `line → source line` jump table, so it is testable without a window.
- **Waveforms.** Subset: `p n P N` clocks, `0 1 x z`, `=` and `2-9` data (labels from `data`), `.`
  continuation, `|` gaps, `l h u d`. Phase, period and unknown characters fall back to showing the
  WaveDrom source for that signal. Groups are flattened with a name prefix.
- **Flowcharts.** `flowchart`/`graph` `A --> B` and `A -->|label| B` become `A → B (label)` lines;
  other diagram types stay as fenced source. A box-drawing layout is a later extra.
- **Install.** `require("hdl-autodoc").setup({})`, called explicitly (the commands are defined there, not
  in a `plugin/` file), so a subdirectory plugin works with lazy.nvim's
  `config = function(p) vim.opt.rtp:append(p.dir .. "/nvim") require("hdl-autodoc").setup() end`.

## Parity with the VS Code extension

| VS Code | Neovim v1 |
|---|---|
| Panel beside the editor, live refresh, unsaved text | Split beside the buffer, same behaviour |
| Click row → jump to line | `<CR>` on the row |
| Header, generics, ports with groups, instances, FSM, processes, CDC, reset | Same sections, as text |
| WaveDrom and Mermaid drawn | Unicode waveforms; FSM table; Mermaid edge list or source |
| Theme follows editor | Highlight groups linked to the colour scheme |
| Export HTML, Markdown, PDF | Markdown only |
| `hdlAutodoc.pythonPath`, `refreshDelayMs`, `browserPath` | `python`, `refresh_delay_ms`; no browser setting |

## Testing

- Lua unit tests run in headless Neovim (`nvim --headless -u NONE -l test/run.lua`) with a small
  in-repo runner: no plenary, no other plugin.
- `render`, `wave`, `mermaid`, `markdown` are pure functions tested on fixture JSON documents.
- `runner` is tested with the same fake-Python wrapper approach as the extension (old, missing and
  hanging interpreters).
- **Drift guard:** a golden test feeds one JSON document (from `example_full`) to both `markdown.ts` and
  `markdown.lua` and requires identical text.
- One end-to-end test runs the real `autodoc_json.py` on `example_full/src/sample_ctrl.vhd` and opens
  the panel.
- CI: a `neovim` job (stable and nightly) beside the existing `vscode` job.

## Phases

1. **Skeleton:** runner, session, panel, `:HdlDoc`, header + generics + ports + instances, jump, refresh.
2. **Full text document:** FSM table, processes, CDC, reset, bus groups, interface support files.
3. **Diagrams:** Unicode waveforms, flowchart edge lists.
4. **Export and release:** Markdown export with the golden test, `nvim/README.md` finished, CI job,
   release tarball (v3.19.0).
5. *(Later, optional)* images over the Kitty graphics protocol with `dot` or headless Chrome.

## Risks

- **Waveform fidelity.** Unicode art is an approximation of WaveDrom (no analogue levels, no arrows
  or edges). The README says so; unknown input shows its source instead of a wrong picture.
- **Subdirectory install.** Plugin managers expect the plugin at the repository root. The explicit
  `setup()` plus an `rtp` line works with lazy.nvim; if it proves awkward a thin separate repository
  can publish `nvim/`.
- **Two Markdown writers** (TypeScript and Lua) can drift; the golden test is the guard.
- **Wide terminals only.** Port tables need roughly 80 columns; narrow splits wrap, which the render
  step must handle (truncate descriptions, keep names whole).

## Left to the builder

Exact highlight group names, key mappings beyond `<CR>` and `q`, how the interface-definition search
is bounded (project root, depth, file count), how the wave glyphs are chosen (the set must render in
Ghostty's default font), and the panel's layout of tables at narrow widths.

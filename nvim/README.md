# HDL AutoDoc for Neovim

> **Status: planned, not yet usable.** This describes what the plugin will do; the design is in
> [`docs/superpowers/specs/2026-11-06-neovim-plugin-design.md`](../docs/superpowers/specs/2026-11-06-neovim-plugin-design.md).
> The command names and options below may still change. For VS Code see [`../vscode/README.md`](../vscode/README.md).

A live documentation view of the VHDL or SystemVerilog buffer you are editing, in a split beside it,
inside the terminal. It shows what the [HDL AutoDoc](https://github.com/RTL-Tools/HDL-AutoDoc) site will
show for that module, as you type:

- the header description, generics or parameters, and ports (with bus groups and interface ports),
- instances and the state machine (as a transitions table),
- every process, with its description, **WaveDrom** waveforms drawn as Unicode and **Mermaid** diagrams
  as an edge list or source,
- clock-domain and reset analysis (crossings, synchronisers, dual-clock instances).

It reads the source with the same extractors as the site build and the VS Code extension, so all three
read your comments the same way. It works on unsaved text, and `<CR>` on a port, generic, instance or
process jumps to its line.

## Use

1. Open a `.vhd`, `.vhdl`, `.sv`, `.svh`, `.v` or `.vh` file.
2. `:HdlDoc` opens the documentation split (and closes it if it is open).
3. `<CR>` on a row jumps to that line in the source; `q` closes the split.
4. `:HdlDocExportMarkdown [file]` saves the same documentation as Markdown, with diagrams inserted as
   fenced `mermaid`, `wavedrom` and `dot` blocks.

The view refreshes after you stop typing, on save, and when you switch HDL buffers. A buffer that is
not HDL leaves the last document in place and says so.

## Requirements

| Tool | For | Notes |
|---|---|---|
| Neovim 0.10 or newer | the plugin | uses `vim.system` |
| Python 3.11 or newer | reading the source | standard library only: nothing to `pip install` |

Nothing else is needed. Real images for the diagrams (through the Kitty graphics protocol, which
Ghostty supports) are a possible later extra.

## Install

With [lazy.nvim](https://github.com/folke/lazy.nvim). The plugin lives in the `nvim/` folder of the
repository, so its path is added explicitly:

```lua
{
  "RTL-Tools/HDL-AutoDoc",
  ft = { "vhdl", "systemverilog", "verilog" },
  config = function(plugin)
    vim.opt.rtp:append(plugin.dir .. "/nvim")
    require("hdl-autodoc").setup({})
  end,
}
```

## Options

| Option | Default | |
|---|---|---|
| `python` | `""` | Python 3.11+ used to read the source. Empty: look for one (`python3`, `python`, `python3.13`… and Homebrew's), skipping any older than 3.11. `~` is expanded |
| `refresh_delay_ms` | `400` | Pause after the last keystroke before the view refreshes |
| `width` | `80` | Width of the split, in columns |
| `side` | `"right"` | `"right"` or `"left"` |

A SystemVerilog file that uses interfaces (`axis_if.slave s_axis`) is documented with those interfaces
when the project holds the `interface` definition. Custom port groups are read from
`docs/hdl_autodoc/bus_groups.toml` in the project root, as the site build does.

## Differences from the VS Code extension

- Waveforms are Unicode art, an approximation of WaveDrom: no analogue levels, arrows or edges. A
  signal the drawing cannot handle shows its WaveDrom source instead.
- The state machine is a table; Mermaid flowcharts are an edge list, and other Mermaid types are
  shown as source.
- Markdown export only: no HTML or PDF export (use the VS Code extension or `make doc-html`).
- Colours come from your colour scheme through highlight groups.

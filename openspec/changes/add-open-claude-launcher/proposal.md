## Why

Today the closest thing to a "work + monitor" workspace is `claude-monitor side claude`: it docks the 44-col monitor on the right and runs a single Claude Code session on the left. People who want a couple of Claude sessions plus scratch terminals next to the monitor have to build that grid by hand every time. A one-shot launcher that opens the monitor plus a sensible mix of Claude consoles and terminals removes that friction.

## What Changes

- Add a standalone `open-claude [n]` command (typed exactly as `open-claude 2` — no `claude-monitor` prefix) that opens the monitor (the existing 44-col tmux dock, unchanged) plus `n` panes — a mix of Claude Code consoles and plain terminals — tiled in the remaining screen area.
- `n` accepts `1`–`4`; `open-claude` with no number defaults to `n=1`.
- Layouts for the pane area (the screen minus the dock), with Claude on top and terminals below:
  - `1`: a single full-height Claude console (same as `side claude` today).
  - `2`: a Claude console and a terminal in two columns side by side.
  - `3`: a top row of two Claude consoles and a full-width terminal below.
  - `4`: an even 2×2 grid — two Claude consoles on top, two terminals below.
- Reuse the existing dock invocation (`--dock`, full truecolor, scene/fps flags) so no new rendering path is introduced.
- Validate `n` (reject values outside 1–4) and guard against terminals too narrow to fit the requested split.

## Capabilities

### New Capabilities
- `open-claude-launcher`: launching the monitor dock alongside a configurable 1–4 pane grid of Claude Code consoles and terminals via a single command, including layout selection, argument handling, and tmux session lifecycle.

### Modified Capabilities
<!-- None: the existing `side`/`window` launch behavior is not covered by any spec in openspec/specs/, so there are no spec-level requirements to modify. -->

## Impact

- **Code**: `claude_monitor.py` — an `open_claude(n, scene)` launch function alongside the existing `run_side`, dispatched when the program is invoked as `open-claude` (via `argv[0]` basename detection in `main()`). Shares helpers (`WIDTH`, `--dock` self-command, mouse mode) with `run_side`. The `claude-monitor open-claude ...` form is not exposed.
- **Dependencies**: tmux (already required for `side`) and the `claude` CLI on `PATH` for the consoles. No new Python dependencies.
- **Distribution**: the Homebrew formula installs an `open-claude` entry point (a symlink/wrapper to `claude_monitor.py`) on `PATH` so `open-claude 2` works directly; manual installs add the same symlink.
- **Docs**: README "Run" section gains the `open-claude [n]` usage.

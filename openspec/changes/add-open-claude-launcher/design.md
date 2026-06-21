## Context

`claude_monitor.py` is a single-file Python TUI. Its `run_side(cmd, scene)` already does the core trick this change generalizes: it splits the terminal with tmux, pins a fixed-width dock (`WIDTH`, currently 44 cols) on the right via `split-window -h -l WIDTH -d <self --dock>`, runs a command on the left, and ties their lifetimes together. It handles two entry states — already inside tmux (`$TMUX` set → `split-window` in the current window) and outside tmux (`os.execvp` into a fresh `tmux new-session ...; tmux kill-session`) — and enables mouse mode on the new session.

This change adds `open-claude [n]`, which keeps that exact dock but tiles the left "console area" with `n` Claude consoles (1–4) instead of one. The hard-won constraint from prior work (see the freeze notes in the code around the `--dock`/truecolor path) is that the dock must keep running full truecolor at full fps under tmux; the 256-color path is what froze sessions. The design avoids any new rendering path by reusing the unchanged `--dock` self-invocation for the dock pane.

## Goals / Non-Goals

**Goals:**
- One command, `open-claude [n]`, that opens the monitor dock plus `n` panes (Claude consoles on top, terminals below) laid out as: 1 = one Claude; 2 = Claude + terminal columns; 3 = two Claude over one terminal; 4 = 2×2 (two Claude over two terminals). Default `n=1`.
- Reuse `run_side`'s dock invocation, mouse mode, tmux-inside/outside handling, and lifetime coupling — no duplicated or divergent dock logic.
- Validate `n` and refuse to create unusably narrow panes.

**Non-Goals:**
- Configurable/custom layouts beyond the four fixed arrangements, or `n > 4`.
- Per-console working directories, branch/repo selection, or passing arguments through to each `claude` invocation. (Possible follow-ups; out of scope here.)
- Changing the dock's width, rendering, scenes, or fps behavior.
- A non-tmux fallback grid (gnome-terminal multi-window). `side` only does single-window non-tmux; `open-claude` requires tmux for the grid.

## Decisions

### Decision: `open-claude` is a standalone command via multi-call dispatch

`open-claude n` is the *only* invocation — no `claude-monitor` prefix, no subcommand. Implement it as a multi-call entry point (the busybox pattern): ship `open-claude` as a symlink (or a tiny `exec` wrapper) to `claude_monitor.py`, and at the top of `main()` detect `os.path.basename(sys.argv[0])`. When the basename is `open-claude`, parse the remaining argv as the console count (optional integer, default 1) and call `open_claude(n, scene)`; otherwise fall through to the existing `claude-monitor` dispatch (`side`/`window`/`--once`/...). `open_claude(n, scene)` mirrors `run_side`'s structure and shares `WIDTH`, the `--dock` self-command builder, and mouse-mode setup.

Rationale: keeps everything in the single file while giving the user a real `open-claude` command on `PATH`. No second script to maintain; the symlink is the whole "install." The Homebrew formula (and manual install) creates the `open-claude` symlink next to `claude-monitor`.

*Alternatives considered:* (a) a `claude-monitor open-claude n` subcommand — rejected, the user explicitly does not want the prefix; (b) a separate standalone `open-claude` Python script — rejected as duplicate logic to keep in sync with `claude_monitor.py`. The symlink dispatch avoids both.

### Decision: Build the grid with explicit tmux splits, dock first

Construct panes in this order so proportions are predictable and the dock stays pinned right:

1. Establish the controlling pane running Claude console #1 (the session's primary pane — see lifetime decision).
2. Split off the dock on the right: `split-window -h -l WIDTH -d <self --dock ...>`. The remaining left area is now the console area.
3. Subdivide the console area (operating on console #1's pane):
   - `n=1`: no further splits.
   - `n=2`: `split-window -h` on the console pane → two columns.
   - `n=3`: `split-window -v` on the console pane (creates the full-width bottom = console #3), then `split-window -h` on the top pane → top row of two (consoles #1, #2).
   - `n=4`: `split-window -v` then `split-window -h` on each of the two rows → 2×2.
4. Run the role-appropriate command in each pane (see "Pane roles"): `claude` in the top-row pane(s), the user's shell in the remaining (terminal) pane(s).

For `n=4`, after the manual splits, normalize with `select-layout tiled` (scoped so it does not disturb the dock — the dock is pinned by `-l`, and tiled re-tiling is applied before/while the dock width is reasserted, or the even split is achieved with `even-horizontal`/`even-vertical` on the rows). Rationale: explicit splits give exact control over the 2-over-1 case that a single named layout cannot express; named layouts are used only where they match (even quarters). *Alternative considered:* drive everything via `select-layout` presets — rejected because none matches the `n=3` two-over-one shape.

### Decision: Lifetime governed by the primary console, matching `side`

The session is created so console #1 is the controlling process (as in `run_side`, where the inner command governs and the dock is killed when it exits). When console #1 ends, the whole session — dock and other consoles — tears down. Closing consoles #2–#4 closes only their own pane. Rationale: this is exactly the mental model `side` already establishes (quit your work, the dock goes too), so behavior is consistent and there is no new teardown machinery. *Alternative considered:* keep the session alive until every console exits — rejected as more surprising and requiring extra hooks (`remain-on-exit`/pane-count watching) for little gain.

### Decision: Reuse the `--dock` self-command verbatim

The dock pane uses the same self-invocation string `run_side` builds (`python claude_monitor.py --dock [--scene S] [--fps N]`). Rationale: guarantees the dock renders through the proven truecolor full-fps path and inherits scene/fps forwarding for free; no risk of reintroducing the 256-color freeze. Factor the self-command construction out of `run_side` into a small helper shared by both.

### Decision: Validation and narrow-terminal guard

Parse `n`; if it is non-integer or outside 1–4, print a one-line error and exit before touching tmux. Before splitting, compute the console-area width = terminal columns − `WIDTH`, and the per-column width for the requested layout (2 columns for `n` in {2,3,4}). If a column would fall below a usable minimum (e.g. ~`WIDTH`-ish, value picked during implementation), warn and either refuse or fall back to a smaller `n`/single console — chosen so the user never lands in unusable slivers. Rationale: tmux will happily create 3-column-wide panes; guarding up front is friendlier.

### Decision: Pane roles — Claude on top, terminals below

Not every pane runs Claude. Claude consoles fill the top row and plain terminals fill the rest: `n=1` 1 claude; `n=2` claude + terminal; `n=3` 2 claude + 1 terminal; `n=4` 2 claude + 2 terminals. A `_console_roles(n)` map assigns each pane key a role (`claude`/`term`); `_split_grid` launches the matching command per pane. Console #1 (the controlling pane, see lifetime decision) is always a Claude console, so the lifetime semantics are unchanged. Rationale: the user wanted scratch terminals alongside the Claude sessions, not N copies of Claude; this keeps `open-claude 4` to two Claude sessions (less quota draw) while still filling the screen.

### Decision: `claude` binary with shell fallback

Claude panes run `claude` (via `shutil.which("claude")`); terminal panes run `$SHELL`. If `claude` is absent, the Claude panes run `$SHELL` too and the launcher emits a notice. Rationale: the launcher is still useful (layout + dock) without Claude installed, and the notice avoids silent confusion.

## Risks / Trade-offs

- **Many panes on a small screen are cramped** → narrow-terminal guard refuses or downshifts before creating unusable panes; document a recommended minimum terminal width for `n=4`.
- **`select-layout tiled` could fight the pinned dock width** → reassert the dock width (`-l WIDTH`, or a `resize-pane` after tiling) so the dock keeps its 44 cols; verify visually for each `n`. The existing dock already self-corrects width on resize via `resize-pane`, which helps.
- **Lifetime tied to console #1 may surprise multi-session users** (closing the first console closes all) → documented explicitly; matches existing `side` semantics so it is at least consistent.
- **`open-claude` name collision** with an existing user alias/binary on `PATH` → unlikely (it is a specific name); the formula installs it like any other command, and a user who has a conflict can rename/unlink the symlink. There is no prefixed fallback by design, since the whole point is the bare `open-claude` name.
- **Multiple `claude` sessions multiply token/quota usage** → no mitigation needed in code, but worth a line in the README so users understand `open-claude 3`/`4` start two Claude sessions at once (the terminals draw no quota).

## Migration Plan

Purely additive — no existing command changes. Add the `argv[0]` dispatch and `open_claude()` to `claude_monitor.py`, create the `open-claude` symlink in the brew formula (and in the manual-install instructions), and update the README "Run" section. Rollback is removing the dispatch branch and the symlink; nothing else depends on it.

## Open Questions

- Exact usable minimum column width for the narrow guard, and whether the guard refuses vs. silently downshifts `n` — pin during implementation against real terminals.
- Should `open-claude` forward extra trailing args to each `claude` invocation (e.g. a starting prompt or `--continue`)? Deferred unless requested.

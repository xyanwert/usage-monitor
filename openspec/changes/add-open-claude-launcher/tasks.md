## 1. Standalone command dispatch

- [x] 1.1 At the top of `main()`, detect `os.path.basename(sys.argv[0]) == "open-claude"`; when matched, read an optional trailing integer count (default `1`) and call `open_claude(n, scene)`, then return (do not fall through to the `claude-monitor` dispatch).
- [x] 1.2 Validate the count: reject non-integers and values outside 1–4 with a clear one-line error and exit before any tmux call.
- [x] 1.3 Update the module docstring / usage text to document `open-claude [n]` (typed directly, e.g. `open-claude 2`) and the four layouts.

## 2. Shared dock helper

- [x] 2.1 Factor the `--dock` self-command string construction (executable + script path + `--dock` + forwarded `--scene`/`--fps`) out of `run_side` into a small helper.
- [x] 2.2 Call the helper from `run_side` (no behavior change) to confirm the refactor is non-breaking.

## 3. `open_claude(n, scene)` layout builder

- [x] 3.1 Implement `open_claude(n, scene)` mirroring `run_side`'s inside-tmux (`$TMUX` set) and outside-tmux (`tmux new-session` via `os.execvp`) entry paths, with mouse mode enabled on a new session.
- [x] 3.2 Choose the console command: `claude` when `shutil.which("claude")` is set, else `$SHELL`, surfacing a notice when `claude` is missing.
- [x] 3.3 Establish console #1 as the controlling/primary pane and pin the dock on the right with `split-window -h -l WIDTH -d <dock self-command>`.
- [x] 3.4 Implement the per-`n` splits of the console area: `n=1` none; `n=2` one `-h`; `n=3` `-v` then `-h` on the top (two-over-one); `n=4` `-v` then `-h` on each row (2×2), normalizing to even quarters.
- [x] 3.5 Run the role-appropriate command in each pane per `_console_roles`: Claude on the top row, the user's shell in the remaining (terminal) panes — `n=1` 1 claude; `n=2` claude + terminal; `n=3` 2 claude + terminal; `n=4` 2 claude + 2 terminals.
- [x] 3.6 Reassert the dock's `WIDTH` after any layout normalization so it keeps its fixed width for every `n`.

## 4. Narrow-terminal guard

- [x] 4.1 Before splitting, compute console-area width (terminal columns − `WIDTH`) and per-column width for the requested layout.
- [x] 4.2 If a column would fall below a usable minimum, warn and refuse (or downshift `n`) instead of creating unusable panes; pin the chosen minimum value against a real terminal.

## 5. Lifetime coupling

- [x] 5.1 Ensure the session is structured so console #1 governs lifetime: when it exits, the dock and other consoles tear down with the session (matching `side`); verify no orphaned dock pane remains.

## 6. `open-claude` entry point on PATH

- [x] 6.1 Create the `open-claude` command as a symlink (or tiny `exec` wrapper) to `claude_monitor.py` so `argv[0]` resolves to `open-claude`; add it to the manual-install instructions in the README.
- [x] 6.2 Update the Homebrew formula to install/link `open-claude` on `PATH` next to `claude-monitor`.
- [x] 6.3 Verify `open-claude 2` works as a bare command (found on `PATH`, no `claude-monitor` prefix) after both a brew install and a manual symlink install.

## 7. Verification & docs

- [x] 7.1 Manually verify each `n` (1–4): correct console layout, dock pinned at `WIDTH` on the right, mouse-click focus works, both inside and outside an existing tmux session.
- [x] 7.2 Verify validation (out-of-range and non-numeric counts) and the narrow-terminal guard behave as specified.
- [x] 7.3 Verify teardown: exiting console #1 closes the dock and remaining consoles with no orphans.
- [x] 7.4 Update the README "Run" section with `open-claude [n]` usage and a note that `open-claude 4` starts four Claude sessions (quota impact).

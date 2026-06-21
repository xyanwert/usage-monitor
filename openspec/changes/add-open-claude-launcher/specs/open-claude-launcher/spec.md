## ADDED Requirements

### Requirement: Launch monitor dock with an N-pane grid

The system SHALL provide a standalone `open-claude [n]` command, invoked by typing `open-claude` directly (with no `claude-monitor` prefix and no subcommand), that opens the monitor dock and `n` panes in a single action, where `n` is an integer from 1 to 4. The panes SHALL be a mix of Claude Code consoles and plain terminals (see "Pane layout per count"). The monitor dock SHALL occupy a fixed-width column on the right of the screen, and the remaining area (the "console area") SHALL be tiled with exactly `n` panes.

#### Scenario: Default pane count

- **WHEN** the user runs `open-claude` with no number
- **THEN** the system launches the monitor dock plus exactly one full-height Claude console (equivalent to today's `side claude`)

#### Scenario: Explicit single console

- **WHEN** the user runs `open-claude 1`
- **THEN** the system launches the monitor dock plus one full-height Claude console occupying the entire console area, with no terminal pane

#### Scenario: Invoked as a standalone command

- **WHEN** the user types `open-claude 2` directly at the shell (no `claude-monitor` prefix)
- **THEN** the command is found on `PATH` and runs the launcher, exactly as if it were any other installed program

### Requirement: Pane layout per count

The console area SHALL be tiled according to the requested count, with Claude consoles filling the top row and plain terminals filling the rest: `1` is one full-height Claude console; `2` is a Claude console and a terminal in two side-by-side columns; `3` is a top row of two Claude consoles above one full-width terminal; `4` is an even 2×2 grid of two Claude consoles on top and two terminals below.

#### Scenario: Two panes side by side

- **WHEN** the user runs `open-claude 2`
- **THEN** the console area is split into two columns side by side: a Claude console and a plain terminal

#### Scenario: Three panes, two over one

- **WHEN** the user runs `open-claude 3`
- **THEN** the console area shows a top row of two side-by-side Claude consoles and a single full-width terminal beneath them

#### Scenario: Four panes in a grid

- **WHEN** the user runs `open-claude 4`
- **THEN** the console area is divided into an even 2×2 grid: two Claude consoles on top and two terminals below

### Requirement: Monitor dock is preserved unchanged

The monitor dock launched by `open-claude` SHALL use the same dock rendering path as the existing `side` command (the `--dock` self-invocation, full truecolor, and any forwarded `--scene`/`--fps` options), pinned to its fixed width on the right regardless of `n`.

#### Scenario: Dock pinned on the right

- **WHEN** the user runs `open-claude` with any valid `n`
- **THEN** the monitor dock appears as a fixed-width pane on the right edge and the `n` consoles fill the area to its left

#### Scenario: Scene and fps forwarded to the dock

- **WHEN** the user runs `open-claude` together with `--scene` or `--fps` options
- **THEN** those options are applied to the dock exactly as they are under the `side` command

### Requirement: Argument validation

The system SHALL accept only integer counts in the range 1–4. Any other value SHALL be rejected with a clear error message, and no panes SHALL be created.

#### Scenario: Count out of range

- **WHEN** the user runs `open-claude 5` (or `0`, or a negative number)
- **THEN** the system prints an error stating the count must be between 1 and 4 and exits without opening any panes

#### Scenario: Non-numeric count

- **WHEN** the user passes a non-numeric value as the count
- **THEN** the system prints an error explaining the expected value and exits without opening any panes

### Requirement: Narrow-terminal guard

Before splitting, the system SHALL verify the console area is wide enough to host the requested number of columns at a usable minimum width. If it is not, the system SHALL warn the user rather than create unusably narrow panes.

#### Scenario: Terminal too narrow for the requested split

- **WHEN** the user requests a count whose columns would each fall below the usable minimum width for the current terminal size
- **THEN** the system warns that the terminal is too narrow for that many panes and does not produce unusable panes

### Requirement: tmux session lifecycle

`open-claude` SHALL build its layout with tmux, reusing the existing behavior of the `side` command: it SHALL work whether or not the user is already inside tmux, enable mouse mode so panes can be focused by clicking, and tie the dock's lifetime to the session so the dock is torn down when the session ends.

#### Scenario: Launched from outside tmux

- **WHEN** the user runs `open-claude` from a shell that is not inside tmux
- **THEN** the system starts a new tmux session containing the console grid and the dock, with mouse mode enabled

#### Scenario: Launched from inside tmux

- **WHEN** the user runs `open-claude` from within an existing tmux session
- **THEN** the system builds the console grid and dock in the current window rather than starting a nested session

#### Scenario: Dock torn down with the session

- **WHEN** the primary Claude console session ends
- **THEN** the monitor dock is closed along with it, leaving no orphaned dock pane

### Requirement: Pane command fallback

Claude console panes SHALL run the `claude` CLI when it is available on `PATH`; terminal panes SHALL run the user's shell. If `claude` is not found, the system SHALL fall back to the user's shell for the Claude panes too so the layout still opens, and SHALL make the missing CLI evident to the user.

#### Scenario: Claude CLI present

- **WHEN** `claude` is on `PATH` and the user runs `open-claude n`
- **THEN** each Claude console pane starts a Claude Code session and each terminal pane starts the user's shell

#### Scenario: Claude CLI missing

- **WHEN** `claude` is not on `PATH`
- **THEN** the system opens the Claude panes running the user's shell instead and surfaces that the `claude` CLI was not found

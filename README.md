# BURNOUT

A terminal monitor for your Claude plan limits that is also a fire.

Burnout watches the same usage numbers `/usage` shows in Claude Code — your
5-hour session, weekly, and model-specific limits — and turns them into a
live pixel animation in a slim 44-column dock next to your work. No API key,
no dependencies, one Python file.

## The scenes

Press `s` to cycle. All three are driven by the same live data.

**🔥 BURNOUT (fire)** — a Doom-fire that grows with your session usage and
gusts when claude is actively working. Hit the limit and it burns out: ash,
smoke, pulsing embers, and a countdown — then it reignites on reset.

**⌨ TOKENFALL** — a prompt line "generates" text (cliché AI included), each
line tokenizes into colored pills, and the tokens fly up into the context
wall: an archive of everything you've spent that closes in as your quota
fills. At 100% the last gap seals and freezes — CONTEXT FULL — and on reset
the whole archive avalanches away.

**👾 INVADERS** — your quota is a fleet. Every token you burn, the ship
shoots one down with a tiny pixel rocket, so *winning means hitting the rate
limit*: GAME OVER, then reinforcements beam in on the real countdown. The
march panics as the fleet thins; a saucer crosses whenever fresh data lands.
(Original sprites — HD quadrant rendering at 88px virtual resolution.)

## Install

### Homebrew (macOS & Linux) — recommended

```bash
brew install xyanwert/tap/claude-monitor
```

That single command adds the tap and pulls in everything it needs — Python 3
and `tmux` (for the split-screen dock). It's shorthand for:

```bash
brew tap xyanwert/tap
brew install claude-monitor
```

**Update to the latest release:**

```bash
brew update && brew upgrade claude-monitor
```

**Track the bleeding edge** (latest `main`, rebuilt from source):

```bash
brew install --HEAD xyanwert/tap/claude-monitor
# later: brew upgrade --fetch-HEAD claude-monitor
```

**Uninstall:** `brew uninstall claude-monitor` (and `brew untap xyanwert/tap`
to forget the tap entirely).

### Manual (any platform)

```bash
git clone https://github.com/xyanwert/usage-monitor.git
cd usage-monitor
chmod +x claude_monitor.py
ln -s "$PWD/claude_monitor.py" ~/.local/bin/claude-monitor   # if ~/.local/bin is on your PATH
ln -s "$PWD/claude_monitor.py" ~/.local/bin/open-claude       # enables `open-claude [n]`
```

The second symlink installs `open-claude`: the same script, but launched under
that name it opens the dock plus a grid of Claude consoles (see Run).

Requirements: Python 3.8+, a truecolor terminal, and a logged-in
[Claude Code](https://claude.com/claude-code) (that's where the OAuth token
comes from). `tmux` is needed only for the `side` split-screen dock — Homebrew
installs it for you.

## Run

```bash
claude-monitor                   # full-window monitor
claude-monitor side claude       # the good stuff: claude left, fire right
open-claude                      # dock + one claude console (same as `side claude`)
open-claude 2                    # dock right, claude + a terminal side by side
open-claude 4                    # dock right, 2 claude over 2 terminals (2x2)
claude-monitor --scene invaders  # pick your poison: fire | tokens | invaders | cube
claude-monitor --once            # print usage once and exit
```

`side` splits the terminal with tmux (44-col dock on the right), ties the
lifetimes together (quit claude and the dock goes too), and turns on mouse
mode — click a pane to focus it.

`open-claude [n]` is the multi-pane version: the same 44-col dock on the
right, and `n` panes (1–4, default 1) tiled in the rest — a mix of Claude
consoles and plain terminals, with Claude filling the top row:
**1** one Claude · **2** Claude + a terminal (two columns) · **3** two Claude
on top, a full-width terminal below · **4** two Claude on top, two terminals
below (2×2). It reuses `side`'s dock, mouse mode, and lifetime coupling:
quitting the **first** Claude console tears the whole layout down (closing any
other pane just closes that pane). It refuses to split if the terminal is too
narrow for usable panes. Note `open-claude 4` starts **two** Claude sessions at
once — they both draw on the same plan limits, which is exactly what the dock
is there to show you. (`--scene`/`--fps` tune the dock as usual.)

Inside tmux the dock runs full truecolor at full frame rate, same as a plain
window. (It only eases off if a write genuinely backs up — a safety net that
shouldn't trigger in practice.) Tune it with `--fps N` if you like; `⌘D`-style
native terminal splits work great too.

## Keys

| key | action |
|---|---|
| `s` | switch scene |
| `t` | reset times: auto-alternate / "resets at" / "in 2h57m" |
| `b` | demo the burnout / context-full / game-over arc |
| `r` | refresh now |
| `q` | quit |

## How it gets data

It reads Claude Code's own OAuth token locally (`~/.claude/.credentials.json`,
or the Keychain on macOS) and polls Anthropic's usage endpoint — the same
numbers `/usage` shows. The token never goes anywhere except to Anthropic.

Niceties: launches paint instantly from a local snapshot cache; multiple
monitors on one machine share a single poll between them; polling speeds up
near the limit and around resets; 429s back off politely. The status row
alternates burn rate with your live session's model · effort · busy state
(busy also makes the fire gusty — you can see claude thinking).

## macOS

Works with two notes: credentials are read from the Keychain automatically,
and Apple's built-in Terminal.app doesn't support truecolor — use iTerm2,
Ghostty, kitty, or WezTerm. (If you hit a Keychain edge case, please open an
issue.)

## License

MIT

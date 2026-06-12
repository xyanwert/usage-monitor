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

## Quick start

Requirements: Python 3.8+, a truecolor terminal, a logged-in
[Claude Code](https://claude.com/claude-code) (that's where the OAuth token
comes from), and optionally `tmux` for the split-screen dock.

```bash
git clone https://github.com/xyanwert/usage-monitor.git
cd usage-monitor
ln -s "$PWD/claude_monitor.py" ~/.local/bin/claude-monitor
chmod +x claude_monitor.py

claude-monitor                   # full-window monitor
claude-monitor side claude       # the good stuff: claude left, fire right
claude-monitor --scene invaders  # pick your poison: fire | tokens | invaders
claude-monitor --once            # print usage once and exit
```

`side` splits the terminal with tmux (44-col dock on the right), ties the
lifetimes together (quit claude and the dock goes too), and turns on mouse
mode — click a pane to focus it.

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

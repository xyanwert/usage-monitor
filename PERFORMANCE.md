# Performance & rendering notes — reference for a native/macOS port

Everything the Python monitor learned the hard way about drawing a live
animation fast and smooth — especially inside a tmux dock on macOS/iTerm2 —
written so a **separate implementation (a native macOS app, a Rust/Swift TUI,
whatever) can implement the same wins without re-walking the dead ends.**

All line numbers are into `claude_monitor.py` at the time of writing; treat them
as pointers, not contracts. Commit hashes name the change that introduced each
item so you can `git show` the full diff.

---

## TL;DR — the known-good end state

After a week of wrong turns (see "Dead ends" at the bottom), the shipped config
for the tmux dock is:

- **Truecolor (24-bit), always.** Never fall back to 256/indexed color under
  tmux — that was the actual freeze.
- **Full frame rate** (default 28–30 fps). No cap.
- **Row-diff output** — send only the rows that changed since the last frame.
- **One safety net:** a *bidirectional* frame-rate adapter — eases off hard when
  a write blocks (pty backed up), climbs back toward target once writes are fast
  again. (It used to only ratchet down; that permanently decayed the flame after
  a single transient hiccup — see item 4.)
- **No app-level synchronized-output and no `Sync` terminal-override under
  tmux** — let tmux composite the pane. App-level sync (`?2026`) is used only in
  a plain window.

If your renderer isn't a terminal at all, skip to the "Portable principle" note
under each item — the mechanism changes but the lesson holds.

---

## Part 1 — Frame output (the dock rendering saga)

### 1. Row-diff frames: only redraw what changed
**Commit:** `49cdb19` · **Code:** ~2668–2677, `_ROWSPLIT`

Render the full frame to a string, split it into rows, and emit only the rows
that differ from the previous frame (`"".join(r for r,p in zip(rows,prev_rows)
if r != p)`). Fall back to a full paint whenever the row **count** changes
(layout shift) or after any screen clear (`prev_rows = None`). Huge win when the
animation is mostly static void (e.g. a short flame over empty rows) — tmux then
has a fraction of the cells to parse.

> **Portable principle:** dirty-region redraw. Never repaint the whole surface
> if you can diff against the last frame and touch only changed cells/rows. A
> native GPU renderer gets this via a damaged-rect invalidation instead of
> full-canvas draws.

### 2. Truecolor is cheaper than you think; 256-color is the trap
**Commits:** `49cdb19` (wrong), `02c9541` (fix) · **Code:** ~2447–2452 (`_sgr`),
~2623–2632, `LOWCOLOR`

The freeze was **never bandwidth.** Measured byte rate of the dock is ~3 KB/s
(silent for the first ~5 s), and the full `side claude` layout drives tmux at
~11 KB/s with zero stalls. The freeze was iTerm2's GPU rendering of the
**indexed-color (256) escape path** under tmux; 24-bit truecolor at the same
byte rate stays perfectly smooth. So: emit truecolor SGR (`38;2;r;g;b`) by
default; 256-color (`38;5;n`) is **opt-in** via `BURNOUT_LOWCOLOR=1`, not a
default "optimization."

> **Portable principle:** profile the actual draw path before "optimizing" it.
> The cheap-looking encoding (fewer bytes) can be the expensive one to render.
> Measure bytes *and* the downstream render cost separately.

### 3. Full frame rate under tmux — the fps cap was superstition
**Commits:** `4881592` (cap), `d445487` (cap removed) · **Code:** ~2648,
~2715–2719, `FPS`

Once the freeze was pinned to color path (not rate), the 4 fps cap became pure
cargo-cult. Truecolor @ 30 fps is confirmed smooth under tmux+iTerm2. Run at the
full target rate; the only limiter is the backpressure ratchet below.

### 4. Bidirectional backpressure adapter: ease off hard, recover gently
**Commits:** `49cdb19` (ramp — wrong, during freeze era), `4881592` (down-only),
`3729233` (bidirectional — current) · **Code:** ~2708–2720, `DOCK_FPS_MIN = 3`

Time each write (`write_dt`). Under tmux:
- **Backed up** (`write_dt > 0.06`) → ease off hard:
  `eff_fps = max(DOCK_FPS_MIN, eff_fps * 0.7)`.
- **Fast again** (`write_dt < 0.02` and below target) → climb back:
  `eff_fps = min(FPS, eff_fps + 1.0)` (~1 fps per frame).

This went through three iterations, and the ordering matters:
1. First version *ramped up* while writes "looked fast" — but during the
   256-color freeze era, tmux's backlog hid behind buffering, so the ramp
   sailed past the safe rate and re-froze the session. **Wrong then.**
2. Overcorrected to **down-only**: never climb back. Safe, but a single
   transient 60 ms hiccup (Spaces switch, display sleep, GC pause) cut the rate
   30% *permanently*, and over hours the flame decayed toward 3 fps. Users saw
   "the animation slowly slows down and never recovers" — which looked like a
   memory leak but wasn't (60k frames held flat render time, stable RSS/gc).
3. Once the freeze was root-caused to the 256-color path (§2) and *eliminated*,
   climbing back under truecolor became safe. **Bidirectional** recovers from
   transient hiccups without accumulating decay.

The lesson isn't "down-only" or "bidirectional" — it's that **the safe recovery
policy depends on whether the underlying failure still exists.** Ramp-up was
lethal while the freeze was live and correct once it was gone.

> **Portable principle:** adaptive rate control needs both directions — react
> fast to real backpressure (blocked present/write), recover gently when it
> clears. A one-way limiter turns every transient stall into permanent
> degradation. Only suppress recovery while a genuine cliff still exists.

### 5. Synchronized output only outside tmux
**Commits:** `edec4c2` (added), `2b17f19` (gate it to non-tmux) · **Code:**
~2616–2622 (`sync = not os.environ.get("TMUX")`), ~2680–2681

DEC private mode 2026 (`\x1b[?2026h … \x1b[?2026l`) makes a terminal apply a
frame atomically — kills cursor-hide flicker in a plain window. But pushing
app-level sync *through* tmux (and, worse, injecting a `Sync` terminal-override
into tmux's `terminal-overrides`) can wedge the whole tmux client on modern
tmux, which already handles synchronized output natively. So wrap frames in
`?2026` **only when `$TMUX` is unset**; inside tmux, let tmux composite the pane
— its redraw is atomic enough.

> **Portable principle:** don't double up on a guarantee the compositor already
> provides. If the layer beneath you (tmux, the window server, the GPU) already
> commits frames atomically, adding your own atomic-commit wrapper is at best
> redundant and at worst deadlock-inducing.

### 6. Drop timing debt instead of spinning to catch up
**Code:** ~2716–2719

Schedule the next frame at `next_t += 1.0/eff_fps`. If you fell far behind
(`next_t < now - 0.25`, e.g. laptop suspend or a scheduling stall), reset
`next_t = now` — drop the accumulated debt rather than firing a burst of frames
to "catch up." A catch-up burst is exactly when you'd hammer a saturated tmux.

> **Portable principle:** a fixed-timestep loop must clamp accumulated lag, or a
> hitch turns into a stampede of frames right after the stall.

### 7. Self-correcting dock geometry & full repaint after clear
**Code:** ~2688–2704

Every ~28 frames, re-read the terminal size. A dock pane pins itself to 44 cols
(`tmux resize-pane -x 44` via `$TMUX_PANE`) because tmux regrows panes
proportionally when the window jumps displays/sizes. On any resize or scene
switch, clear (`\x1b[2J`) and set `prev_rows = None` so the next frame is a full
repaint (the diff baseline is gone).

> **Portable principle:** invalidate your diff baseline on every event that
> changes the drawable surface (resize, clear, backing-store reallocation), or
> the first post-event frame paints garbage.

### 8. (tmux-only) Mouse-mode agreement stops pointer strobing
**Commits:** `046a0e9` (SGR-tracking experiment, later reverted), current ship
uses `set-option mouse on` · **Code:** ~2796, ~2939

When two tmux panes disagree about mouse mode, tmux toggles outer mouse tracking
during redraws and the pointer strobes between arrow and I-beam at frame rate.
Shipped fix is simply enabling tmux session mouse mode (`tmux set-option mouse
on`) so panes agree. **Irrelevant to a non-tmux native app** — a native window
owns its own cursor — but noted so a porter doesn't reintroduce the flicker by
half-enabling mouse handling.

---

## Part 2 — Data layer (network & startup performance)

These predate the rendering saga but any port wants them; they're what make the
monitor cheap to run several at once and instant to launch.

### 9. Instant first paint from a snapshot cache
**Code:** ~319–350 (`save_cache`/`load_cache`), ~370–372, ~2588–2594

Persist the last good `(gauges, plan)` to `$XDG_CACHE_HOME/…` after every
successful fetch, written atomically (`.tmp` then `os.replace`). On launch,
paint from that cache immediately (aged accordingly) instead of showing an empty
screen while the first network poll is in flight.

> **Portable principle:** cache-first render. Show last-known state instantly,
> reconcile when the live fetch lands.

### 10. One shared poll across many monitors
**Code:** ~403–416 (`_adopt`, cache adoption)

Multiple monitors on one machine share a single network poll through the same
cache file: before hitting the network, a monitor adopts a sibling's fresher
cached fetch, and skips the network entirely while the data (its own or adopted)
is younger than the poll window (12 s hot / 30 s idle). N monitors ≈ 1 poller.

> **Portable principle:** treat the on-disk cache as a coordination channel, not
> just a warm-start blob — readers can free-ride on whoever fetched last.

### 11. Adaptive poll cadence
**Code:** ~407, ~454 (`POLL_SECS_HOT` vs `POLL_SECS`)

Poll faster ("hot": ~12 s) near the limit and around resets, slower (~30 s) when
idle. Don't burn requests when nothing's changing; tighten up when the numbers
matter.

### 12. 429 backoff with jitter — friendly to a fleet
**Commit:** `88d5a8c` · **Code:** ~436–452 (`_cools`, `_fails`)

On HTTP 429: exponential backoff `min(POLL_SECS * 2^(n-1), 600) + rand(0,10)`.
The jitter is what lets a *fleet* of monitors decorrelate and actually let the
rate-limit window cool instead of retrying in lockstep. Transient/offline errors
use a gentler fast-retry-then-back-off ladder (5 s → 15 s → `POLL_SECS`).

> **Portable principle:** any polling client needs capped exponential backoff
> **with jitter**; without jitter, N clients synchronize into a thundering herd.

---

## Dead ends — do NOT re-discover these

The saga cost several releases (v1.1.2–v1.1.6). A port can skip all of it:

1. **"It's bandwidth."** It wasn't. Measured ~3 KB/s. Throttling fps and
   downgrading color to save bytes fixed nothing and caused (2).
2. **256-color as an "optimization."** Indexed color is the *slow* path for
   iTerm2's GPU renderer under tmux. It was the regression, shipped as a fix.
3. **Ramping the frame rate up *while the freeze was still live*.** tmux
   buffering hid the backlog; the ramp climbed past the safe rate and froze a
   beat later. But note the flip side (dead end 3b): once the freeze cause was
   gone, over-correcting to a **down-only** ratchet was *also* wrong — it let a
   transient hiccup permanently decay the flame. The right answer is a
   bidirectional adapter (item 4); the recovery policy tracks whether the cliff
   still exists.
4. **Forcing app-level synchronized output / a `Sync` terminal-override through
   tmux.** Wedges modern tmux, which does sync natively. Gate `?2026` to
   non-tmux.
5. **A fixed low fps cap.** Superstition once the color path was understood.
   Truecolor @ full fps is smooth.

The one-line summary: **the freeze was the 256-color escape path under
tmux+iTerm2, not output volume or frame rate.** Everything else followed from
chasing the wrong theory.

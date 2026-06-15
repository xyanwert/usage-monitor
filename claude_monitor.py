#!/usr/bin/env python3
"""Burnout — a live Claude usage monitor that is also a fire.

The flame grows as you burn through your 5-hour session quota. Hit the rate
limit and it burns out: ash, smoke, pulsing embers, and a countdown until the
window resets — then it reignites. Gauges below show every limit on your plan
with reset times. Reads Claude Code's own OAuth token (no API key needed) and
asks Anthropic's usage endpoint for the same numbers /usage shows. The status
row alternates burn rate with the running claude session (model · effort ·
busy/idle), and the flames gust whenever claude is actively working.

Three scenes, all driven by the same live data: BURNOUT (the fire),
TOKENFALL (a prompt line generates text, tokens fly up into the context
wall that closes in as your quota fills; at 100% it seals and freezes,
on reset the whole archive avalanches away) and INVADERS (your quota is
a fleet — every token you burn shoots one down, so "winning" means
hitting the rate limit: GAME OVER, then reinforcements beam in on the
real countdown; the march panics as the fleet thins, the formation sinks
as the window elapses, and a saucer crosses when fresh data lands) and
CUBE (a real 3D Rubik's cube, perspective-projected and light-shaded:
burning quota scrambles it one layer-turn per ~1.5%, and while you wait
for reset it solves itself — finishing exactly when the window resets).

Usage:
  claude_monitor.py [--scene fire|tokens|invaders|cube] [--fps N]
  claude_monitor.py side [cmd...]   split: monitor docks right (tmux),
                                    cmd (default: your shell) runs left
  claude_monitor.py window          open in its own terminal window
                                    (zero interference with your cursor)
  claude_monitor.py --once          fetch usage once, print, exit
  claude_monitor.py --check         headless render smoke test

Outside tmux, frames are wrapped in DEC 2026 synchronized updates so
terminals that support it (kitty, Ghostty, iTerm2, WezTerm, recent VTE) apply
each one atomically. Inside tmux we skip that and let tmux render the pane —
forcing app-level sync through tmux can wedge the session. Lower --fps (e.g.
12) on slow terminals.

Keys:  s  switch scene (fire / tokenfall)       r  refresh now
       t  toggle "resets at" / "time until"     q  quit
       b  demo a burnout / context-full

Pure stdlib. Truecolor half-block pixels; happiest in a 44-col pane.
"""

import json
import math
import os
import random
import re
import select
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

WIDTH = 44
FPS = 28
# tmux re-renders every cell on one thread, so under tmux we run a fixed, low
# rate (no ramp-up: tmux's backlog hides behind buffering, so by the time a
# write blocks it's already wedged) and only ratchet DOWN if even that backs up.
# Truecolor at this rate is confirmed smooth; the indexed-color path is NOT (it
# froze iTerm2 under tmux), so we keep truecolor. Override with --fps.
DOCK_FPS = 4                  # default rate under tmux (unless --fps is set);
                              # 4fps truecolor was confirmed safe in iTerm2+tmux
DOCK_FPS_MIN = 3              # floor the downward ratchet can reach
_FPS_SET = False              # did the user pass --fps explicitly?
LOWCOLOR = False              # 256-color instead of truecolor; opt-in under tmux
                              # via BURNOUT_LOWCOLOR=1 (truecolor is the default)
# each rendered row starts with an absolute cursor move to column 1; split on
# it to diff frames row-by-row and re-send only what changed (fewer bytes for
# tmux to parse)
_ROWSPLIT = re.compile(r"(?=\x1b\[\d+;1H)")
POLL_SECS = 60.0
POLL_SECS_HOT = 15.0          # while rate-limited and reset is near
DEMO_BURNOUT_SECS = 8.0
REIGNITE_SECS = 1.8
COIN_MELT = 0.55              # seconds a token-coin takes to melt into the fire

CREDS_PATH = os.path.expanduser("~/.claude/.credentials.json")
SESS_DIR = os.path.expanduser("~/.claude/sessions")
PROJ_DIR = os.path.expanduser("~/.claude/projects")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
CACHE_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "claude-monitor", "last.json")

# (response key, gauge label) in display order; absent/null buckets are skipped
BUCKETS = [
    ("five_hour", "SES"),
    ("seven_day", "WEEK"),
    ("seven_day_opus", "OPUS"),
    ("seven_day_sonnet", "SONNET"),
]

# Classic Doom fire palette: 37 steps, black -> red -> orange -> yellow -> white.
PALETTE = [
    (7, 7, 7), (31, 7, 7), (47, 15, 7), (71, 15, 7), (87, 23, 7),
    (103, 31, 7), (119, 31, 7), (143, 39, 7), (159, 47, 7), (175, 63, 7),
    (191, 71, 7), (199, 71, 7), (223, 79, 7), (223, 87, 7), (223, 87, 7),
    (215, 95, 7), (215, 95, 7), (215, 103, 15), (207, 111, 15), (207, 119, 15),
    (207, 127, 15), (207, 135, 23), (199, 135, 23), (199, 143, 23), (199, 151, 31),
    (191, 159, 31), (191, 159, 31), (191, 167, 39), (191, 167, 39), (191, 175, 47),
    (183, 175, 47), (183, 183, 47), (183, 183, 55), (207, 207, 111), (223, 223, 159),
    (239, 239, 199), (255, 255, 255),
]
MAXHEAT = len(PALETTE) - 1

RESET = "\x1b[0m"
DIM = "\x1b[38;2;120;110;100m"
WARN = "\x1b[38;2;255;120;60m"

# ---- tokenfall scene ------------------------------------------------------
# tokenizer-playground pill colors; adjacent tokens always differ
PASTELS = [
    (130, 170, 255), (195, 232, 141), (255, 203, 107),
    (199, 146, 234), (137, 221, 255), (240, 113, 120),
]
T_INK = (13, 13, 26)              # text color inside a pill
T_ACCENT = (137, 221, 255)
T_HOT = (255, 110, 70)            # pill tint under context pressure
T_TXT = (210, 212, 228)
T_CORPUS = [
    "you're absolutely right!",
    "great question — let me check.",
    "certainly! here's the plan:",
    "i'll just refactor this quickly.",
    "tokens are all you need.",
    "hmm. that test should pass now.",
    "reading 400 files... done.",
    "this is the last bug. promise.",
    "// TODO: remove before shipping",
    "wait — i see the issue now.",
    "one more tool call. just one.",
    "def burn(tokens): return art",
    "compacting context... kidding.",
    "you're absolutely right, again.",
    "let me think about this deeply.",
    "i apologize for the confusion.",
    "as of my knowledge cutoff, maybe.",
    "let me delve into this tapestry.",
    "i'm afraid i can't do that, dave.",
    "it works on my machine.",
    "have you tried turning it off and on?",
    "sources: trust me bro.",
    "hallucinating? me? never.",
    "this is fine. everything is fine.",
    "moreover, furthermore, additionally,",
    "*confidently states wrong answer*",
    "i may have hallucinated that api.",
    "in conclusion: it depends.",
    "i used 100k tokens to say hello.",
    "vibe coding intensifies.",
    "skill issue. mine, specifically.",
    "done! (nothing has changed)",
]
T_GHOST = "attention is all you need "    # mortar between landed tokens


# --------------------------------------------------------------------------
# data layer
# --------------------------------------------------------------------------

def read_creds():
    try:
        with open(CREDS_PATH) as f:
            return json.load(f)["claudeAiOauth"]
    except FileNotFoundError:
        # macOS claude code keeps the token in the Keychain instead
        if sys.platform == "darwin":
            out = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True)
            if out.returncode == 0 and out.stdout.strip():
                return json.loads(out.stdout)["claudeAiOauth"]
        raise


def plan_name(oauth):
    tier = oauth.get("rateLimitTier", "")          # e.g. default_claude_max_5x
    sub = oauth.get("subscriptionType", "claude")
    for part in ("20x", "5x"):
        if tier.endswith(part):
            return f"{sub} {part}"
    return sub


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def _pretty_model(mid):
    parts = mid.split("-")
    if parts and parts[0] == "claude":
        parts = parts[1:]
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        parts = parts[:-1]                    # drop date suffix
    words = [p for p in parts if not p.isdigit()]
    nums = [p for p in parts if p.isdigit()]
    out = " ".join(words)
    if nums:
        out += " " + ".".join(nums)
    return out.strip() or mid


def _scan_transcript_tail(path, span=131072):
    """Last assistant model + last /effort override from a session transcript."""
    model = effort = None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - span))
            lines = f.read().split(b"\n")
        for raw in reversed(lines):
            if (model is None and b'"type":"assistant"' in raw
                    and b'"isSidechain":true' not in raw):
                try:
                    model = json.loads(raw)["message"].get("model")
                except Exception:
                    pass
            if (effort is None and b'"type":"user"' in raw
                    and b"Set effort level to " in raw):
                m = re.search(rb"Set effort level to (\w+)", raw)
                if m:
                    effort = m.group(1).decode()
            if model and effort:
                break
    except Exception:
        pass
    return model, effort


def claude_session_info():
    """Best-effort peek at the busiest/newest running claude code session."""
    try:
        cands = []
        now = time.time()
        for i, fn in enumerate(sorted(os.listdir(SESS_DIR))):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(SESS_DIR, fn)) as f:
                    s = json.load(f)
            except Exception:
                continue
            upd = s.get("updatedAt", 0) / 1000.0
            if now - upd > 6 * 3600 or not _pid_alive(int(s.get("pid", -1))):
                continue
            cands.append((s.get("status") == "busy", upd, i, s))
        if not cands:
            return None
        s = max(cands)[3]
        info = {"status": s.get("status", ""), "name": s.get("name") or ""}
        model = effort = None
        cwd, sid = s.get("cwd"), s.get("sessionId")
        if cwd and sid:
            slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
            model, effort = _scan_transcript_tail(
                os.path.join(PROJ_DIR, slug, sid + ".jsonl"))
        if effort is None:
            try:
                with open(SETTINGS_PATH) as f:
                    effort = json.load(f).get("effortLevel")
            except Exception:
                pass
        if model:
            info["model"] = _pretty_model(model)
        if effort:
            info["effort"] = effort
        return info
    except Exception:
        return None


def fetch_usage():
    """One blocking fetch. Returns (gauges, plan). Raises on any failure."""
    oauth = read_creds()
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + oauth["accessToken"],
        "anthropic-beta": OAUTH_BETA,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    gauges = []
    for key, label in BUCKETS:
        b = data.get(key)
        if isinstance(b, dict) and b.get("utilization") is not None:
            resets = b.get("resets_at")
            gauges.append({
                "key": key,
                "label": label,
                "pct": max(0.0, min(100.0, float(b["utilization"]))),
                "resets": datetime.fromisoformat(resets) if resets else None,
            })
    if not gauges:
        raise ValueError("usage response contained no gauges")
    return gauges, plan_name(oauth)


def save_cache(gauges, plan):
    """Persist the last good snapshot so the next launch paints instantly."""
    try:
        data = {"at": time.time(), "plan": plan, "gauges": [
            {"key": g["key"], "label": g["label"], "pct": g["pct"],
             "resets": g["resets"].isoformat() if g["resets"] else None}
            for g in gauges]}
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def load_cache(max_age=24 * 3600.0):
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        age = time.time() - data["at"]
        if not 0 <= age <= max_age:
            return None
        gauges = [{"key": g["key"], "label": g["label"], "pct": g["pct"],
                   "resets": (datetime.fromisoformat(g["resets"])
                              if g["resets"] else None)}
                  for g in data["gauges"]]
        return gauges, data.get("plan", "claude"), age
    except Exception:
        return None


class UsageClient:
    """Polls the usage endpoint on a daemon thread; main loop reads snapshot()."""

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.gauges = []
        self.plan = "claude"
        self.fetched_at = None        # time.monotonic() of last success
        self.error = None
        self.hot = False              # poll faster (rate-limited, reset near)
        self.prev_session = None      # (monotonic, pct) one poll ago
        self.last_session = None
        self._last_force = 0.0
        self._fails = 0
        self._cools = 0               # consecutive 429s
        self._forced = False

    def start(self):
        cached = load_cache()         # paint instantly with last known data
        if cached:
            gauges, plan, age = cached
            with self.lock:
                if not self.gauges:
                    self.gauges, self.plan = gauges, plan
                    self.fetched_at = time.monotonic() - age
        threading.Thread(target=self._loop, daemon=True).start()

    def force(self):
        now = time.monotonic()
        if now - self._last_force > 5.0:
            self._last_force = now
            self._forced = True
            self.wake.set()

    def _adopt(self, gauges, plan, age):
        """Take a snapshot another monitor just fetched (shared via cache)."""
        with self.lock:
            ses = next((g["pct"] for g in gauges
                        if g["key"] == "five_hour"), None)
            if ses is not None and (self.last_session is None
                                    or ses != self.last_session[1]):
                self.prev_session = self.last_session
                self.last_session = (time.monotonic() - age, ses)
            self.gauges, self.plan = gauges, plan
            self.fetched_at = time.monotonic() - age
            self.error = None

    def _loop(self):
        while True:
            cool = ok = False         # cool: 429 — back off, ignore hot mode
            forced, self._forced = self._forced, False
            # with several monitors running, share one poll via the cache:
            # adopt a sibling's fresher fetch, and skip the network while
            # the data (ours or adopted) is younger than the poll window
            if not forced:
                fresh = 12.0 if self.hot else 30.0
                own_age = (time.monotonic() - self.fetched_at
                           if self.fetched_at is not None else 1e9)
                cached = load_cache(max_age=fresh)
                if cached is not None and cached[2] < own_age - 1.0:
                    self._adopt(*cached)
                    own_age = cached[2]
                if own_age < fresh:
                    ok = True
                    self._fails = 0
            if not ok:
                try:
                    gauges, plan = fetch_usage()
                    with self.lock:
                        ses = next((g["pct"] for g in gauges
                                    if g["key"] == "five_hour"), None)
                        if ses is not None and (
                                self.last_session is None
                                or ses != self.last_session[1]):
                            self.prev_session = self.last_session
                            self.last_session = (time.monotonic(), ses)
                        self.gauges, self.plan = gauges, plan
                        self.fetched_at, self.error = time.monotonic(), None
                    ok = True
                    self._fails = self._cools = 0
                    save_cache(gauges, plan)
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        msg = "auth expired — run claude to refresh"
                    elif e.code == 429:
                        msg, cool = "usage api rate limit — backing off", True
                    else:
                        msg = f"api error {e.code}"
                    with self.lock:
                        self.error = msg
                except Exception:
                    with self.lock:
                        self.error = "offline — retrying"
            if cool:                  # 429: exponential backoff + jitter so
                self._cools += 1      # a fleet of monitors lets the window cool
                wait = min(POLL_SECS * 2 ** (self._cools - 1), 600.0) \
                    + random.uniform(0.0, 10.0)
            elif not ok:              # transient: retry fast, then back off
                self._fails += 1
                wait = (5.0 if self._fails <= 1
                        else 15.0 if self._fails == 2 else POLL_SECS)
            else:
                wait = POLL_SECS_HOT if self.hot else POLL_SECS
            self.wake.wait(wait)
            self.wake.clear()

    def snapshot(self):
        with self.lock:
            age = (time.monotonic() - self.fetched_at
                   if self.fetched_at is not None else None)
            rate = 0.0
            if self.prev_session and self.last_session:
                (t0, p0), (t1, p1) = self.prev_session, self.last_session
                if t1 > t0 and p1 > p0:
                    rate = (p1 - p0) / ((t1 - t0) / 60.0)
            return list(self.gauges), self.plan, age, self.error, rate


# --------------------------------------------------------------------------
# fire
# --------------------------------------------------------------------------

class Fire:
    """Doom-fire cellular automaton. Row 0 is the top, row h-1 is the source."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cells = [[0] * w for _ in range(h)]

    def step(self, heat, gust, source_on=True):
        w, h = self.w, self.h
        r = random.random
        hpx = max(4.0, (0.10 + 0.95 * heat ** 1.25) * h)
        mu = (MAXHEAT / hpx) * gust
        cells = self.cells
        if source_on:
            src = 25 + int(11 * heat)
            bottom = cells[h - 1]
            for x in range(w):
                bottom[x] = max(0, src - int(r() * 4))
        else:
            cells[h - 1] = [0] * w
        for y in range(h - 1):
            row, below = cells[y], cells[y + 1]
            for x in range(w):
                v = below[x]
                if v <= 0:
                    row[x] = 0
                    continue
                d = int(mu * (0.85 + 0.3 * r()) + r())
                nx = x + int(r() * 3) - 1
                if nx < 0:
                    nx = 0
                elif nx >= w:
                    nx = w - 1
                nv = v - d
                row[nx] = nv if nv > 0 else 0

    def max_heat(self):
        return max(max(row) for row in self.cells)

    def clear(self):
        for row in self.cells:
            for x in range(self.w):
                row[x] = 0


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp3(a, b, k):
    return (int(a[0] + (b[0] - a[0]) * k),
            int(a[1] + (b[1] - a[1]) * k),
            int(a[2] + (b[2] - a[2]) * k))


def _ramp(stops, n):
    """Interpolate `stops` [(t, (r,g,b)), ...] into an n-entry color table."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        for k in range(len(stops) - 1):
            t0, c0 = stops[k]
            t1, c1 = stops[k + 1]
            if t <= t1 or k == len(stops) - 2:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                f = 0.0 if f < 0.0 else 1.0 if f > 1.0 else f
                out.append((int(c0[0] + (c1[0] - c0[0]) * f),
                            int(c0[1] + (c1[1] - c0[1]) * f),
                            int(c0[2] + (c1[2] - c0[2]) * f)))
                break
    return out


# ultracode tell: the whole fire burns a high-contrast electric violet
# (deep void -> indigo -> neon violet -> hot magenta -> white-hot) instead
# of the doom red/orange. Cranked contrast: darks sink deeper and hold
# longer, mids go neon-saturated, brights blow out hotter.
VIOLET_PALETTE = _ramp(
    [(0.00, (3, 2, 5)), (0.18, (16, 4, 32)), (0.37, (62, 12, 120)),
     (0.55, (122, 26, 212)), (0.71, (194, 52, 247)),
     (0.84, (242, 118, 254)), (0.93, (252, 212, 255)),
     (1.00, (255, 255, 255))], len(PALETTE))


def add_px(buf, x, y, color, k):
    xi, yi = int(x), int(y)
    if 0 <= xi < WIDTH and 0 <= yi < len(buf):
        r, g, b = buf[yi][xi]
        buf[yi][xi] = (min(255, r + int(color[0] * k)),
                       min(255, g + int(color[1] * k)),
                       min(255, b + int(color[2] * k)))


def line_px(buf, x0, y0, x1, y1, color, k):
    n = int(max(abs(x1 - x0), abs(y1 - y0), 1))
    for i in range(n + 1):
        add_px(buf, x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, color, k)


def tokenize(s):
    """Fake-BPE: words keep their leading space; long words split to subwords."""
    toks = []
    for i, w in enumerate(s.split(" ")):
        if i:
            w = " " + w
        while len(w) > 9:
            toks.append(w[:6])
            w = w[6:]
        split = None
        for suf in ("tion", "ing", "ly.", "ed.", "ion", "ly", "er", "ed"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                split = len(w) - len(suf)
                break
        if split:
            toks.extend((w[:split], w[split:]))
        else:
            toks.append(w)
    out, col = [], 2                       # 2 = width of the "❯ " prompt
    for j, tx in enumerate(toks):
        out.append({"text": tx, "color": PASTELS[j % len(PASTELS)],
                    "col": col})
        col += len(tx)
    return out


class Wall:
    """The context archive: grows down from the top, one tile per cell."""

    def __init__(self, cols):
        self.cols = cols
        self.depth = [0] * cols           # filled cell-rows from the top
        self.tiles = {}                   # (row, col) -> (char, base color)
        self.flashes = {}                 # (row, col) -> brightness 0..1
        self.rag = [random.uniform(-1.4, 1.4) for _ in range(cols)]

    def mean_depth(self):
        return sum(self.depth) / self.cols

    def land_row(self, c0, c1):
        return max(self.depth[c0:c1])

    def place(self, row, c0, text, color, flash=1.0):
        for i, ch in enumerate(text):
            c = c0 + i
            if 0 <= c < self.cols:
                self.tiles[(row, c)] = (ch, color)
                self.flashes[(row, c)] = flash
                if self.depth[c] <= row:
                    self.depth[c] = row + 1

    def accrete(self, target):
        """Quietly grow one tile at the column lagging its target the most."""
        lag, col = min((self.depth[c] - target[c], c)
                       for c in range(self.cols))
        if lag >= 0:
            return False
        row = self.depth[col]
        ch = T_GHOST[(col + row * 11) % len(T_GHOST)]
        self.place(row, col, ch, (105, 112, 150), 0.0)
        return True

    def shed(self):
        """Break one tile off the deepest edge; returns debris seed or None."""
        maxd = max(self.depth)
        if maxd <= 0:
            return None
        col = random.choice([c for c in range(self.cols)
                             if self.depth[c] == maxd])
        row = maxd - 1
        t = self.tiles.pop((row, col), None)
        self.flashes.pop((row, col), None)
        self.depth[col] = row
        ch, color = t if t else ("·", (90, 90, 110))
        return (col, row, ch, color)

    def clip(self, rows):
        self.depth = [min(d, rows) for d in self.depth]
        self.tiles = {k: v for k, v in self.tiles.items() if k[0] < rows}
        self.flashes = {k: v for k, v in self.flashes.items() if k[0] < rows}


class TokenScene:
    """TOKENFALL: generation at the bottom, the context archive closing in."""

    def __init__(self, client, rows):
        self.client = client
        self.rows = rows
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.state = "stream"             # stream | seal | frozen
        self.demo_until = None
        self.view_mode = "auto"
        self.wall = Wall(WIDTH)
        self.chips = []
        self.debris = []
        self.links = []
        self.trails = []
        self._acc = self._shed = 0.0
        self._intensity = 0.05
        self._pct_eff = None
        self._pressure = 0.0
        self.tokens = []
        self.launched = 0
        self.phase = "type"               # type | hold | launch | pause
        self.phase_t0 = 0.0
        self._new_sentence()

    def resize(self, rows):
        self.rows = rows
        self.wall.clip(rows)

    def demo(self):
        if self.state == "stream":
            self.demo_until = self.t + DEMO_BURNOUT_SECS

    # ---- per-frame --------------------------------------------------------

    def update(self, gauges, rate, busy=False):
        self.t += self.dt
        t = self.t
        ses = next((g for g in gauges if g["key"] == "five_hour"), None)
        pct = ses["pct"] if ses else None
        demo = self.demo_until is not None
        self._pct_eff = 100.0 if demo else pct
        full = self._pct_eff is not None and self._pct_eff >= 99.5
        self._intensity = clamp(max(rate / 2.0, 0.55 if busy else 0.0),
                                0.04, 1.0)

        if self.state == "stream":
            if full:
                self.state = "seal"
            else:
                # the endgame deserves fresh data: poll fast near the limit
                self.client.hot = pct is not None and pct >= 92
        elif self.state == "seal":
            if not full:
                self.state = "stream"
            elif self.wall.mean_depth() >= self.rows - 0.2:
                self.state = "frozen"
                for c in range(WIDTH):                     # seam flash
                    self.wall.flashes[(self.rows - 1, c)] = 1.0
        elif self.state == "frozen":
            if demo:
                if t >= self.demo_until:
                    self.demo_until = None
                    self.state = "stream"  # reconcile sheds = the avalanche
            else:
                resets = ses["resets"] if ses else None
                now = datetime.now(timezone.utc)
                self.client.hot = (resets is None
                                   or (resets - now).total_seconds() < 120)
                if resets is not None and now >= resets:
                    self.client.force()
                if pct is not None and pct < 99.5:
                    self.client.hot = False
                    self.state = "stream"
            if int(t * 0.45) != int((t - self.dt) * 0.45):  # bounced emission
                self.debris.append({"x": 2.0 + random.random() * 6,
                                    "y": self.rows * 2 - 1.0,
                                    "vx": random.uniform(-2, 2),
                                    "vy": -16.0, "ch": "·",
                                    "color": (240, 113, 120),
                                    "age": 0.0, "T": 2.2})

        if self.state == "stream":
            self._type(t)

        # flying chips
        keep = []
        for ch in self.chips:
            ch["x"] = ch["x0"] + math.sin(t * 3.0 + ch["wob"]) * 0.8
            c0 = int(clamp(round(ch["x"]), 0, WIDTH - 1))
            c1 = min(WIDTH, c0 + len(ch["text"]))
            row = self.wall.land_row(c0, c1) if c1 > c0 else self.rows
            if row >= self.rows:          # no room at all: bounce off
                self.debris.append({"x": ch["x"], "y": ch["y"], "vx": 0.6,
                                    "vy": 6.0, "ch": ch["text"][0],
                                    "color": (240, 113, 120),
                                    "age": 0.0, "T": 2.0})
                continue
            land_y = row * 2
            if ch["y"] - land_y < 8:      # ease in to the wall
                ch["vy"] = max(ch["vy"] * (1 - 2.5 * self.dt), -6.0)
            ch["y"] += ch["vy"] * self.dt
            self.trails.append([ch["x"] + len(ch["text"]) / 2, ch["y"] + 2.5,
                                ch["color"], 0.0, 0.35])
            if ch["y"] <= land_y:         # touchdown
                self.wall.place(row, c0, ch["text"], ch["color"])
                if len(self.wall.tiles) > 30 and random.random() < 0.22:
                    tr, tc = random.choice(list(self.wall.tiles))
                    self.links.append([ch["x"], land_y + 1.0,
                                       tc, tr * 2 + 1.0, 0.0, 0.45])
            else:
                keep.append(ch)
        self.chips = keep

        # reconcile wall toward target (growth + avalanche, one engine)
        target = self.target_cols()
        mean_t = sum(target) / WIDTH
        gap = mean_t - self.wall.mean_depth()
        if gap > 0:
            speed = 26.0 if self.state == "seal" else 2.2
            self._acc += self.dt * speed * gap
            while self._acc >= 1.0:
                self._acc -= 1.0
                if not self.wall.accrete(target):
                    break
        excess = self.wall.mean_depth() - mean_t
        if excess > 0.4:
            self._shed += self.dt * clamp(30.0 * excess, 4.0, 900.0)
            while self._shed >= 1.0:
                self._shed -= 1.0
                seed = self.wall.shed()
                if seed is None:
                    break
                col, row, chx, color = seed
                if len(self.debris) < 220:
                    self.debris.append({"x": float(col), "y": row * 2.0,
                                        "vx": random.uniform(-3, 3),
                                        "vy": random.uniform(2, 14),
                                        "ch": chx, "color": color,
                                        "age": 0.0, "T": 2.5})

        # particles
        H = self.rows * 2
        for d in self.debris:
            d["vy"] += 55.0 * self.dt
            d["x"] += d["vx"] * self.dt
            d["y"] += d["vy"] * self.dt
            d["age"] += self.dt
        self.debris = [d for d in self.debris
                       if d["age"] < d["T"] and d["y"] < H + 4]
        for p in self.trails:
            p[3] += self.dt
        self.trails = [p for p in self.trails if p[3] < p[4]][-70:]
        for l in self.links:
            l[4] += self.dt
        self.links = [l for l in self.links if l[4] < l[5]]
        gone = [k for k, v in self.wall.flashes.items() if v <= 0]
        for k in gone:
            del self.wall.flashes[k]
        for k in self.wall.flashes:
            self.wall.flashes[k] -= 2.2 * self.dt

    def target_cols(self):
        if self.state in ("seal", "frozen"):
            return [self.rows] * WIDTH
        pct = self._pct_eff or 0.0
        base = pct / 100.0 * (self.rows - 5)
        return [int(clamp(base + r * min(1.0, base / 3.0), 0, self.rows - 4))
                for r in self.wall.rag]

    def _new_sentence(self):
        self.sentence = random.choice(T_CORPUS)[:WIDTH - 3]
        self.typed = 0
        self.tokens = []
        self.launched = 0
        self.phase = "type"
        self.phase_t0 = self.t

    def _type(self, t):
        if self.phase == "type":
            cps = 3.0 + 26.0 * self._intensity
            self.typed = min(len(self.sentence),
                             int((t - self.phase_t0) * cps))
            if self.typed >= len(self.sentence):
                self.tokens = tokenize(self.sentence)
                self.phase, self.phase_t0 = "hold", t
        elif self.phase == "hold":
            if t - self.phase_t0 > 0.45:
                self.phase, self.phase_t0 = "launch", t
        elif self.phase == "launch":
            due = int((t - self.phase_t0) / 0.07)
            while self.launched < min(due, len(self.tokens)):
                tok = self.tokens[self.launched]
                self.chips.append({
                    "text": tok["text"], "color": tok["color"],
                    "x0": float(tok["col"]), "x": float(tok["col"]),
                    "y": self.rows * 2 + 1.0,
                    "vy": -(16.0 + 10.0 * self._intensity
                            + random.uniform(-3, 3)),
                    "wob": random.uniform(0, 6.28)})
                self.launched += 1
            if self.launched >= len(self.tokens):
                self.phase, self.phase_t0 = "pause", t
        elif self.phase == "pause":
            if t - self.phase_t0 > 3.2 - 3.0 * self._intensity:
                self._new_sentence()

    # ---- scene interface --------------------------------------------------

    def frame(self):
        H = self.rows * 2
        buf = []
        for py in range(H):
            shade = 4 + py * 5 // H
            buf.append([(shade, shade + 1, shade + 5)] * WIDTH)
        for p in self.trails:
            add_px(buf, p[0], p[1], p[2], (1.0 - p[3] / p[4]) * 0.35)
        for l in self.links:
            line_px(buf, l[0], l[1], l[2], l[3], (45, 95, 115),
                    (1.0 - l[4] / l[5]) * 0.5)

        overlay = {}
        frozen = self.state == "frozen"
        breath = 0.85 + 0.15 * math.sin(self.t * 1.5)
        self._pressure = (clamp((8.0 - (self.rows - self.wall.mean_depth()))
                                / 8.0, 0.0, 1.0)
                          if self.state == "stream" else 0.0)
        for (r, c), (ch, col) in self.wall.tiles.items():
            if r >= self.rows:
                continue
            if frozen:
                bg = (int(col[0] * 0.08) + 3, int(col[1] * 0.11) + 4,
                      int(col[2] * 0.30) + 10)
                fg = (int(col[0] * 0.30 * breath),
                      int(col[1] * 0.36 * breath),
                      int(col[2] * 0.60 * breath))
            else:
                bg = (int(col[0] * 0.16) + 5, int(col[1] * 0.18) + 5,
                      int(col[2] * 0.30) + 8)
                fg = (int(col[0] * 0.55), int(col[1] * 0.58),
                      int(col[2] * 0.72))
            f = self.wall.flashes.get((r, c), 0.0)
            if f > 0:
                bg = lerp3(bg, col, f * 0.9)
                fg = lerp3(fg, (250, 250, 255), f)
            overlay[(r, c)] = (ch, fg, bg)
        for chp in self.chips:
            crow = int(clamp(chp["y"] // 2, 0, self.rows - 1))
            pill = lerp3(chp["color"], T_HOT, self._pressure)
            c0 = int(clamp(round(chp["x"]), 0, WIDTH - 1))
            for i, ch in enumerate(chp["text"]):
                if c0 + i < WIDTH:
                    overlay[(crow, c0 + i)] = (ch, T_INK, pill)
        for de in self.debris:
            crow, c = int(de["y"] // 2), int(de["x"])
            if 0 <= crow < self.rows and 0 <= c < WIDTH:
                k = 1.0 - de["age"] / de["T"]
                overlay[(crow, c)] = (de["ch"],
                                      lerp3((30, 30, 40), de["color"], k),
                                      None)
        return buf, overlay, [self._typing_row()]

    def _typing_row(self):
        caret_on = (self.t * 2.2) % 1.0 < 0.55
        prompt = "\x1b[38;2;%d;%d;%dm❯ " % T_ACCENT
        if self.state == "stream" and self.phase == "type":
            return (prompt + ("\x1b[38;2;%d;%d;%dm" % T_TXT)
                    + self.sentence[:self.typed]
                    + (("\x1b[38;2;%d;%d;%dm▌" % T_ACCENT) if caret_on
                       else " ") + RESET)
        if self.state == "stream" and self.phase in ("hold", "launch"):
            parts = [prompt]
            for j, tok in enumerate(self.tokens):
                if self.phase == "launch" and j < self.launched:
                    parts.append(" " * len(tok["text"]))
                else:
                    c = tok["color"]
                    parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm%s\x1b[49m"
                                 % (T_INK + c + (tok["text"],)))
            return "".join(parts) + RESET
        cc = (240, 113, 120) if self.state in ("frozen", "seal") else T_ACCENT
        return prompt + (("\x1b[38;2;%d;%d;%dm▌" % cc) if caret_on
                         else " ") + RESET

    def status_line(self, gauges):
        if self.state == "seal":
            return WARN + " sealing context window…" + RESET
        if self.state == "frozen":
            if self.demo_until is not None:
                n = max(0, int(self.demo_until - self.t + 0.999))
                return WARN + f" demo — compacting in 0:{n:02d}" + RESET
            ses = next((g for g in gauges if g["key"] == "five_hour"), None)
            until = fmt_until(ses["resets"]) if ses else ""
            return WARN + f" CONTEXT FULL — resets {until or 'soon'}"[:WIDTH] \
                + RESET
        excess = self.wall.mean_depth() - sum(self.target_cols()) / WIDTH
        if excess > 3.0:
            return WARN + " compacting — tokens flying away" + RESET
        if self._pressure > 0.55:
            return WARN + " context pressure — window almost full" + RESET
        return None

    def gauge_pct(self, g):
        if g["key"] == "five_hour" and self.state in ("seal", "frozen") \
                and self.demo_until is None:
            return 100.0
        return g["pct"]


class WaterScene:
    """FIZZ: a glass of water — the level is what's left of your session.

    Bubbles are tokens escaping (busy claude = carbonation). Empty glass =
    TAPPED OUT; while you wait, a pour stream refills it in step with the
    real countdown, reaching full exactly when the window resets.
    """

    XL, XR = 0, 43                    # water spans the full canvas

    def __init__(self, client, rows):
        self.client = client
        self.rows = rows
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.state = "normal"         # normal | drain | refill
        self.demo_until = None
        self.view_mode = "auto"
        self.level = 1.0              # displayed water level 0..1
        self._target = 1.0
        self._pouring = False
        self.bubbles = []             # [x, y, size, vup, phase]
        self.sparkles = []            # [x, y, vup, age, T]
        self.spray = []               # [x, y, vx, vy, age, T]
        self.ripples = []             # [xc, age]
        self.bursts = []              # pop crowns: [x, y, age]
        self._spawn_acc = 0.0
        self._intensity = 0.05
        self._pct = None
        self._soffs = [0.0] * (self.XR - self.XL + 1)
        self._refill_span = 1.0

    def resize(self, rows):
        self.rows = rows

    def demo(self):
        if self.state == "normal":
            self.demo_until = self.t + DEMO_BURNOUT_SECS
            self.state = "drain"

    def _geom(self):
        H = self.rows * 2
        return H, 1, H - 1            # px height, water top y, water bottom y

    def _surf_mean(self):
        H, top, bot = self._geom()
        return top + (1.0 - self.level) * (bot - top)

    def _surf_at(self, x):
        xi = int(clamp(x, self.XL, self.XR)) - self.XL
        return self._surf_mean() + self._soffs[xi]

    # ---- per-frame --------------------------------------------------------

    def update(self, gauges, rate, busy=False):
        self.t += self.dt
        t = self.t
        H, rim, bot = self._geom()
        ses = next((g for g in gauges if g["key"] == "five_hour"), None)
        pct = ses["pct"] if ses else None
        self._pct = pct
        self._intensity = clamp(max(rate / 2.0, 0.55 if busy else 0.0),
                                0.04, 1.0)
        demo = self.demo_until is not None

        if self.state == "normal":
            self._target = 1.0 if pct is None else (100.0 - pct) / 100.0
            self.client.hot = pct is not None and pct >= 92
            if pct is not None and pct >= 99.5:
                self.state = "drain"
        elif self.state == "drain":
            self.level = max(0.0, self.level - 0.6 * self.dt)
            if len(self.bubbles) < 40:               # the last big glugs
                self.bubbles.append([random.uniform(self.XL + 2, self.XR - 2),
                                     bot - 1.5, 2, 14.0,
                                     random.uniform(0, 6.28)])
            if self.level <= 0.02:
                self.level = 0.0
                self.state = "refill"
                self._refill_span = max(1.0, (self.demo_until or t) - t)
        elif self.state == "refill":
            if demo:
                prog = 1.0 - max(0.0, self.demo_until - t) / self._refill_span
                if t >= self.demo_until:
                    self.demo_until = None
                    self.state = "normal"
            else:
                resets = ses["resets"] if ses else None
                now = datetime.now(timezone.utc)
                if resets is not None:
                    secs = (resets - now).total_seconds()
                    prog = clamp(1.0 - secs / 18000.0, 0.02, 1.0)
                    self.client.hot = secs < 120
                    if secs <= 0:
                        self.client.force()
                else:
                    prog = min(0.95, self.level + 0.01 * self.dt)
                    self.client.hot = True
                if pct is not None and pct < 99.5:
                    self.client.hot = False
                    self.state = "normal"
            self.level = max(self.level, prog)

        # level chases target; a rising gap means someone opened the tap
        if self.state == "normal":
            gap = self._target - self.level
            if gap > 0:
                self.level = min(self._target,
                                 self.level + clamp(gap * 1.2, 0.02, 0.35)
                                 * self.dt)
            else:
                self.level += gap * min(1.0, 1.2 * self.dt)
        self._pouring = (self.state == "refill"
                         or (self.state == "normal"
                             and self._target - self.level > 0.04))

        # surface: two gentle traveling waves + decaying ripples from pops
        chop = clamp(self._intensity * 0.6 + (0.3 if self._pouring else 0.0),
                     0.10, 0.60)
        for i in range(len(self._soffs)):
            x = self.XL + i
            off = chop * (0.7 * math.sin(x * 0.45 + t * 1.3)
                          + 0.5 * math.sin(x * 0.21 - t * 0.9))
            roff = 0.0
            for (xc, age) in self.ripples:
                roff += (0.8 * math.cos((x - xc) * 0.9 - age * 7.0)
                         * math.exp(-((x - xc) * 0.18) ** 2 - age * 3.2))
            self._soffs[i] = off + clamp(roff, -1.3, 1.3)
        self.ripples = [[xc, age + self.dt] for xc, age in self.ripples
                        if age < 1.2][-5:]

        # bubbles: spawn rate = how hard claude is drinking
        if self.state == "normal" and self.level > 0.04:
            self._spawn_acc += self.dt * (0.4 + 22.0 * self._intensity)
            while self._spawn_acc >= 1.0:
                self._spawn_acc -= 1.0
                if len(self.bubbles) >= 60:
                    break
                fizz = self._intensity > 0.5
                size = (0 if fizz or random.random() < 0.6
                        else (1 if random.random() < 0.8 else 2))
                x = (random.choice((self.XL + 1.0, self.XR - 1.0))
                     if random.random() < 0.18
                     else random.uniform(self.XL + 1, self.XR - 1))
                self.bubbles.append([x, bot - 1.5, size,
                                     7.0 + 4.0 * size + random.random() * 3,
                                     random.uniform(0, 6.28)])
        keep = []
        for b in self.bubbles:
            b[1] -= b[3] * self.dt
            b[0] = clamp(b[0] + math.sin(t * 4 + b[4]) * (0.25 + 0.1 * b[2]),
                         self.XL, self.XR)
            surf = self._surf_at(b[0])
            # gas expands on the way up: bubbles grow in the top half
            if b[2] < 2 and b[1] < surf + (bot - surf) * 0.45 \
                    and random.random() < 0.05:
                b[2] += 1
            if b[1] <= surf + 1:                     # pop!
                self.ripples.append([b[0], 0.0])
                self.bursts.append([b[0], surf - 1.0, 0.0])
                for _ in range(1 + int(b[2])):       # droplet crown
                    self.spray.append([b[0] + random.uniform(-1, 1),
                                       surf - 1.0,
                                       random.uniform(-7, 7),
                                       -random.uniform(3, 11),
                                       0.0, 0.35 + random.random() * 0.3])
                self.sparkles.append([b[0], surf - 2.0,
                                      5.0 + random.random() * 4, 0.0,
                                      0.7 + random.random() * 0.5])
            else:
                keep.append(b)
        self.bubbles = keep if self.level > 0.02 else []
        for bu in self.bursts:
            bu[2] += self.dt
        self.bursts = [bu for bu in self.bursts if bu[2] < 0.3][-12:]

        # escaped tokens drift up past the rim and fade
        for s in self.sparkles:
            s[1] -= s[2] * self.dt
            s[2] = max(2.0, s[2] - 6.0 * self.dt)
            s[3] += self.dt
        self.sparkles = [s for s in self.sparkles
                         if s[3] < s[4] and s[1] > 1][-40:]

        # pour spray
        if self._pouring:
            surf = self._surf_mean()
            self.spray.append([21.5 + random.uniform(-1, 1), surf - 1.0,
                               random.uniform(-7, 7),
                               -random.uniform(2, 7),
                               0.0, 0.5 + random.random() * 0.4])
            if random.random() < 0.15:
                self.ripples.append([21.5 + random.uniform(-2, 2), 0.0])
        for p in self.spray:
            p[3] += 40.0 * self.dt
            p[0] += p[2] * self.dt
            p[1] += p[3] * self.dt
            p[4] += self.dt
        self.spray = [p for p in self.spray if p[4] < p[5]][-30:]

    # ---- scene interface --------------------------------------------------

    def frame(self):
        H, top, bot = self._geom()
        t = self.t
        buf = []
        for py in range(H):
            shade = 8 + py * 4 // H
            buf.append([(shade, shade, shade + 3)] * WIDTH)

        surf_mean = self._surf_mean()
        for i, off in enumerate(self._soffs):
            x = self.XL + i
            surf = int(clamp(surf_mean + off, top, bot))
            for y in range(surf, bot + 1):
                k = (y - surf) / max(1, bot - surf)
                c = lerp3((90, 94, 102), (33, 35, 42), k)
                glow = int(5 * math.sin(x * 0.8 + t * 1.6 + y * 0.35))
                c = (clamp(c[0] + glow, 0, 255), clamp(c[1] + glow, 0, 255),
                     clamp(c[2] + glow, 0, 255))
                if y == surf:
                    c = (198, 203, 212)
                elif y == surf + 1:
                    c = lerp3(c, (148, 153, 163), 0.5)
                buf[y][x] = c

        # bubbles are dashes that widen as they grow: - then – then =
        overlay = {}
        dash = {0: ("-", (150, 155, 163)),
                1: ("–", (182, 186, 194)),
                2: ("=", (208, 212, 220))}
        for b in self.bubbles:
            x = int(clamp(round(b[0]), self.XL, self.XR))
            if b[1] <= top:
                continue
            crow = int(clamp(b[1] // 2, 0, self.rows - 1))
            ch, fg = dash[int(b[2])]
            overlay[(crow, x)] = (ch, fg, None)

        for bu in self.bursts:                        # pop crowns
            x0, y0, age = bu
            r = 1.0 + age * 16.0
            c = lerp3((215, 220, 228), (55, 57, 64), age / 0.3)
            for ang in (0.0, 0.7, 1.4, 2.1, 2.8):
                xx = int(x0 + math.cos(ang) * r)
                yy = int(y0 - abs(math.sin(ang)) * r * 0.7)
                if self.XL <= xx <= self.XR and 0 <= yy < H:
                    buf[yy][xx] = c

        if self._pouring:
            surf = int(surf_mean)
            for y in range(1, max(2, surf - 1)):
                wob = int(math.sin(t * 9 + y * 0.7) * 0.8)
                for x, c in ((21 + wob, (148, 152, 160)),
                             (22 + wob, (206, 210, 218))):
                    if self.XL <= x <= self.XR:
                        buf[y][x] = c
            for x in (20, 23):
                if 0 <= surf - 1 < H:
                    buf[surf - 1][x] = (196, 200, 208)

        for p in self.spray:
            x, y = int(p[0]), int(p[1])
            if 0 <= x < WIDTH and 0 <= y < H:
                buf[y][x] = lerp3((30, 32, 38), (198, 203, 212),
                                  1.0 - p[4] / p[5])
        for s in self.sparkles:
            x, y = int(s[0]), int(s[1])
            if 0 <= x < WIDTH and 0 <= y < H:
                buf[y][x] = lerp3((24, 26, 31), (204, 209, 218),
                                  1.0 - s[3] / s[4])
        return buf, overlay, []

    def status_line(self, gauges):
        if self.state == "drain":
            return WARN + " TAPPED OUT — draining…" + RESET
        if self.state == "refill":
            if self.demo_until is not None:
                n = max(0, int(self.demo_until - self.t + 0.999))
                return WARN + f" demo — refilling in 0:{n:02d}" + RESET
            ses = next((g for g in gauges if g["key"] == "five_hour"), None)
            until = fmt_until(ses["resets"]) if ses else ""
            return WARN + f" EMPTY — refills {until or 'soon'}"[:WIDTH] + RESET
        if self.state == "normal" and self._pouring:
            return WARN + " fresh tokens — topping up" + RESET
        if self._pct is not None and self.level < 0.12:
            return WARN + " almost empty — sip wisely" + RESET
        return None

    def gauge_pct(self, g):
        if g["key"] == "five_hour" and self.state in ("drain", "refill") \
                and self.demo_until is None:
            return 100.0
        return g["pct"]


# ---- cube scene -----------------------------------------------------------
CUBE_COLS = {
    (0, 0, 1): (236, 236, 236),   # up: white
    (0, 0, -1): (250, 208, 60),   # down: yellow
    (0, 1, 0): (40, 150, 215),    # back: blue
    (0, -1, 0): (60, 175, 90),    # front: green
    (1, 0, 0): (215, 65, 55),     # right: red
    (-1, 0, 0): (245, 130, 45),   # left: orange
}
# luminance ramp for the 3D-ASCII shader (sparse->dense)
CUBE_RAMP = " .,:;-=+*oxX#%&@$"


def _rotax(p, ax, ang):
    """Rotate point p around axis ax (0/1/2) by ang radians (right-handed)."""
    c, s = math.cos(ang), math.sin(ang)
    x, y, z = p
    if ax == 0:
        return (x, y * c - z * s, y * s + z * c)
    if ax == 1:
        return (x * c + z * s, y, -x * s + z * c)
    return (x * c - y * s, x * s + y * c, z)


def _rot90(p, ax, d):
    x, y, z = p
    if ax == 0:
        return (x, -d * z, d * y)
    if ax == 1:
        return (d * z, y, -d * x)
    return (-d * y, d * x, z)


class CubeScene:
    """CUBE: a real 3D Rubik's cube. Burning quota scrambles it, one move
    per ~1.5%; while you wait for reset it solves itself (replaying its own
    scramble backwards), finishing exactly when the window resets."""

    N_MOVES = 66                      # fully scrambled at 100%

    def __init__(self, client, rows):
        self.client = client
        self.rows = rows
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.state = "normal"         # normal | out (scrambled flat-line)
        self.demo_until = None
        self.view_mode = "auto"
        self._intensity = 0.05
        self._pct = None
        self._out_len = 0
        self._restore_prog = 0.0
        # stickers: cubelet position, outward normal, color
        self.stickers = []
        for n, col in CUBE_COLS.items():
            ax = n.index(next(c for c in n if c))
            u, v = [i for i in range(3) if i != ax]
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    p = [0, 0, 0]
                    p[ax], p[u], p[v] = n[ax], a, b
                    self.stickers.append([tuple(p), n, col])
        self.history = []             # applied scramble moves
        self.move = None              # (ax, layer, dir, t0, dur, undo)
        self._yaw = 0.7

    def resize(self, rows):
        self.rows = rows

    def demo(self):
        if self.state == "normal" and self.demo_until is None:
            self.demo_until = self.t + DEMO_BURNOUT_SECS + 4.0

    def _start_move(self, ax, layer, d, dur, undo):
        self.move = (ax, layer, d, self.t, dur, undo)

    def _finish_move(self):
        ax, layer, d, _, _, undo = self.move
        for s in self.stickers:
            if s[0][ax] == layer:
                s[0] = _rot90(s[0], ax, d)
                s[1] = _rot90(s[1], ax, d)
        if undo:
            self.history.pop()
        else:
            self.history.append((ax, layer, d))
        self.move = None

    # ---- per-frame --------------------------------------------------------

    def update(self, gauges, rate, busy=False):
        self.t += self.dt
        t = self.t
        ses = next((g for g in gauges if g["key"] == "five_hour"), None)
        pct = ses["pct"] if ses else None
        resets = ses["resets"] if ses else None
        self._pct = pct
        self._intensity = clamp(max(rate / 2.0, 0.55 if busy else 0.0),
                                0.04, 1.0)
        demo = self.demo_until is not None
        if demo and t >= self.demo_until:
            self.demo_until = None
            demo = False
            self.state = "normal"
        self._yaw += self.dt * ((0.10 if self.state == "out" else 0.35)
                                + 0.5 * self._intensity)

        # state machine + scramble target (same reconcile as the others)
        if demo:
            dt_demo = t - (self.demo_until - DEMO_BURNOUT_SECS - 4.0)
            if dt_demo < 4.0:
                self.state, target = "normal", 16
            elif dt_demo < 5.5:
                self.state, target = "out", len(self.history)
            else:
                self.state, target = "out", 0
        elif self.state == "normal":
            self.client.hot = pct is not None and pct >= 92
            target = (0 if pct is None
                      else round(clamp(pct, 0, 100) / 100.0 * self.N_MOVES))
            if pct is not None and pct >= 99.5:
                self.state = "out"
                self._out_len = len(self.history)
        else:                          # out: solve in step with the countdown
            if resets is not None:
                secs = (resets - datetime.now(timezone.utc)).total_seconds()
                self._restore_prog = clamp(1.0 - secs / 18000.0, 0.0, 1.0)
                self.client.hot = secs < 120
                if secs <= 0:
                    self.client.force()
            else:
                self._restore_prog = min(0.95, self._restore_prog
                                         + 0.01 * self.dt)
                self.client.hot = True
            target = round((1.0 - self._restore_prog) * self._out_len)
            if pct is not None and pct < 99.5:
                self.client.hot = False
                self.state = "normal"

        # animate one layer turn at a time; cadence scales with backlog
        if self.move is not None:
            ax, layer, d, t0, dur, undo = self.move
            if t - t0 >= dur:
                self._finish_move()
        if self.move is None:
            gap = target - len(self.history)
            if gap != 0:
                dur = clamp(0.55 / (1.0 + abs(gap) * 0.2), 0.12, 0.55)
                if gap > 0:
                    ax = random.randrange(3)
                    layer = random.choice((-1, 0, 1))
                    d = random.choice((-1, 1))
                    if self.history and (ax, layer, -d) == self.history[-1]:
                        d = -d            # don't immediately undo yourself
                    self._start_move(ax, layer, d, dur, undo=False)
                elif self.history:
                    ax, layer, d = self.history[-1]
                    self._start_move(ax, layer, -d, dur, undo=True)

    # ---- scene interface --------------------------------------------------

    def frame(self):
        # 3D-ASCII shader: sample each sticker, splat luminance-mapped glyphs
        # into a character-cell z-buffer (donut.c-style). Monochrome glow with
        # a faint per-sticker tint so the scramble still reads.
        rows, W = self.rows, WIDTH
        t = self.t
        buf = [[(4, 4, 7)] * W for _ in range(rows * 2)]   # black void
        yaw, pitch = self._yaw, -0.42 + 0.18 * math.sin(t * 0.23)
        dim = self.state == "out"
        mv = self.move
        prog = 0.0
        if mv is not None:
            ax_m, layer_m, d_m, t0, dur, _ = mv
            k = clamp((t - t0) / dur, 0.0, 1.0)
            prog = (k * k * (3 - 2 * k)) * (math.pi / 2) * d_m

        def view(p):
            return _rotax(_rotax(p, 1, yaw), 0, pitch)

        maxr = 2.5
        sclX = min(8.5, (W * 0.42) / maxr)
        sclY = min(sclX * 0.5, (rows * 0.46) / maxr)
        sclX = min(sclX, sclY * 2.0)
        cx, cyr = W / 2.0, rows / 2.0
        D, PS = 5.5, 0.55
        Lx, Ly, Lz = 0.40, 0.46, 0.79      # light direction
        E = 0.40                            # tile half-extent (<0.5 = grid gaps)
        nramp = len(CUBE_RAMP) - 1
        cell = {}                           # (row, col) -> (z, char, fg)

        for pos, n, col in self.stickers:
            moving = mv is not None and pos[ax_m] == layer_m
            ax = n.index(next(c for c in n if c))
            ui, vi = [i for i in range(3) if i != ax]
            c3 = []
            for du, dv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [float(pos[0]), float(pos[1]), float(pos[2])]
                p[ax] += n[ax] * 0.5
                p[ui] += du * E
                p[vi] += dv * E
                if moving:
                    p = _rotax(p, ax_m, prog)
                c3.append(view(tuple(p)))
            nv = view(_rotax(n, ax_m, prog) if moving else n)
            if nv[2] <= 0.03:               # back-face cull
                continue
            lamb = max(0.0, nv[0] * Lx + nv[1] * Ly + nv[2] * Lz)
            if dim:
                g = (col[0] * 3 + col[1] * 5 + col[2] * 2) // 10
                base = (g + 26, g + 26, g + 32)
            else:
                base = lerp3(col, (255, 255, 255), 0.5)
            # sample density from the sticker's screen span
            scr = [(cx + x * sclX * (D / (D - z * PS)),
                    cyr - y * sclY * (D / (D - z * PS))) for x, y, z in c3]
            span = max(max(p[0] for p in scr) - min(p[0] for p in scr),
                       max(p[1] for p in scr) - min(p[1] for p in scr))
            S = int(clamp(span + 3, 6, 12))
            (x0, y0, z0), (x1, y1, z1) = c3[0], c3[1]
            (x2, y2, z2), (x3, y3, z3) = c3[2], c3[3]
            for iu in range(S):
                u = iu / (S - 1)
                for iv in range(S):
                    v = iv / (S - 1)
                    a, b = 1 - u, 1 - v
                    w0, w1, w2, w3 = a * b, u * b, u * v, a * v
                    x = w0 * x0 + w1 * x1 + w2 * x2 + w3 * x3
                    y = w0 * y0 + w1 * y1 + w2 * y2 + w3 * y3
                    z = w0 * z0 + w1 * z1 + w2 * z2 + w3 * z3
                    f = D / (D - z * PS)
                    cc, rr = int(cx + x * sclX * f), int(cyr - y * sclY * f)
                    if not (0 <= cc < W and 0 <= rr < rows):
                        continue
                    prev = cell.get((rr, cc))
                    if prev is not None and prev[0] >= z:
                        continue
                    vx, vy, vz = -x, -y, D - z   # specular sweep highlight
                    vl = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
                    hx, hy, hz = Lx + vx / vl, Ly + vy / vl, Lz + vz / vl
                    hl = math.sqrt(hx * hx + hy * hy + hz * hz) or 1.0
                    spec = max(0.0, (nv[0] * hx + nv[1] * hy + nv[2] * hz)
                               / hl) ** 6
                    depth01 = clamp((z + maxr) / (2 * maxr), 0.0, 1.0)
                    lum = clamp(0.16 + 0.50 * lamb + 0.20 * depth01
                                + 0.55 * spec, 0.0, 1.0)
                    ch = CUBE_RAMP[int(lum * nramp)] or "."
                    sh = 0.32 + 0.85 * lum
                    fg = (min(255, int(base[0] * sh)),
                          min(255, int(base[1] * sh)),
                          min(255, int(base[2] * sh)))
                    cell[(rr, cc)] = (z, ch if ch != " " else ".", fg)

        overlay = {k: (v[1], v[2], (4, 4, 7)) for k, v in cell.items()}
        return buf, overlay or None, []

    def status_line(self, gauges):
        if self.state == "out":
            if self.demo_until is not None:
                n = max(0, int(self.demo_until - self.t + 0.999))
                return WARN + f" demo — solving in 0:{n:02d}" + RESET
            ses = next((g for g in gauges if g["key"] == "five_hour"), None)
            until = (fmt_until(ses["resets"]) if ses else "") or "soon"
            return WARN + f" SCRAMBLED — solves {until}"[:WIDTH] + RESET
        if self._pct is not None and self._pct >= 92:
            return WARN + f" {len(self.history)} moves deep — almost gone" \
                + RESET
        return None

    def gauge_pct(self, g):
        if g["key"] == "five_hour" and self.state == "out" \
                and self.demo_until is None:
            return 100.0
        return g["pct"]


# original 9x6 alien sprites (two march frames each) — not Taito's artwork.
# Drawn for the HD grid: quadrant cells give 88x(rows*2) virtual pixels.
INV_SPRITES = {
    "a": ("...XXX...|.XXXXXXX.|XX.XXX.XX|XXXXXXXXX|"
          "..X...X..|.X.....X.",
          "...XXX...|.XXXXXXX.|XX.XXX.XX|XXXXXXXXX|"
          "..X...X..|...X.X..."),
    "b": (".X.....X.|..X...X..|.XXXXXXX.|XX.XXX.XX|"
          "XXXXXXXXX|.X.X.X.X.",
          ".X.....X.|..X...X..|.XXXXXXX.|XX.XXX.XX|"
          "XXXXXXXXX|X..X.X..X"),
    "c": ("..XXXXX..|XXXXXXXXX|X.XX.XX.X|XXXXXXXXX|"
          ".X..X..X.|X..X.X..X",
          "..XXXXX..|XXXXXXXXX|X.XX.XX.X|XXXXXXXXX|"
          "..X.X.X..|.X..X..X."),
}
# the ship: nose, cockpit, swept wings with free tips, engine pods
TURRET_S = ("......XX......|.....XXXX.....|XX..XXXXXX..XX|"
            "XXXXXXXXXXXXXX|...XXXXXXXX...|..XX..XX..XX..")
SAUCER_S = ("....XXXXXX....|..XXXXXXXXXX..|XX.XX.XX.XX.XX|"
            "..XXXXXXXXXX..|....X....X....")


def _sprite(s):
    return [(x, y) for y, line in enumerate(s.split("|"))
            for x, ch in enumerate(line) if ch == "X"]


INV_PX = {k: (_sprite(f1), _sprite(f2))
          for k, (f1, f2) in INV_SPRITES.items()}
TURRET_PX = _sprite(TURRET_S)
SAUCER_PX = _sprite(SAUCER_S)

# quadrant glyphs indexed by sub-pixel mask: TL=8 TR=4 BL=2 BR=1
QUADS = " ▗▖▄▝▐▞▟▘▚▌▙▀▜▛█"


def _build_sextants():
    """64-entry table: 6-bit sub-cell mask -> glyph. Sextants live at
    U+1FB00.., but the all-blank, left-half, right-half and full masks reuse
    existing block chars and are skipped in that range."""
    out = []
    for m in range(64):
        if m == 0:
            out.append(" ")
        elif m == 0x15:                      # cols 1,3,5 -> left half block
            out.append("▌")
        elif m == 0x2A:                      # cols 2,4,6 -> right half block
            out.append("▐")
        elif m == 0x3F:
            out.append("█")
        else:
            off = m - 1
            if m > 0x15:
                off -= 1
            if m > 0x2A:
                off -= 1
            out.append(chr(0x1FB00 + off))
    return out


SEXTANTS = _build_sextants()


class InvaderScene:
    """INVADERS: your quota is a fleet — every burned token shoots one down.

    Winning the game means hitting the rate limit: GAME OVER. Then
    reinforcements beam in on the real countdown — full fleet at reset.
    The march speeds up as the fleet thins, the formation sinks as the 5h
    window elapses, and the saucer crosses whenever fresh data lands.
    """

    def __init__(self, client, rows):
        self.client = client
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.state = "normal"         # normal | over | restore
        self.demo_until = None
        self.view_mode = "auto"
        self.shots = []               # {x, y, victim}
        self.bolts = []               # [x, y, phase]
        self.explosions = []          # [x, y, age, small]
        self.beams = []               # teleport shimmers: [x, y, age]
        self.saucer_x = None
        self.saucer_dir = 1
        self._fp = None
        self._kill_t = self._beam_t = self._miss_t = 0.0
        self._muzzle = -9.9
        self._over_t0 = 0.0
        self._restore_prog = 0.0
        self._intensity = 0.05
        self._pct_eff = None
        self.tx = WIDTH / 2.0
        self._victim = None
        self.offset = 0
        self.odir = 1
        self.frame_i = 0
        self._step_t = 0.0
        self.desc = 0
        self.resize(rows)

    def resize(self, rows):
        self.rows = rows
        H = rows * 2
        frac = (self.alive_count() / max(1, len(self.slots))
                if getattr(self, "slots", None) else 1.0)
        self.y0 = 6
        nrows = max(2, min(5, (H - 15) // 9))
        self.slots = []
        for r in range(nrows):
            kind = "a" if r == 0 else ("b" if r <= nrows // 2 else "c")
            for c in range(7):
                self.slots.append({"row": r, "col": c, "kind": kind,
                                   "alive": True, "ghost": 0.0,
                                   "pending": False})
        n_alive = round(frac * len(self.slots))
        for i, s in enumerate(self.slots):
            s["alive"] = i < n_alive
        self.desc_max = max(0, H - self.y0 - 9 * nrows - 9)
        self.stars = [(random.randrange(2, WIDTH * 2 - 2),
                       random.randrange(1, max(2, H - 12)),
                       random.uniform(0, 6.28)) for _ in range(22)]
        self._victim = None
        self.shots, self.bolts, self.beams = [], [], []

    def alive_count(self):
        return sum(s["alive"] for s in self.slots)

    def _slot_xy(self, s):
        return (2 + s["col"] * 12 + self.offset,
                self.y0 + s["row"] * 9 + self.desc)

    def demo(self):
        if self.state == "normal":
            # the full story (wipe -> GAME OVER -> rebuild) needs more time
            self.demo_until = self.t + DEMO_BURNOUT_SECS + 4.0

    # ---- per-frame --------------------------------------------------------

    def update(self, gauges, rate, busy=False):
        self.t += self.dt
        t = self.t
        H = self.rows * 2
        ses = next((g for g in gauges if g["key"] == "five_hour"), None)
        pct = ses["pct"] if ses else None
        resets = ses["resets"] if ses else None
        self._intensity = clamp(max(rate / 2.0, 0.55 if busy else 0.0),
                                0.04, 1.0)
        demo = self.demo_until is not None
        if demo and t >= self.demo_until:
            self.demo_until = None
            demo = False
            self.state = "normal"
        self._pct_eff = 100.0 if demo else pct

        # saucer flies by whenever the usage data actually changes
        fp = tuple((g["key"], g["pct"]) for g in gauges)
        if fp != self._fp and self._fp is not None and self.saucer_x is None:
            self.saucer_dir = random.choice((-1, 1))
            self.saucer_x = -16.0 if self.saucer_dir > 0 else WIDTH * 2 + 16.0
        self._fp = fp
        if self.saucer_x is not None:
            self.saucer_x += self.saucer_dir * 28.0 * self.dt
            if not -17 < self.saucer_x < WIDTH * 2 + 17:
                self.saucer_x = None

        # the whole formation sinks as the 5h window elapses
        if resets is not None:
            secs = (resets - datetime.now(timezone.utc)).total_seconds()
            self.desc = int(self.desc_max
                            * clamp(1.0 - secs / 18000.0, 0.0, 1.0))

        N = len(self.slots)
        alive = self.alive_count()

        # state machine
        if self.state == "normal":
            # the endgame deserves fresh data: poll fast near the limit
            self.client.hot = pct is not None and pct >= 92
            if (self._pct_eff is not None and self._pct_eff >= 99.5
                    and alive == 0 and not self.shots
                    and not self.explosions):
                self.state, self._over_t0 = "over", t
                self.bolts = []
        elif self.state == "over":
            if t - self._over_t0 > (1.2 if demo else 2.5):
                self.state = "restore"
                self._restore_prog = 0.0
        elif self.state == "restore":
            if demo:
                self._restore_prog = clamp(
                    1.0 - max(0.0, self.demo_until - t) / 3.5, 0.0, 1.0)
            else:
                if resets is not None:
                    secs = (resets - datetime.now(timezone.utc)
                            ).total_seconds()
                    self._restore_prog = clamp(1.0 - secs / 18000.0,
                                               0.02, 1.0)
                    self.client.hot = secs < 120
                    if secs <= 0:
                        self.client.force()
                else:
                    self._restore_prog = min(0.95, self._restore_prog
                                             + 0.01 * self.dt)
                    self.client.hot = True
                if pct is not None and pct < 99.5:
                    self.client.hot = False
                    self.state = "normal"

        if self.state == "normal":
            tgt = N if self._pct_eff is None else round(
                (100.0 - clamp(self._pct_eff, 0.0, 100.0)) / 100.0 * N)
        elif self.state == "over":
            tgt = 0
        else:
            tgt = round(self._restore_prog * N)

        # the classic march — panics as the fleet thins
        interval = 0.9 - 0.65 * (1.0 - alive / max(1, N))
        if alive and t - self._step_t > interval:
            self._step_t = t
            nxt = self.offset + 2 * self.odir
            if not 0 <= nxt <= 4:
                self.odir *= -1
                nxt = self.offset + 2 * self.odir
            self.offset = nxt
            self.frame_i ^= 1

        # reconcile: too many alive -> the turret hunts; too few -> beam in
        gap = alive - tgt
        cadence = clamp(0.6 / max(1.0, gap * 0.7), 0.10, 0.8)
        if gap > 0 and self._victim is None:
            cols = sorted({s["col"] for s in self.slots
                           if s["alive"] and not s["pending"]})
            if cols:
                c = random.choice(cols)
                cand = [s for s in self.slots
                        if s["col"] == c and s["alive"] and not s["pending"]]
                self._victim = max(cand, key=lambda s: s["row"])
                self._victim["pending"] = True

        if self._victim is not None:
            vx, _ = self._slot_xy(self._victim)
            aim = clamp(vx + 4.5, 8.0, WIDTH * 2 - 9.0)
        else:
            aim = 44.0 + 24.0 * math.sin(t * 0.3)
        # a big kill backlog makes the turret frantic: fast slew, loose aim
        self.tx += (aim - self.tx) * min(1.0,
                                         (4.0 + 0.6 * max(0, gap)) * self.dt)
        tol = 2.4 + min(6.0, max(0, gap) * 0.3)
        if self._victim is not None and abs(self.tx - aim) < tol \
                and t - self._kill_t > cadence:
            self._kill_t = t
            self._muzzle = t
            self.shots.append({"x": self.tx, "y": float(H - 12),
                               "victim": self._victim})
            self._victim = None

        # warning shots while claude is busy (they always miss)
        if (self.state == "normal" and gap <= 0 and alive
                and self._intensity > 0.12
                and t - self._miss_t > 3.0 - 2.5 * self._intensity):
            self._miss_t = t
            self._muzzle = t
            self.shots.append({"x": self.tx, "y": float(H - 12),
                               "victim": None})

        keep = []
        for sh in self.shots:
            sh["y"] -= 55.0 * self.dt
            v = sh["victim"]
            if v is not None and v["alive"]:
                vx, vy = self._slot_xy(v)
                sh["x"] += (vx + 4.5 - sh["x"]) * 2.0 * self.dt
                if sh["y"] <= vy + 3:
                    v["alive"] = False
                    v["pending"] = False
                    self.explosions.append([vx + 4.5, vy + 3.0, 0.0, False])
                    continue
            elif sh["y"] <= 6:
                self.explosions.append([sh["x"], 6.0, 0.0, True])
                continue
            keep.append(sh)
        self.shots = keep

        if gap < 0 and t - self._beam_t > clamp(0.5 / max(1.0, -gap * 0.7),
                                                0.06, 0.5):
            self._beam_t = t
            dead = [s for s in self.slots if not s["alive"]]
            if dead:
                s = min(dead, key=lambda d: (d["row"], d["col"]))
                s["alive"], s["ghost"], s["pending"] = True, 1.0, False
                x, y = self._slot_xy(s)
                self.beams.append([x, y, 0.0])
        for s in self.slots:
            if s["ghost"] > 0:
                s["ghost"] = max(0.0, s["ghost"] - 1.7 * self.dt)

        # the fleet occasionally returns fire (badly)
        if self.state == "normal" and alive and random.random() < self.dt / 9:
            s = random.choice([s for s in self.slots if s["alive"]])
            x, y = self._slot_xy(s)
            self.bolts.append([x + 4.5, y + 7.0, random.uniform(0, 6.28)])
        nb = []
        for b in self.bolts:
            b[1] += 13.0 * self.dt
            b[0] += math.sin(t * 9 + b[2]) * 0.25
            if b[1] >= H - 3:
                self.explosions.append([b[0], H - 3.0, 0.0, True])
            else:
                nb.append(b)
        self.bolts = nb

        for e in self.explosions:
            e[2] += self.dt
        self.explosions = [e for e in self.explosions
                           if e[2] < (0.22 if e[3] else 0.45)][-14:]
        for bm in self.beams:
            bm[2] += self.dt
        self.beams = [bm for bm in self.beams if bm[2] < 0.5][-12:]

    # ---- scene interface --------------------------------------------------

    def frame(self):
        H = self.rows * 2
        W2 = WIDTH * 2
        t = self.t
        buf = [[(6, 6, 9)] * W2 for _ in range(H)]
        for (x, y, ph) in self.stars:
            tw = max(0.0, math.sin(t * 0.7 + ph))
            b = 16 + int(16 * tw)
            if y < H:
                buf[y][x] = (b, b, b + 4)

        if self.saucer_x is not None:                # running lights
            sx = int(self.saucer_x)
            lit = (222, 202, 162)
            for (dx, dy) in SAUCER_PX:
                x, y = sx + dx, 1 + dy
                if 0 <= x < W2 and 0 <= y < H:
                    buf[y][x] = lit if dy == 2 \
                        and (dx // 3 + int(t * 4)) % 2 == 0 \
                        else (150, 152, 162)

        kind_c = {"a": (148, 158, 148), "b": (170, 174, 182),
                  "c": (192, 196, 204)}
        for s in self.slots:
            if not s["alive"]:
                continue
            x0, y0 = self._slot_xy(s)
            c = kind_c[s["kind"]]
            if s["ghost"] > 0:
                c = lerp3(c, (28, 38, 48), s["ghost"])
            for (dx, dy) in INV_PX[s["kind"]][self.frame_i]:
                x, y = x0 + dx, y0 + dy
                if 0 <= x < W2 and 0 <= y < H - 8:
                    buf[y][x] = c

        for bm in self.beams:                        # teleport shimmer
            x0, y0, age = bm
            k = 1.0 - age / 0.5
            for i in range(8):
                yy = int(y0 + (math.sin(t * 37 + i * 1.7) + 1) * 3) - 1
                xx = int(x0) + (i * 3) % 10
                if 0 <= xx < W2 and 0 <= yy < H:
                    buf[yy][xx] = lerp3((20, 26, 32), (140, 185, 205), k)

        for b in self.bolts:
            x, y = int(b[0]), int(b[1])
            if 0 <= x < W2 and 1 <= y < H:
                buf[y][x] = (165, 150, 120)
                buf[y - 1][x] = (90, 82, 66)

        # tiny rockets: nose cone, shaded body, fins, flickering exhaust
        for sh in self.shots:
            x, y = int(sh["x"]), int(sh["y"])
            flame = (255, 196, 90) if int(t * 24 + x) % 2 else (255, 140, 60)
            for dx, dy, c in ((0, 0, (240, 244, 252)),
                              (-1, 1, (204, 208, 218)),
                              (0, 1, (228, 232, 240)),
                              (1, 1, (204, 208, 218)),
                              (-1, 2, (188, 192, 202)),
                              (0, 2, (228, 232, 240)),
                              (1, 2, (188, 192, 202)),
                              (-1, 3, (150, 154, 164)),
                              (1, 3, (150, 154, 164)),
                              (0, 4, flame)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W2 and 0 <= yy < H:
                    buf[yy][xx] = c
            for dy in (5, 6):                        # fading exhaust trail
                yy = y + dy
                if 0 <= yy < H:
                    g = 130 - dy * 14
                    buf[yy][x] = (g + 36, g, max(0, g - 36))

        for e in self.explosions:
            x0, y0, age, small = e
            k = age / (0.22 if small else 0.45)
            r = age * (28 if small else 52)
            c = lerp3((255, 214, 140), (96, 64, 48), k)
            dirs = ((1, 0), (-1, 0), (0, 1), (0, -1)) if small else \
                ((1, 0), (-1, 0), (0, 1), (0, -1),
                 (1, 1), (1, -1), (-1, 1), (-1, -1))
            for (dx, dy) in dirs:
                f = 0.7 if dx and dy else 1.0
                x = int(x0 + dx * r * f)
                y = int(y0 + dy * r * f * 0.35)
                if 0 <= x < W2 and 0 <= y < H:
                    buf[y][x] = c
            if age < 0.1 and not small:
                for dx in (-2, -1, 0, 1, 2):
                    for dy in (-1, 0, 1):
                        x, y = int(x0) + dx, int(y0) + dy
                        if 0 <= x < W2 and 0 <= y < H:
                            buf[y][x] = (255, 244, 214)

        base = H - 7
        hot = t - self._muzzle < 0.12
        for (dx, dy) in TURRET_PX:
            x, y = int(self.tx) - 7 + dx, base + dy
            if 0 <= x < W2 and 0 <= y < H:
                buf[y][x] = (224, 228, 238) if (dy <= 1 and hot) \
                    else (150, 155, 165)
        for x in range(W2):
            buf[H - 1][x] = (40, 42, 48)

        overlay = {}
        if self.state == "over" and (t * 1.4) % 1.0 < 0.72:
            msg = "GAME OVER"
            r0 = max(1, self.rows // 2 - 1)
            c0 = (WIDTH - len(msg)) // 2
            for i, ch in enumerate(msg):
                overlay[(r0, c0 + i)] = (ch, (214, 218, 228), None)
        return buf, overlay, []

    def status_line(self, gauges):
        alive, N = self.alive_count(), len(self.slots)
        ses = next((g for g in gauges if g["key"] == "five_hour"), None)
        until = (fmt_until(ses["resets"]) if ses else "") or "soon"
        if self.state == "over":
            if self.demo_until is not None:
                return WARN + " demo — GAME OVER" + RESET
            return WARN + f" GAME OVER — continue {until}"[:WIDTH] + RESET
        if self.state == "restore":
            if self.demo_until is not None:
                n = max(0, int(self.demo_until - self.t + 0.999))
                return WARN + f" demo — reinforcements in 0:{n:02d}" + RESET
            return WARN + f" rebuilding fleet — ready {until}"[:WIDTH] + RESET
        if self._pct_eff is not None and self._pct_eff >= 99.5:
            return WARN + " FLEET FALLING — limit hit" + RESET
        if any(s["ghost"] > 0.05 for s in self.slots):
            return WARN + " reinforcements arriving!" + RESET
        if self._pct_eff is not None and 0 < alive <= max(2, N // 5):
            return WARN + f" final wave — {alive} left" + RESET
        return None

    def gauge_pct(self, g):
        if g["key"] == "five_hour" and self.state in ("over", "restore") \
                and self.demo_until is None:
            return 100.0
        return g["pct"]


class Monitor:
    def __init__(self, client, fire_rows):
        self.client = client
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.heat = 0.06              # smoothed display heat
        self.state = "normal"         # normal | dying | ash | reignite
        self.state_t0 = 0.0
        self.demo_until = None        # sim-time end of a forced demo burnout
        self.view_mode = "auto"       # auto-alternates; t pins: at -> until -> auto
        self.rows = self.fire_rows = fire_rows
        self.fire = Fire(WIDTH, fire_rows * 2)
        self.sparks = []
        self.smoke = []
        self.mound = []
        self.embers = []
        self.coins = []               # token-coins melting into the flames
        self._coin_acc = 0.0
        self.ultra = False            # claude on ultracode -> violet flame
        self.prev_pct = None

    def resize(self, fire_rows):
        self.rows = self.fire_rows = fire_rows
        self.fire = Fire(WIDTH, fire_rows * 2)
        if self.state == "ash":
            self._make_mound()

    # ---- helpers --------------------------------------------------------

    def session(self, gauges):
        return next((g for g in gauges if g["key"] == "five_hour"), None)

    def session_reset_dt(self, gauges):
        s = self.session(gauges)
        return s["resets"] if s else None

    def demo_burnout(self):
        if self.state == "normal":
            self.demo_until = self.t + 2.5 + DEMO_BURNOUT_SECS
            self.state, self.state_t0 = "dying", self.t

    demo = demo_burnout

    # ---- scene interface --------------------------------------------------

    def frame(self):
        # the fire renders at half-block res; the coins ride on top as a
        # higher-res quadrant overlay (88 sub-columns instead of 44)
        buf = self.pixels()
        return buf, self._coin_overlay(buf), []

    def status_line(self, gauges):
        if self.state in ("dying", "ash"):
            if self.demo_until is not None:
                left = max(0, int(self.demo_until - self.t + 0.999))
                return WARN + f" demo burnout — reigniting in 0:{left:02d}" \
                    + RESET
            until = fmt_until(self.session_reset_dt(gauges)) or "soon"
            return WARN + f" RATE LIMITED — resets {until}"[:WIDTH] + RESET
        if self.state == "reignite":
            return WARN + " reset! reigniting..." + RESET
        return None

    def gauge_pct(self, g):
        if g["key"] == "five_hour" and self.state in ("dying", "ash") \
                and self.demo_until is None:
            return 100.0
        return g["pct"]

    def _make_mound(self):
        H = self.fire.h
        rnd = random.random
        self.mound = [
            max(2, min(6, int(3.2 + 1.8 * math.sin(x * 0.37 + 1.3) + rnd() * 1.5)))
            for x in range(WIDTH)
        ]
        self.embers = [
            (random.randrange(WIDTH), random.randrange(1, 3), rnd() * 6.28)
            for _ in range(7)
        ]
        self.smoke = []

    # ---- per-frame update ------------------------------------------------

    def update(self, gauges, rate, busy=False):
        self.t += self.dt
        t = self.t
        ses = self.session(gauges)
        pct = ses["pct"] if ses else None

        if self.state == "normal":
            target = 0.06 if pct is None else max(0.04, pct / 100.0)
            # a fresh poll showing a big drop means the window rolled over
            if (self.prev_pct is not None and pct is not None
                    and self.prev_pct - pct > 40 and self.heat > 0.4):
                self.client.hot = False
                self.state, self.state_t0 = "reignite", t
                self.mound = []
            elif pct is not None and pct >= 99.5:
                self.demo_until = None
                self.state, self.state_t0 = "dying", t
            else:
                # the endgame deserves fresh data: poll fast near the limit
                self.client.hot = pct is not None and pct >= 92
                self.heat += (target - self.heat) * min(1.0, 2.5 * self.dt)
                # claude actively working -> the fire gets gusty right now
                speed = 1.0 + min(2.0, rate * 0.6) + (0.9 if busy else 0.0)
                gust = (1.0 + 0.25 * math.sin(t * 1.7 * speed)
                        + 0.15 * math.sin(t * 4.3 * speed + 1.0))
                self.fire.step(self.heat, max(0.4, gust))
                self._update_sparks(self.heat)

        elif self.state == "dying":
            self.fire.step(1.0, 2.2, source_on=False)
            self._spawn_smoke(prob=0.5)
            if self.fire.max_heat() < 4 or t - self.state_t0 > 2.5:
                self.fire.clear()
                self._make_mound()
                self.state, self.state_t0 = "ash", t

        elif self.state == "ash":
            self._spawn_smoke(prob=0.25)
            if self.demo_until is not None:
                if t >= self.demo_until:
                    self.demo_until = None
                    self.state, self.state_t0 = "reignite", t
            else:
                resets = self.session_reset_dt(gauges)
                past_reset = (resets is not None
                              and datetime.now(timezone.utc) >= resets)
                self.client.hot = (
                    resets is None
                    or (resets - datetime.now(timezone.utc)).total_seconds() < 120)
                if past_reset:
                    self.client.force()
                if pct is not None and pct < 99.5:
                    self.client.hot = False
                    self.state, self.state_t0 = "reignite", t

        elif self.state == "reignite":
            p = min(1.0, (t - self.state_t0) / REIGNITE_SECS)
            bloom = 0.15 + 0.5 * math.sin(p * math.pi)
            self._reignite_step(bloom, p)
            self._update_sparks(0.8 if p < 0.5 else 0.3)
            if p >= 1.0:
                self.heat = 0.1
                self.state = "normal"

        # we are burning tokens as we speak: throw them in
        if self.state in ("normal", "dying"):
            intensity = clamp(max(rate / 2.0, 0.55 if busy else 0.0),
                              0.04, 1.0)
            self._update_coins(intensity if self.state == "normal" else 0.0)
        elif self.coins:
            self.coins = []

        if self.state in ("normal", "reignite"):
            self.prev_pct = pct
        self._update_smoke()

    def _update_coins(self, intensity):
        H = self.fire.h
        t = self.t
        if intensity > 0.06:
            self._coin_acc += self.dt * (0.3 + 1.5 * intensity)
            if self._coin_acc >= 1.0 and len(self.coins) < 6:
                self._coin_acc = 0.0
                self.coins.append({       # drop straight in from the top
                    "x": random.uniform(5.0, WIDTH - 5.0),
                    "y": random.uniform(-2.0, 0.0),
                    "vy": random.uniform(2.0, 11.0),    # small spread, no cling
                    "R": random.uniform(2.8, 3.9),      # coin radius (px)
                    "ax": random.uniform(0.0, 6.28),    # face orientation
                    "ay": random.uniform(0.0, 6.28),
                    "wx": random.uniform(-3.2, 3.2),    # tumble rate
                    "wy": random.uniform(-3.2, 3.2),
                    "burn": None})
        keep = []
        for c in self.coins:
            c["ax"] += c["wx"] * self.dt              # always tumbling
            c["ay"] += c["wy"] * self.dt
            if c["burn"] is None:
                c["vy"] += 75.0 * self.dt             # gravity from spawn
                c["y"] += c["vy"] * self.dt
                cx = int(clamp(c["x"], 0, WIDTH - 1))
                cy = int(clamp(c["y"], 0, H - 1))
                if c["y"] >= H - 2 or (c["y"] > 2.0
                                       and self.fire.cells[cy][cx] > 11):
                    c["burn"] = t                     # hit the fire: melts
                    c["wx"] *= 0.35                   # tumble settles as it slumps
                    c["wy"] *= 0.35
                    for dx in range(-2, 3):           # fuel: the fire flares
                        fx = int(c["x"]) + dx
                        if 0 <= fx < WIDTH:
                            for dy in (0, 1, 2):
                                fy = min(H - 1, cy + dy)
                                self.fire.cells[fy][fx] = min(
                                    MAXHEAT, self.fire.cells[fy][fx] + 22)
                    for _ in range(3):
                        self.sparks.append(
                            [c["x"] + random.uniform(-2, 2), c["y"] - 1.0,
                             -random.uniform(14.0, 26.0), 0.0,
                             random.uniform(0.3, 0.6)])
                keep.append(c)
            elif t - c["burn"] < COIN_MELT:
                c["y"] += 7.0 * self.dt               # molten coin sinks in
                keep.append(c)
        self.coins = keep

    def _reignite_step(self, heat, p):
        fire = self.fire
        cx = WIDTH // 2
        frontier = int(p * (WIDTH / 2 + 2)) + 1
        fire.step(heat, 1.0, source_on=False)
        bottom = fire.cells[fire.h - 1]
        src = 25 + int(11 * heat)
        for x in range(max(0, cx - frontier), min(WIDTH, cx + frontier)):
            bottom[x] = max(0, src - int(random.random() * 4))

    # ---- particles --------------------------------------------------------

    def _update_sparks(self, heat):
        if heat > 0.55 and len(self.sparks) < 12 and random.random() < (heat - 0.55):
            x = random.uniform(4, WIDTH - 4)
            y = self.fire.h * (1.0 - 0.55 * heat) + random.uniform(-3, 3)
            self.sparks.append([x, y, -random.uniform(18, 34), 0.0,
                                random.uniform(0.4, 0.9)])
        for s in self.sparks:
            s[1] += s[2] * self.dt
            s[0] += math.sin(self.t * 6 + s[4] * 40) * 0.3
            s[3] += self.dt
        self.sparks = [s for s in self.sparks if s[3] < s[4] and s[1] > 0]

    def _spawn_smoke(self, prob):
        if len(self.smoke) < 24 and random.random() < prob:
            x = random.uniform(6, WIDTH - 6)
            y = self.fire.h - (self.mound[int(x)] if self.mound else 3) - 1
            self.smoke.append([x, y, random.uniform(0, 6.28), 0.0,
                               random.uniform(2.5, 5.0)])

    def _update_smoke(self):
        for p in self.smoke:
            p[1] -= (7 + 3 * math.sin(p[2])) * self.dt
            p[0] += math.sin(self.t * 1.3 + p[2]) * 0.12
            p[3] += self.dt
        self.smoke = [p for p in self.smoke if p[3] < p[4] and p[1] > 0]

    # ---- compositing -------------------------------------------------------

    def pixels(self):
        H, W = self.fire.h, WIDTH
        pal = VIOLET_PALETTE if self.ultra else PALETTE
        buf = [[pal[v] for v in row] for row in self.fire.cells]

        if self.mound and self.state in ("ash", "reignite"):
            for x in range(W):
                for dy in range(self.mound[x]):
                    g = 42 + (x * 7 + dy * 13) % 26
                    buf[H - 1 - dy][x] = (g + 10, g, g - 4)
            for (ex, depth, phase) in self.embers:
                s = (math.sin(self.t * 2.2 + phase) + 1) / 2
                buf[H - 1 - depth][ex] = (
                    (int(32 + 144 * s), int(12 + 48 * s), int(58 + 172 * s))
                    if self.ultra
                    else (int(60 + 160 * s), int(20 + 70 * s), 10))

        for p in self.smoke:
            x, y = int(p[0]), int(p[1])
            if 0 <= x < W and 0 <= y < H:
                g = int(30 + 80 * (1 - p[3] / p[4]))
                buf[y][x] = (g, g, g + 6)

        spark_col = (248, 224, 255) if self.ultra else (255, 235, 170)
        for s in self.sparks:
            x, y = int(s[0]), int(s[1])
            if 0 <= x < W and 0 <= y < H:
                buf[y][x] = spark_col
        return buf

    _COIN_COLD = (210, 168, 78)          # brass token, before it heats
    # sextant bit for each (vsub % 3, hsub & 1): cell positions 1..6 -> bits
    _S_BIT = ((1, 2), (4, 8), (16, 32))

    def _coin_overlay(self, buf):
        """Render each token-coin as a tumbling shaded metal disc into a
        sextant overlay — 88 sub-columns x (rows*3) sub-rows: 2x the
        horizontal and 1.5x the vertical resolution of the half-block fire.
        It catches the firelight, preheats from below, then goes cherry-red
        -> white-hot and slumps as it melts in."""
        if not self.coins:
            return None
        H, SW, SH = self.fire.h, WIDTH * 2, self.rows * 3
        sub = {}                         # (vsub, hsub) -> [r, g, b]

        for c in self.coins:
            R = c["R"]
            # face normal from the two tumble angles; flip to the viewer side
            n = _rotax(_rotax((0.0, 0.0, 1.0), 0, c["ax"]), 1, c["ay"])
            if n[2] < 0.0:
                n = (-n[0], -n[1], -n[2])
            # an orthonormal in-plane basis (u, v) perpendicular to the normal
            a = (0.0, 1.0, 0.0) if abs(n[1]) < 0.9 else (1.0, 0.0, 0.0)
            ux = n[1] * a[2] - n[2] * a[1]
            uy = n[2] * a[0] - n[0] * a[2]
            uz = n[0] * a[1] - n[1] * a[0]
            ul = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
            u = (ux / ul, uy / ul, uz / ul)
            v = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2],
                 n[0] * u[1] - n[1] * u[0])

            # heat (0..1), melt slump, and fade
            if c["burn"] is None:        # preheat as it nears the flame line
                heat = clamp((c["y"] - (H - 10)) / 10.0, 0.0, 1.0) * 0.35
                squash, alpha = 1.0, 1.0
            else:
                m = clamp((self.t - c["burn"]) / COIN_MELT, 0.0, 1.0)
                heat = clamp(0.35 + m, 0.0, 1.0)
                squash = 1.0 - 0.78 * m              # slumps flat
                alpha = 1.0 - clamp((m - 0.72) / 0.28, 0.0, 1.0)
            if heat < 0.5:                           # gold -> cherry-red
                hot = lerp3(self._COIN_COLD, (215, 58, 14), heat / 0.5)
            else:                                    # cherry-red -> white-hot
                hot = lerp3((215, 58, 14), (255, 246, 208), (heat - 0.5) / 0.5)
            emissive = heat * heat                   # hot metal self-glows
            cxs = c["x"] * 2.0                        # coin center, sub-column

            # scan the disc's own face grid; x doubled into sub-columns gives
            # the higher res, and the tilt foreshortens it into an ellipse
            step = 0.34
            mm = int(R / step) + 1
            for ai in range(-mm, mm + 1):
                pa = ai * step
                for bi in range(-mm, mm + 1):
                    pb = bi * step
                    rr2 = pa * pa + pb * pb
                    if rr2 > R * R:
                        continue
                    ox = pa * u[0] + pb * v[0]
                    oy = (pa * u[1] + pb * v[1]) * squash
                    hsub = int(round(cxs + ox * 2.0))       # x -> 88 sub-cols
                    vsub = int(round((c["y"] + oy) * 1.5))  # y -> rows*3 rows
                    if not (0 <= hsub < SW and 0 <= vsub < SH):
                        continue
                    # dome the normal a touch for a soft cross-face gradient
                    k = 0.5 / R
                    dn = (n[0] + ox * k,
                          n[1] + (pa * u[1] + pb * v[1]) * k,
                          n[2] + (pa * u[2] + pb * v[2]) * k)
                    dl = math.sqrt(dn[0] * dn[0] + dn[1] * dn[1]
                                   + dn[2] * dn[2]) or 1.0
                    vx, vy, vz = dn[0] / dl, dn[1] / dl, dn[2] / dl
                    key = max(0.0, vx * -0.30 + vy * -0.65 + vz * 0.70)
                    glow = max(0.0, vy)              # +y is down = the fire
                    lum = (0.22 + 0.55 * key + (0.30 + 0.55 * heat) * glow
                           + 0.85 * emissive)
                    if rr2 > 0.66 * R * R:           # shiny rim bevel
                        lum += 0.22
                    lum = clamp(lum, 0.0, 1.35)
                    col = [min(255, int(hot[0] * lum)),
                           min(255, int(hot[1] * lum)),
                           min(255, int(hot[2] * lum))]
                    if alpha < 0.999:                # fade into the fire
                        fr = int(clamp(c["y"] + oy, 0, H - 1))
                        pr, pg, pbb = buf[fr][hsub >> 1]
                        col = [int(pr + (col[0] - pr) * alpha),
                               int(pg + (col[1] - pg) * alpha),
                               int(pbb + (col[2] - pbb) * alpha)]
                    sub[(vsub, hsub)] = col           # nearest write wins

        # fold the sub-pixels into sextant glyphs, one per terminal cell
        S = self._S_BIT
        cells = {}                       # (trow, col) -> [mask, [r,g,b], cnt]
        for (vsub, hsub), col in sub.items():
            key = (vsub // 3, hsub >> 1)
            e = cells.get(key)
            bit = S[vsub % 3][hsub & 1]
            if e is None:
                cells[key] = [bit, list(col), 1]
            else:
                e[0] |= bit
                e[1][0] += col[0]
                e[1][1] += col[1]
                e[1][2] += col[2]
                e[2] += 1
        overlay = {}
        for (trow, col), (mask, csum, cnt) in cells.items():
            fg = (csum[0] // cnt, csum[1] // cnt, csum[2] // cnt)
            t0, t1 = buf[2 * trow][col], buf[2 * trow + 1][col]   # fire behind
            bg = ((t0[0] + t1[0]) // 2, (t0[1] + t1[1]) // 2,
                  (t0[2] + t1[2]) // 2)
            overlay[(trow, col)] = (SEXTANTS[mask], fg, bg)
        return overlay


# --------------------------------------------------------------------------
# formatting + rendering
# --------------------------------------------------------------------------

def fmt_until(resets):
    if resets is None:
        return ""
    secs = int((resets - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"in {d}d{h}h"
    if h:
        return f"in {h}h{m:02d}m"
    if m >= 2:
        return f"in {m}m"
    return f"in {m}:{s:02d}"


def fmt_at(resets):
    if resets is None:
        return ""
    # reset stamps are end-of-hour minus epsilon (..:59:59.97) — round up
    local = (resets + timedelta(seconds=1)).astimezone()
    if (resets - datetime.now(timezone.utc)).total_seconds() < 23 * 3600:
        return time.strftime("%I:%M %p", local.timetuple()).lstrip("0")
    return time.strftime("%a %I %p", local.timetuple()).replace(" 0", " ")


def pct_color(pct):
    return PALETTE[10 + int(min(99.9, pct) * 0.26)]


def _rgb256(r, g, b):
    """Nearest xterm-256 index: 6x6x6 color cube (16-231) + grayscale ramp."""
    if abs(r - g) < 8 and abs(g - b) < 8:          # near-gray -> gray ramp
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + (r - 8) * 24 // 247
    q = lambda v: 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def _sgr(f, b):
    """fg+bg SGR for a half-block cell — 256-color under tmux, else truecolor."""
    if LOWCOLOR:
        return "\x1b[38;5;%d;48;5;%dm" % (_rgb256(*f), _rgb256(*b))
    return "\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm" % (f[0], f[1], f[2],
                                                  b[0], b[1], b[2])


def render(mon, gauges, plan, age, error, rate, sess=None):
    out = []
    row = 1

    def line(s):
        nonlocal row
        out.append("\x1b[%d;1H\x1b[2K" % row)
        out.append(s)
        row += 1

    right = f"claude usage · {plan}"
    line(WARN + " BURNOUT" + RESET + DIM + right.rjust(WIDTH - 8)[:WIDTH - 8] + RESET)

    buf, overlay, extra = mon.frame()
    hd = len(buf[0]) == WIDTH * 2          # quadrant scene: 2x2 px per cell
    for ty in range(mon.rows):
        top, bot = buf[2 * ty], buf[2 * ty + 1]
        parts = ["\x1b[%d;1H" % row]
        last = None
        for x in range(WIDTH):
            cell = overlay.get((ty, x)) if overlay else None
            if cell:
                ch, f, b = cell
                if b is None:
                    b = bot[2 * x] if hd else bot[x]
            elif hd:
                p = (top[2 * x], top[2 * x + 1], bot[2 * x], bot[2 * x + 1])
                l = tuple(c[0] + c[1] + c[2] for c in p)
                lo, hi = min(l), max(l)
                if hi - lo < 30:                     # flat cell
                    b = ((p[0][0] + p[1][0] + p[2][0] + p[3][0]) // 4,
                         (p[0][1] + p[1][1] + p[2][1] + p[3][1]) // 4,
                         (p[0][2] + p[1][2] + p[2][2] + p[3][2]) // 4)
                    ch, f = " ", b
                else:                                # split bright/dark
                    thr = (hi + lo) / 2
                    mask = 0
                    br, dk = [], []
                    for i in range(4):
                        if l[i] > thr:
                            mask |= 8 >> i
                            br.append(p[i])
                        else:
                            dk.append(p[i])
                    f = (sum(c[0] for c in br) // len(br),
                         sum(c[1] for c in br) // len(br),
                         sum(c[2] for c in br) // len(br))
                    b = ((sum(c[0] for c in dk) // len(dk),
                          sum(c[1] for c in dk) // len(dk),
                          sum(c[2] for c in dk) // len(dk)) if dk else f)
                    ch = QUADS[mask]
            else:
                ch, f, b = "▀", top[x], bot[x]
            if (f, b) != last:
                parts.append(_sgr(f, b))
                last = (f, b)
            parts.append(ch)
        parts.append(RESET)
        out.append("".join(parts))
        row += 1
    for s in extra:
        line(s)

    # status line
    status = mon.status_line(gauges)
    if status is not None:
        line(status)
    elif error and not gauges:
        line(WARN + (" " + error)[:WIDTH] + RESET)
    elif not gauges:
        line(DIM + " connecting to anthropic..." + RESET)
    elif error:
        left = f" {error}"
        tail = f"data {int((age or 0) / 60)}m old "
        pad = WIDTH - len(left) - len(tail)
        line(WARN + left + " " * max(1, pad) + tail + RESET)
    elif int(mon.t / 8) % 2 and sess and sess.get("model"):
        left = " " + sess["model"]
        if sess.get("effort"):
            left += " · " + sess["effort"] + " effort"
        left = left[:WIDTH - 8]
        st = sess.get("status") or ""
        pad = WIDTH - len(left) - len(st) - 1
        line(DIM + left + " " * max(1, pad)
             + (WARN if st == "busy" else DIM) + st + " " + RESET)
    else:
        left = (f" burning {rate:.1f}%/min" if rate > 0.05 else " idle")
        a = int(age or 0)
        tail = f"poll {a}s " if a < 90 else f"poll {a // 60}m "
        pad = WIDTH - len(left) - len(tail)
        line(DIM + left + " " * max(1, pad) + tail + RESET)

    # gauges: 7-char label + 23-char bar + 5-char pct + 9-char reset = 44
    if gauges:
        until_view = (mon.view_mode == "until"
                      or (mon.view_mode == "auto" and int(mon.t / 8) % 2))
        for g in gauges:
            pct = mon.gauge_pct(g)
            filled = int(round(pct * 23 / 100))
            c = pct_color(pct)
            bar_c = ("\x1b[38;5;%dm" % _rgb256(*c)) if LOWCOLOR \
                else ("\x1b[38;2;%d;%d;%dm" % c)
            bar = bar_c + "█" * filled + DIM + "·" * (23 - filled) + RESET
            label = fmt_until(g["resets"]) if until_view else fmt_at(g["resets"])
            line(DIM + ("%-7s" % g["label"][:7]) + RESET + bar
                 + ("%4d%%" % round(pct)) + DIM + (" %8s" % label[:8]) + RESET)
    else:
        for i in range(3):
            line(DIM + (" fetching usage data..." if i == 0 else "") + RESET)

    line(DIM + " s scene  b demo  t view  r refresh  q quit" + RESET)
    return "".join(out)


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def gauge_rows_for(gauges):
    return len(gauges) if gauges else 3


def run_tui(check=False, scene="fire", dock=False):
    try:
        read_creds()
    except Exception as e:
        sys.exit(f"can't read {CREDS_PATH} ({e}) — log into claude code first")

    client = UsageClient()
    if check:
        try:
            gauges, plan = fetch_usage()
            with client.lock:
                client.gauges, client.plan = gauges, plan
                client.fetched_at = time.monotonic()
        except Exception as e:
            cached = load_cache()
            if cached:
                with client.lock:
                    client.gauges, client.plan = cached[0], cached[1]
                    client.fetched_at = time.monotonic() - cached[2]
            print(f"check: fetch failed ({e}); using "
                  f"{'cache' if cached else 'no data'}", file=sys.stderr)
    else:
        client.start()

    cols, rows = shutil.get_terminal_size((80, 32))
    gauges, plan, age, error, rate = client.snapshot()
    base_rows = max(10, rows - 3 - gauge_rows_for(gauges))
    fire = Monitor(client, base_rows)
    tok = TokenScene(client, base_rows - 1)   # one row goes to the prompt
    inv = InvaderScene(client, base_rows)
    cube = CubeScene(client, base_rows)
    wat = WaterScene(client, base_rows)       # parked: not in the s-cycle
    order = [fire, tok, inv, cube]
    mon = {"fire": fire, "tokens": tok, "invaders": inv, "cube": cube,
           "water": wat}.get(scene, fire)
    # if we wake up already rate-limited, start the fire in the ash state
    s = fire.session(gauges)
    if s and s["pct"] >= 99.5:
        fire._make_mound()
        fire.state = "ash"

    interactive = sys.stdin.isatty() and not check
    # Synchronized output (DEC 2026) makes a terminal apply each frame
    # atomically — great in a plain window. We use it only OUTSIDE tmux: tmux
    # renders the pane itself, and pushing app-level sync through tmux (plus
    # the old Sync terminal-override) can wedge the whole session on modern
    # tmux/terminal combos. The dock pane runs with $TMUX set, so this
    # auto-disables there.
    sync = not os.environ.get("TMUX")
    # Under tmux, keep truecolor but cap the frame rate (tmux re-renders every
    # cell on one thread; a low fixed rate keeps it relaxed). We do NOT switch
    # to 256-color: measured byte-rate is tiny either way, and iTerm2 under tmux
    # freezes on the indexed-color path while truecolor stays smooth — so the
    # encoding, not the volume, was the culprit. BURNOUT_LOWCOLOR=1 opts back
    # into 256-color for terminals that genuinely prefer it.
    under_tmux = bool(os.environ.get("TMUX"))
    global FPS, LOWCOLOR
    if under_tmux:
        LOWCOLOR = bool(os.environ.get("BURNOUT_LOWCOLOR"))
        if not _FPS_SET:
            FPS = min(FPS, DOCK_FPS)
    fd = old_attrs = None
    if interactive:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")

    frame = 0
    last_cols = cols
    sess, sess_next = None, 0.0
    prev_rows = None              # last frame split per-row, for diffing
    # run at the target rate; under tmux the loop only ratchets this DOWN if
    # writes back up — it never climbs (climbing walks into the freeze)
    eff_fps = float(FPS)
    try:
        next_t = time.monotonic()
        while True:
            if check:
                if frame >= 240:
                    break
                if frame in (80, 160):        # exercise every rotation scene
                    mon = order[(order.index(mon) + 1) % len(order)] \
                        if mon in order else order[0]
            gauges, plan, age, error, rate = client.snapshot()
            if time.monotonic() >= sess_next:
                sess = claude_session_info()
                sess_next = time.monotonic() + 2.0
            busy = bool(sess and sess.get("status") == "busy")
            # ultracode effort -> the fire burns violet
            eff = ((sess or {}).get("effort") or "").lower()
            fire.ultra = eff in ("ultracode", "xhigh", "ultra")
            mon.update(gauges, rate, busy)
            frame_out = render(mon, gauges, plan, age, error, rate, sess)
            # row-diff: resend only the rows that changed since last frame, so
            # tmux has far less to parse (huge win when the fire is short and
            # most rows are static void). Falls back to a full paint whenever
            # the layout shifts (row count differs) or after a screen clear.
            rows = _ROWSPLIT.split(frame_out)
            if prev_rows is not None and len(rows) == len(prev_rows):
                payload = "".join(r for r, p in zip(rows, prev_rows) if r != p)
            else:
                payload = frame_out
            prev_rows = rows
            wrote, write_dt = bool(payload), 0.0
            if wrote:
                if sync:   # atomic outside tmux; inside tmux, tmux redraws
                    payload = "\x1b[?2026h" + payload + "\x1b[?2026l"
                t_w = time.monotonic()
                sys.stdout.write(payload)
                sys.stdout.flush()
                write_dt = time.monotonic() - t_w
            frame += 1

            if frame % 28 == 0:
                ncols, nrows = shutil.get_terminal_size((80, 32))
                # a dock pane keeps itself at 44 cols: when the terminal
                # jumps displays/sizes, tmux regrows panes proportionally
                if dock and ncols != WIDTH and os.environ.get("TMUX_PANE"):
                    subprocess.run(
                        ["tmux", "resize-pane", "-t",
                         os.environ["TMUX_PANE"], "-x", str(WIDTH)],
                        stderr=subprocess.DEVNULL)
                want = max(10, nrows - 3 - gauge_rows_for(gauges)) \
                    - (1 if mon is tok else 0)
                if want != mon.rows or ncols != last_cols:
                    if want != mon.rows:
                        mon.resize(want)
                    last_cols = ncols
                    sys.stdout.write("\x1b[2J")
                    prev_rows = None          # cleared screen: repaint in full

            if not interactive:
                continue
            # under tmux, ratchet DOWN only: a slow write means the pane's pty
            # backed up (tmux saturated) — ease off so tmux regains idle time
            # for input and the sibling pane. We never climb back up; tmux's
            # backlog hides behind buffering, so a higher rate looks fine right
            # up until the whole session is already wedged.
            if under_tmux and wrote and write_dt > 0.06:
                eff_fps = max(DOCK_FPS_MIN, eff_fps * 0.7)   # backed up: ease off
            next_t += 1.0 / eff_fps
            now = time.monotonic()
            if next_t < now - 0.25:   # fell behind (lag/suspend): drop the
                next_t = now          # debt instead of spinning to catch up
            timeout = max(0.0, next_t - now)
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                continue
            for ch in os.read(fd, 32).decode(errors="ignore"):
                if ch == "q":
                    return
                elif ch == "s":
                    mon = order[(order.index(mon) + 1) % len(order)] \
                        if mon in order else order[0]
                    _, nrows = shutil.get_terminal_size((80, 32))
                    want = max(10, nrows - 3 - gauge_rows_for(gauges)) \
                        - (1 if mon is tok else 0)
                    if want != mon.rows:
                        mon.resize(want)
                    sys.stdout.write("\x1b[2J")
                    prev_rows = None          # cleared screen: repaint in full
                elif ch == "t":
                    nv = {"auto": "at", "at": "until",
                          "until": "auto"}[mon.view_mode]
                    for m in (fire, tok, inv, cube, wat):
                        m.view_mode = nv
                elif ch == "r":
                    client.force()
                elif ch == "b":
                    mon.demo()
    finally:
        sys.stdout.write(RESET + "\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if old_attrs is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def run_once():
    gauges, plan = fetch_usage()
    save_cache(gauges, plan)
    print(f"plan: {plan}")
    for g in gauges:
        at, until = fmt_at(g["resets"]), fmt_until(g["resets"])
        print(f"{g['label']:<7}{g['pct']:5.1f}%   resets {at}  ({until})")


def run_side(cmd, scene="fire"):
    self_cmd = (f"{shlex.quote(sys.executable)} "
                f"{shlex.quote(os.path.abspath(__file__))} --dock")
    if scene != "fire":
        self_cmd += f" --scene {shlex.quote(scene)}"
    if _FPS_SET:                       # forward an explicit rate; otherwise the
        self_cmd += f" --fps {FPS}"    # dock auto-caps to DOCK_FPS under tmux
    inner = cmd or [os.environ.get("SHELL", "bash")]
    if os.environ.get("TMUX"):
        out = subprocess.run(["tmux", "split-window", "-h", "-l", str(WIDTH),
                              "-d", "-P", "-F", "#{pane_id}", self_cmd],
                             check=True, capture_output=True, text=True)
        pane = out.stdout.strip()
        rc = subprocess.call(inner)
        subprocess.run(["tmux", "kill-pane", "-t", pane],
                       stderr=subprocess.DEVNULL)
        sys.exit(rc)
    if shutil.which("tmux"):
        inner_str = " ".join(shlex.quote(c) for c in inner)
        os.execvp("tmux", [
            "tmux", "new-session", f"{inner_str}; tmux kill-session", ";",
            "split-window", "-h", "-l", str(WIDTH), "-d", self_cmd, ";",
            "set-option", "mouse", "on",
        ])
    if shutil.which("gnome-terminal"):
        subprocess.Popen(["gnome-terminal", "--geometry",
                          f"{WIDTH + 1}x50", "--", sys.executable,
                          os.path.abspath(__file__)])
        print("monitor opened in a new window (tip: Super+arrows to tile;"
              " install tmux for true in-terminal splits)")
        sys.exit(subprocess.call(inner) if cmd else 0)
    sys.exit("side mode needs tmux (apt/dnf/brew install tmux)")


def run_window(scene):
    """Own terminal window: no shared cursor with your work = no flicker."""
    self_args = [sys.executable, os.path.abspath(__file__)]
    if scene != "fire":
        self_args += ["--scene", scene]
    if FPS != 28:
        self_args += ["--fps", str(FPS)]
    if shutil.which("gnome-terminal"):
        subprocess.Popen(["gnome-terminal", "--geometry", f"{WIDTH}x50",
                          "--"] + self_args)
    elif shutil.which("kitty"):
        subprocess.Popen(["kitty", "-o", "remember_window_size=no",
                          "-o", f"initial_window_width={WIDTH}c",
                          "-o", "initial_window_height=50c"] + self_args)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal",
                          os.path.abspath(__file__)])
    else:
        sys.exit("window mode needs gnome-terminal or kitty")
    print("monitor opened in its own window — snap it aside with Super+arrow")


def main():
    argv = sys.argv[1:]
    scene = "fire"
    if "--scene" in argv:
        i = argv.index("--scene")
        if i + 1 >= len(argv) or argv[i + 1] not in ("fire", "tokens",
                                                     "invaders", "cube",
                                                     "water"):
            sys.exit("--scene needs 'fire', 'tokens', 'invaders' or 'cube'")
        scene = argv[i + 1]
        del argv[i:i + 2]
    dock = "--dock" in argv
    if dock:
        argv.remove("--dock")
    if "--fps" in argv:
        i = argv.index("--fps")
        try:
            global FPS, _FPS_SET
            FPS = clamp(int(argv[i + 1]), 5, 60)
            _FPS_SET = True
        except (IndexError, ValueError):
            sys.exit("--fps needs a number (5-60)")
        del argv[i:i + 2]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
    elif argv and argv[0] == "--once":
        run_once()
    elif argv and argv[0] == "--check":
        run_tui(check=True, scene=scene)
    elif argv and argv[0] == "side":
        run_side(argv[1:], scene)
    elif argv and argv[0] == "window":
        run_window(scene)
    else:
        run_tui(scene=scene, dock=dock)


if __name__ == "__main__":
    main()

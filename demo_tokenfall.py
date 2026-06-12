#!/usr/bin/env python3
"""TOKENFALL — concept demo: watch claude generate, tokenize, and archive.

A prompt line at the bottom "generates" text. Each finished line gets
tokenized — every token lights up as a colored pill, exactly like a
tokenizer playground — then the tokens lift off one by one and fly up into
the context wall: a mosaic archive of everything you've already spent.
The wall grows DOWN toward the prompt as your session quota fills, so the
dark gap that remains *is* your remaining quota. Long words split into
subword pieces mid-line. Landing tokens occasionally fire a faint blue
attention glimmer back to an archived token.

At 100% the last slit of dark seals shut, the archive freezes over cold,
and the caret blinks alone — CONTEXT FULL. Tokens that try to emit bounce
off and fall as debris. On reset the wall shatters bottom-up and the whole
archive avalanches away past the prompt: compaction. Then it starts again.

Demo only: usage data is simulated and auto-plays the whole story on loop.

Keys:  <-/->  scrub usage      up/down  burn rate     a  auto-play
       f  fast-forward to full     c  compact now     q  quit

Pure stdlib. Truecolor; happiest in a 44-col pane (claude-monitor side).
"""

import math
import os
import random
import re
import select
import shutil
import sys
import time

WIDTH = 44
FPS = 28

# tokenizer-playground pill colors; adjacent tokens always differ
PASTELS = [
    (130, 170, 255),   # periwinkle
    (195, 232, 141),   # green
    (255, 203, 107),   # amber
    (199, 146, 234),   # purple
    (137, 221, 255),   # cyan
    (240, 113, 120),   # coral
]
INK = (13, 13, 26)            # text color inside a pill
ACCENT = (137, 221, 255)
HOT = (255, 110, 70)          # pill tint under context pressure
TEXT = (210, 212, 228)

CORPUS = [
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
GHOST_FRAG = "attention is all you need "   # filler glyphs for ghost accretion

RESET = "\x1b[0m"
DIM = "\x1b[38;2;120;115;135m"
WARN = "\x1b[38;2;255;140;90m"


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp3(a, b, k):
    return (int(a[0] + (b[0] - a[0]) * k),
            int(a[1] + (b[1] - a[1]) * k),
            int(a[2] + (b[2] - a[2]) * k))


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
    out, col = [], 2                       # 2 = width of the "> " prompt
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
        ch = GHOST_FRAG[(col + row * 11) % len(GHOST_FRAG)]
        self.place(row, col, ch, (105, 112, 150), 0.0)
        return True

    def shed(self):
        """Break one tile off the deepest edge; returns debris seed or None."""
        maxd = max(self.depth)
        if maxd <= 0:
            return None
        col = random.choice([c for c in range((self.cols))
                             if self.depth[c] == maxd])
        row = maxd - 1
        t = self.tiles.pop((row, col), None)
        self.flashes.pop((row, col), None)
        self.depth[col] = row
        ch, color = t if t else ("·", (90, 90, 110))
        return (col, row, ch, color)


class Demo:
    def __init__(self, rows):
        self.rows = rows                  # canvas height in cell rows
        self.t = 0.0
        self.dt = 1.0 / FPS
        self.pct = 8.0
        self.rate = 0.25                  # 0..1 burn intensity
        self.auto = True
        self.state = "stream"             # stream | seal | frozen | compact
        self.frozen_until = 0.0
        self.wall = Wall(WIDTH)
        self.chips = []                   # flying tokens
        self.debris = []                  # falling characters
        self.links = []                   # attention glimmers (px line + age)
        self.trails = []                  # rising motion pixels
        self._accrete_budget = 0.0
        self._shed_budget = 0.0
        # typing machine
        self.sentence = random.choice(CORPUS)[:WIDTH - 3]
        self.typed = 0
        self.tokens = []
        self.launched = 0
        self.phase = "type"               # type | hold | launch | pause
        self.phase_t0 = 0.0
        # auto-play burn schedule: (seconds, target rate)
        self.sched = [(5, 0.06), (9, 0.40), (6, 0.95),
                      (5, 0.45), (4, 0.12), (7, 0.80)]
        self.sched_i = 0
        self.sched_t0 = 0.0

    # ---- knobs ----------------------------------------------------------

    def gap_rows(self):
        return self.rows - self.wall.mean_depth()

    def target_cols(self):
        if self.state in ("seal", "frozen"):
            base = float(self.rows)
            return [self.rows] * WIDTH
        if self.state == "compact":
            return [1] * WIDTH
        base = self.pct / 100.0 * (self.rows - 5)
        return [int(clamp(base + r * min(1.0, base / 3.0), 0, self.rows - 4))
                for r in self.wall.rag]

    # ---- per-frame ------------------------------------------------------

    def update(self):
        self.t += self.dt
        t = self.t

        if self.auto and self.state == "stream":
            dur, want = self.sched[self.sched_i]
            if t - self.sched_t0 > dur:
                self.sched_i = (self.sched_i + 1) % len(self.sched)
                self.sched_t0 = t
                dur, want = self.sched[self.sched_i]
            self.rate += (want - self.rate) * min(1.0, 1.5 * self.dt)
            self.pct = min(100.0, self.pct + self.rate * 2.4 * self.dt)

        # ---- state machine
        if self.state == "stream" and self.pct >= 99.5:
            self.state = "seal"
        elif self.state == "seal":
            if self.wall.mean_depth() >= self.rows - 0.2:
                self.state = "frozen"
                self.frozen_until = t + 8.0
                for c in range(WIDTH):                     # seam flash
                    self.wall.flashes[(self.rows - 1, c)] = 1.0
        elif self.state == "frozen":
            if int(t * 0.45) != int((t - self.dt) * 0.45):  # bounced emission
                self.debris.append({"x": 2.0 + random.random() * 6,
                                    "y": self.rows * 2 - 1.0,
                                    "vx": random.uniform(-2, 2),
                                    "vy": -16.0, "ch": "·",
                                    "color": (240, 113, 120),
                                    "age": 0.0, "T": 2.2})
            if t >= self.frozen_until:
                self.state = "compact"
        elif self.state == "compact":
            if self.wall.mean_depth() <= 1.5:
                self.state = "stream"
                self.pct = 5.0
                self.sched_i, self.sched_t0 = 0, t
                self._new_sentence()

        # ---- typing machine (only while streaming)
        if self.state == "stream":
            self._type(t)

        # ---- flying chips
        H = self.rows * 2
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

        # ---- reconcile wall toward target (growth + avalanche, one engine)
        target = self.target_cols()
        mean_t = sum(target) / WIDTH
        gap = mean_t - self.wall.mean_depth()
        if gap > 0 and self.state != "compact":
            speed = 26.0 if self.state == "seal" else 2.2
            self._accrete_budget += self.dt * speed * gap
            while self._accrete_budget >= 1.0:
                self._accrete_budget -= 1.0
                if not self.wall.accrete(target):
                    break
        excess = self.wall.mean_depth() - mean_t
        if excess > 0.4 or self.state == "compact":
            self._shed_budget += self.dt * clamp(30.0 * max(excess, 0.5),
                                                 4.0, 900.0)
            while self._shed_budget >= 1.0:
                self._shed_budget -= 1.0
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

        # ---- particles
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

    def _new_sentence(self):
        self.sentence = random.choice(CORPUS)[:WIDTH - 3]
        self.typed = 0
        self.tokens = []
        self.launched = 0
        self.phase = "type"
        self.phase_t0 = self.t

    def _type(self, t):
        if self.phase == "type":
            cps = 3.0 + 26.0 * self.rate
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
                    "vy": -(16.0 + 10.0 * self.rate + random.uniform(-3, 3)),
                    "wob": random.uniform(0, 6.28)})
                self.launched += 1
            if self.launched >= len(self.tokens):
                self.phase, self.phase_t0 = "pause", t
        elif self.phase == "pause":
            wait = 3.2 - 3.0 * min(1.0, self.rate)
            if t - self.phase_t0 > wait:
                self._new_sentence()


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

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


def pct_color(pct):
    if pct < 55:
        return lerp3((120, 200, 140), (255, 203, 107), pct / 55.0)
    return lerp3((255, 203, 107), (240, 113, 120), (pct - 55) / 45.0)


def bar_row(label, pct, when):
    filled = int(round(pct * 23 / 100))
    c = pct_color(pct)
    return (DIM + ("%-7s" % label[:7]) + RESET
            + ("\x1b[38;2;%d;%d;%dm" % c) + "█" * filled
            + DIM + "·" * (23 - filled) + RESET
            + ("%4d%%" % round(pct)) + DIM + (" %8s" % when[:8]) + RESET)


def render(d):
    out = []
    row = 1

    def line(s):
        nonlocal row
        out.append("\x1b[%d;1H\x1b[2K" % row)
        out.append(s)
        row += 1

    line(WARN + " TOKENFALL" + RESET
         + DIM + "claude usage · demo".rjust(WIDTH - 10) + RESET)

    # ---- canvas: pixel layer ------------------------------------------------
    H = d.rows * 2
    buf = []
    for py in range(H):
        shade = 4 + py * 5 // H
        buf.append([(shade, shade + 1, shade + 5)] * WIDTH)
    for p in d.trails:
        add_px(buf, p[0], p[1], p[2], (1.0 - p[3] / p[4]) * 0.35)
    for l in d.links:
        line_px(buf, l[0], l[1], l[2], l[3], (45, 95, 115),
                (1.0 - l[4] / l[5]) * 0.5)

    # ---- canvas: text overlay -------------------------------------------
    overlay = {}
    frozen = d.state == "frozen"
    breath = 0.85 + 0.15 * math.sin(d.t * 1.5)
    pressure = clamp((8.0 - d.gap_rows()) / 8.0, 0.0, 1.0) \
        if d.state == "stream" else 0.0
    for (r, c), (ch, col) in d.wall.tiles.items():
        if r >= d.rows:
            continue
        if frozen:
            bg = (int(col[0] * 0.08) + 3, int(col[1] * 0.11) + 4,
                  int(col[2] * 0.30) + 10)
            fg = (int(col[0] * 0.30 * breath), int(col[1] * 0.36 * breath),
                  int(col[2] * 0.60 * breath))
        else:
            bg = (int(col[0] * 0.16) + 5, int(col[1] * 0.18) + 5,
                  int(col[2] * 0.30) + 8)
            fg = (int(col[0] * 0.55), int(col[1] * 0.58), int(col[2] * 0.72))
        f = d.wall.flashes.get((r, c), 0.0)
        if f > 0:
            bg = lerp3(bg, col, f * 0.9)
            fg = lerp3(fg, (250, 250, 255), f)
        overlay[(r, c)] = (ch, fg, bg)
    for chp in d.chips:
        crow = int(clamp(chp["y"] // 2, 0, d.rows - 1))
        pill = lerp3(chp["color"], HOT, pressure)
        c0 = int(clamp(round(chp["x"]), 0, WIDTH - 1))
        for i, ch in enumerate(chp["text"]):
            if c0 + i < WIDTH:
                overlay[(crow, c0 + i)] = (ch, INK, pill)
    for de in d.debris:
        crow, c = int(de["y"] // 2), int(de["x"])
        if 0 <= crow < d.rows and 0 <= c < WIDTH:
            k = 1.0 - de["age"] / de["T"]
            overlay[(crow, c)] = (de["ch"], lerp3((30, 30, 40),
                                                  de["color"], k), None)

    # ---- canvas: compose mixed pixel/text rows ---------------------------
    for r in range(d.rows):
        parts = ["\x1b[%d;1H" % row]
        last = None
        for x in range(WIDTH):
            cell = overlay.get((r, x))
            if cell:
                ch, fg, bg = cell
                if bg is None:
                    bg = buf[2 * r + 1][x]
                key = (fg, bg)
                if key != last:
                    parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm"
                                 % (fg + bg))
                    last = key
                parts.append(ch)
            else:
                key = (buf[2 * r][x], buf[2 * r + 1][x])
                if key != last:
                    parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm"
                                 % (key[0] + key[1]))
                    last = key
                parts.append("▀")
        parts.append(RESET)
        out.append("".join(parts))
        row += 1

    # ---- typing line ------------------------------------------------------
    caret_on = (d.t * 2.2) % 1.0 < 0.55
    prompt = "\x1b[38;2;%d;%d;%dm❯ " % ACCENT
    if d.state == "stream" and d.phase == "type":
        txt = d.sentence[:d.typed]
        body = ("\x1b[38;2;%d;%d;%dm" % TEXT) + txt \
            + (("\x1b[38;2;%d;%d;%dm▌" % ACCENT) if caret_on else " ")
        line(prompt + body + RESET)
    elif d.state == "stream" and d.phase in ("hold", "launch"):
        parts = [prompt]
        for j, tok in enumerate(d.tokens):
            if d.phase == "launch" and j < d.launched:
                parts.append(" " * len(tok["text"]))
            else:
                c = tok["color"]
                parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm%s\x1b[49m"
                             % (INK + c + (tok["text"],)))
        line("".join(parts) + RESET)
    else:
        cc = (240, 113, 120) if d.state in ("frozen", "seal") else ACCENT
        car = ("\x1b[38;2;%d;%d;%dm▌" % cc) if caret_on else " "
        line(prompt + car + RESET)

    # ---- status -----------------------------------------------------------
    if d.state == "stream":
        toks = (3.0 + 26.0 * d.rate) / 4.2
        left = (f" streaming · {toks:.1f} tok/s" if d.rate > 0.08 else " idle")
        if pressure > 0.55:
            left = WARN + f" context pressure · {toks:.0f} tok/s" + RESET + DIM
        tail = f"{d.pct:3.0f}% "
        line(DIM + left + " " * max(1, WIDTH - 21 - len(tail)) + tail + RESET)
    elif d.state == "seal":
        line(WARN + " sealing context window…" + RESET)
    elif d.state == "frozen":
        n = max(0, int(d.frozen_until - d.t + 0.999))
        line(WARN + f" CONTEXT FULL — resets in 0:{n:02d}" + RESET)
    else:
        line(WARN + " compacting — tokens flying away" + RESET)

    # ---- gauges + footer ----------------------------------------------------
    line(bar_row("SES", d.pct, "in 2h08m"))
    line(bar_row("WEEK", 27 + d.pct * 0.15, "Tue 11AM"))
    line(bar_row("SONNET", 9 + d.pct * 0.05, "Tue 11AM"))
    line(DIM + " ←→ usage ↑↓ rate  a auto  f full  c cmpct"[:WIDTH] + RESET)
    return "".join(out)


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def run():
    cols, rows = shutil.get_terminal_size((80, 32))
    d = Demo(max(14, rows - 7))

    interactive = sys.stdin.isatty()
    fd = old_attrs = None
    if interactive:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        next_t = time.monotonic()
        while True:
            d.update()
            sys.stdout.write(render(d))
            sys.stdout.flush()
            if not interactive:
                continue
            next_t += 1.0 / FPS
            now = time.monotonic()
            if next_t < now - 0.25:   # fell behind (lag/suspend): drop the
                next_t = now          # debt instead of spinning to catch up
            ready, _, _ = select.select(
                [sys.stdin], [], [], max(0.0, next_t - now))
            if not ready:
                continue
            data = os.read(fd, 64).decode(errors="ignore")
            for ch in data:
                if ch == "q":
                    return
                elif ch == "a":
                    d.auto = True
                elif ch == "f" and d.state == "stream":
                    d.pct = 99.6
                elif ch == "c" and d.state != "compact":
                    d.state = "compact"
            if "\x1b[C" in data:
                d.auto, d.pct = False, min(100.0, d.pct + 3.0)
            if "\x1b[D" in data:
                d.auto, d.pct = False, max(0.0, d.pct - 3.0)
            if "\x1b[A" in data:
                d.auto, d.rate = False, min(1.0, d.rate + 0.15)
            if "\x1b[B" in data:
                d.auto, d.rate = False, max(0.0, d.rate - 0.15)
    finally:
        sys.stdout.write(RESET + "\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if old_attrs is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def run_check():
    """Headless smoke test: drive the full story, assert every state."""
    random.seed(7)
    d = Demo(24)
    d.auto, d.rate, d.pct = False, 0.95, 30.0
    t0 = time.monotonic()
    frames = 0
    for _ in range(280):
        d.update()
        render(d)
        frames += 1
    grew = d.wall.mean_depth()
    assert grew > 1.0, f"wall did not grow (depth {grew:.2f})"
    assert d.wall.tiles, "no tiles landed"
    d.pct = 99.6
    for _ in range(1200):
        d.update()
        frames += 1
        if d.state == "frozen":
            break
    assert d.state == "frozen", f"never froze (state={d.state})"
    d.frozen_until = d.t
    for _ in range(800):
        d.update()
        frames += 1
        if d.state == "stream":
            break
    assert d.state == "stream", "compaction never finished"
    assert d.wall.mean_depth() < 3.0, "wall not cleared"
    out = render(d)
    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out)
    dt = time.monotonic() - t0
    print(f"check ok: {frames} frames in {dt:.2f}s "
          f"({frames / dt:.0f} fps headless)")
    print(f"  peak tiles {grew:.1f} rows deep -> "
          f"cleared to {d.wall.mean_depth():.1f}")
    for ln in plain.splitlines():
        if "TOKENFALL" in ln or "SES" in ln or "%" in ln:
            print("  |" + ln.rstrip()[:WIDTH])
            break


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
    elif argv and argv[0] == "--check":
        run_check()
    else:
        run()


if __name__ == "__main__":
    main()

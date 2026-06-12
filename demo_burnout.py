#!/usr/bin/env python3
"""Burnout — animation concept demo for the Claude usage monitor.

A pixel fire that grows as you burn through your session quota, burns out
when you hit the rate limit (ash, smoke, pulsing embers, reset countdown),
and reignites on reset. Pure stdlib, truecolor half-block pixels.

Run:   python3 demo_burnout.py
Keys:  left/right  scrub usage      a  toggle auto-play
       b           force burnout    t  toggle "resets at" / "time until"
       q           quit

Headless smoke test (no tty needed):  python3 demo_burnout.py --check
"""

import math
import os
import random
import select
import shutil
import sys
import time

WIDTH = 44          # dock width in columns = pixels across
FPS = 28
BURNOUT_SECS = 8.0  # demo countdown while rate-limited
REIGNITE_SECS = 1.8

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
MAXHEAT = len(PALETTE) - 1  # 36

RESET = "\x1b[0m"
DIM = "\x1b[38;2;120;110;100m"
WARN = "\x1b[38;2;255;120;60m"


class Fire:
    """Doom-fire cellular automaton. Row 0 is the top, row h-1 is the source."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.cells = [[0] * w for _ in range(h)]

    def step(self, heat, gust, source_on=True):
        """heat 0..1 -> flame height/intensity; gust modulates decay (flicker)."""
        w, h = self.w, self.h
        r = random.random
        # Target flame height in pixels; mean per-row decay makes it emerge.
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
                # int(mu*jitter + U[0,1)) dithers fractional decay to the right mean
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


class Demo:
    def __init__(self, fire_rows):
        self.t = 0.0                  # simulated clock (seconds)
        self.dt = 1.0 / FPS
        self.usage = 12.0             # session %
        self.auto = True
        self.show_until = False       # False: "resets at", True: "time until"
        self.state = "normal"         # normal | dying | ash | reignite
        self.state_t0 = 0.0
        self.fire = Fire(WIDTH, fire_rows * 2)
        self.fire_rows = fire_rows
        self.sparks = []              # [x, y, vy, age, life]
        self.smoke = []               # [x, y, phase, age, life]
        self.mound = []
        self.embers = []
        self.launch_wall = time.time()
        self.ses_reset_in = 2 * 3600 + 41 * 60   # fake: 2h41m from launch
        self.week_reset_in = 3 * 86400 + 14 * 3600

    def resize(self, fire_rows):
        self.fire_rows = fire_rows
        self.fire = Fire(WIDTH, fire_rows * 2)
        if self.state == "ash":
            self._make_mound()

    # ---- state machine -------------------------------------------------

    def begin_burnout(self):
        if self.state == "normal":
            self.usage = 100.0
            self.state, self.state_t0 = "dying", self.t

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

    def update(self):
        self.t += self.dt
        t = self.t

        if self.state == "normal":
            if self.auto:
                rate = 5.5 + 2.5 * math.sin(t * 0.6)       # %/sec, demo speed
                self.usage = min(100.0, self.usage + rate * self.dt)
                if self.usage >= 100.0:
                    self.state, self.state_t0 = "dying", t
            heat = self.usage / 100.0
            gust = 1.0 + 0.25 * math.sin(t * 1.7) + 0.15 * math.sin(t * 4.3 + 1.0)
            self.fire.step(heat, max(0.4, gust))
            self._update_sparks(heat)

        elif self.state == "dying":
            self.fire.step(1.0, 2.2, source_on=False)
            self._spawn_smoke(prob=0.5)
            if self.fire.max_heat() < 4 or t - self.state_t0 > 2.5:
                self.fire.clear()
                self._make_mound()
                self.state, self.state_t0 = "ash", t

        elif self.state == "ash":
            self._spawn_smoke(prob=0.25)
            if t - self.state_t0 >= BURNOUT_SECS:
                self.usage = 0.0
                self.state, self.state_t0 = "reignite", t

        elif self.state == "reignite":
            p = min(1.0, (t - self.state_t0) / REIGNITE_SECS)
            heat = 0.15 + 0.5 * math.sin(p * math.pi)       # bloom, then settle
            self._reignite_step(heat, p)
            self._update_sparks(0.8 if p < 0.5 else 0.3)
            if p >= 1.0:
                self.state = "normal"

        self._update_smoke()

    def _reignite_step(self, heat, p):
        # Light the source outward from the center as the flame catches.
        fire = self.fire
        cx = WIDTH // 2
        frontier = int(p * (WIDTH / 2 + 2)) + 1
        fire.step(heat, 1.0, source_on=False)
        bottom = fire.cells[fire.h - 1]
        src = 25 + int(11 * heat)
        for x in range(max(0, cx - frontier), min(WIDTH, cx + frontier)):
            bottom[x] = max(0, src - int(random.random() * 4))

    # ---- particles -----------------------------------------------------

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

    # ---- rendering -----------------------------------------------------

    def pixels(self):
        """Compose the full pixel buffer (h x w of RGB tuples)."""
        H, W = self.fire.h, WIDTH
        buf = [[PALETTE[v] for v in row] for row in self.fire.cells]

        if self.state == "ash" or (self.state == "reignite" and self.mound):
            for x in range(W):
                for dy in range(self.mound[x]):
                    g = 42 + (x * 7 + dy * 13) % 26
                    buf[H - 1 - dy][x] = (g + 10, g, g - 4)
            for (ex, depth, phase) in self.embers:
                s = (math.sin(self.t * 2.2 + phase) + 1) / 2
                buf[H - 1 - depth][ex] = (
                    int(60 + 160 * s), int(20 + 70 * s), 10)

        for p in self.smoke:
            x, y = int(p[0]), int(p[1])
            if 0 <= x < W and 0 <= y < H:
                g = int(30 + 80 * (1 - p[3] / p[4]))
                buf[y][x] = (g, g, g + 6)

        for s in self.sparks:
            x, y = int(s[0]), int(s[1])
            if 0 <= x < W and 0 <= y < H:
                buf[y][x] = (255, 235, 170)
        return buf

    def gauge_rows(self):
        week = min(100.0, 23 + self.usage * 0.18 + 6 * math.sin(self.t * 0.05))
        opus = min(100.0, 9 + self.usage * 0.07)
        return [("SES", self.usage), ("WEEK", week), ("OPUS", opus)]

    def reset_label(self, which):
        if which == "SES":
            if self.state in ("dying", "ash"):
                left = max(0.0, BURNOUT_SECS - (self.t - self.state_t0))
                return "in 0:%02d" % int(left + 0.999)
            secs = max(0, self.ses_reset_in - int(self.t))
            if self.show_until:
                return "in %dh %02dm" % (secs // 3600, (secs % 3600) // 60)
            wall = time.localtime(self.launch_wall + self.ses_reset_in)
            return time.strftime("%I:%M %p", wall).lstrip("0")
        secs = max(0, self.week_reset_in - int(self.t))
        if self.show_until:
            return "in %dd %dh" % (secs // 86400, (secs % 86400) // 3600)
        wall = time.localtime(self.launch_wall + self.week_reset_in)
        return time.strftime("%a %I %p", wall).replace(" 0", " ").lstrip("0")


def pct_color(pct):
    return PALETTE[10 + int(min(99.9, pct) * 0.26)]


def render(demo):
    out = ["\x1b[H"]
    row = 1

    def line(s):
        nonlocal row
        out.append("\x1b[%d;1H\x1b[2K" % row)
        out.append(s)
        row += 1

    line(WARN + " BURNOUT" + RESET + DIM
         + " · claude usage".rjust(WIDTH - 8) + RESET)

    buf = demo.pixels()
    last = None
    for ty in range(demo.fire_rows):
        top, bot = buf[2 * ty], buf[2 * ty + 1]
        parts = ["\x1b[%d;1H" % row]
        for x in range(WIDTH):
            f, b = top[x], bot[x]
            if (f, b) != last:
                parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm"
                             % (f[0], f[1], f[2], b[0], b[1], b[2]))
                last = (f, b)
            parts.append("▀")
        parts.append(RESET)
        out.append("".join(parts))
        last = None
        row += 1

    if demo.state in ("dying", "ash"):
        left = max(0, int(BURNOUT_SECS - (demo.t - demo.state_t0) + 0.999))
        line(WARN + (" RATE LIMITED — burnt out · resets in 0:%02d" % left)
             .ljust(WIDTH)[:WIDTH] + RESET)
    elif demo.state == "reignite":
        line(WARN + " reset! reigniting...".ljust(WIDTH) + RESET)
    else:
        mode = "[auto]" if demo.auto else "[manual]"
        line(DIM + (" burn %.1f%%/s" % (5.5 + 2.5 * math.sin(demo.t * 0.6))
                    if demo.auto else " scrub with arrow keys")
             + mode.rjust(WIDTH - (14 if demo.auto else 23)) + RESET)

    for name, pct in demo.gauge_rows():
        if name == "SES" and demo.state in ("dying", "ash"):
            pct = 100.0
        filled = int(round(pct / 4))
        c = pct_color(pct)
        bar = ("\x1b[38;2;%d;%d;%dm" % c) + "█" * filled \
            + DIM + "·" * (25 - filled) + RESET
        label = demo.reset_label(name)
        line(DIM + ("%-5s" % name) + RESET + bar
             + ("%4d%%" % round(pct)) + DIM + (" %8s" % label[:8]) + RESET)

    line(DIM + " ←→ usage  a auto  b burnout  t view  q quit" + RESET)
    return "".join(out)


def run(check=False):
    cols, rows = shutil.get_terminal_size((80, 32))
    fire_rows = max(10, rows - 6)
    demo = Demo(fire_rows)
    interactive = sys.stdin.isatty() and not check

    fd = None
    old_attrs = None
    if interactive:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")

    frame = 0
    try:
        next_t = time.monotonic()
        while True:
            if check and frame >= 900:
                break
            demo.update()
            sys.stdout.write(render(demo))
            sys.stdout.flush()
            frame += 1

            if frame % 28 == 0:
                ncols, nrows = shutil.get_terminal_size((80, 32))
                nfr = max(10, nrows - 6)
                if nfr != demo.fire_rows:
                    demo.resize(nfr)
                    sys.stdout.write("\x1b[2J")

            if not interactive:
                continue
            next_t += 1.0 / FPS
            now = time.monotonic()
            if next_t < now - 0.25:   # fell behind (lag/suspend): drop the
                next_t = now          # debt instead of spinning to catch up
            timeout = max(0.0, next_t - now)
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                continue
            data = os.read(fd, 32).decode(errors="ignore")
            i = 0
            while i < len(data):
                ch = data[i]
                if ch == "\x1b" and data[i:i + 3] in ("\x1b[C", "\x1b[D"):
                    if demo.state == "normal":
                        demo.auto = False
                        step = 2.0 if data[i + 2] == "C" else -2.0
                        demo.usage = max(0.0, min(99.0, demo.usage + step))
                    i += 3
                    continue
                if ch == "q":
                    return
                elif ch == "a":
                    demo.auto = not demo.auto
                elif ch == "b":
                    demo.begin_burnout()
                elif ch == "t":
                    demo.show_until = not demo.show_until
                i += 1
    finally:
        sys.stdout.write(RESET + "\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if old_attrs is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


if __name__ == "__main__":
    run(check="--check" in sys.argv)

# Animation concepts — Claude usage monitor

Constraints driving everything: 44-column dock (fixed canvas), Unicode half-block
"pixels" (1×2 px per cell, 24-bit color) so it works in GNOME Terminal and through
tmux, glanceable from across the room, runs forever without getting boring.

## Concept lineup

| Concept | Idea | Verdict |
|---|---|---|
| Aquarium | water drains as usage climbs, fish get cramped | cute, but emptiest when you care most |
| Hourglass | falling-sand physics, flips on reset | great time metaphor, weaker drama |
| Pixel pet | creature gets tired as usage climbs | most charming, most hand-drawn art needed |
| Rocket | fuel gauge + starfield, sputters when low | good, thrust = burn rate is nice |
| **Fire ("Burnout")** | **flames grow as you burn tokens, burn out at the limit** | **winner** |

Why fire wins: the metaphor is native ("burning through tokens"), it's 100%
procedural (Doom-fire cellular automaton — no sprite art, any canvas size), it gets
*more* spectacular as you approach the limit, and hitting the rate limit maps to the
single best state name available: **burnout**.

## The Burnout concept

One fire, driven by the session gauge (the most volatile one). State map:

| Usage | Visual |
|---|---|
| 0–15% | cozy ember bed, small calm flames |
| 15–50% | proper campfire, occasional licks |
| 50–80% | roaring fire, flames climb the panel, sparks rise |
| 80–99% | inferno — white-hot core, panel nearly full, heavy sparks |
| 100% (rate limited) | **burnout**: flames lift off and die, ash mound + drifting smoke, embers pulse, countdown to reset |
| reset | reignition — spark catches, fire blooms outward, settles back to cozy |

Live signals layered on top:
- **Burn rate** (tokens/min right now) → gust speed / flicker intensity. Idle claude
  = laminar calm flame; heavy generation = roaring gusts. You can *see* claude working.
- **Flame height + palette** = session %. Doom palette runs black → deep red →
  orange → yellow → white, so "white-hot" naturally reads as danger.

Secondary gauges (later, once real data is plumbed):
- **Weekly limit = the woodpile.** A row of pixel logs at the base of the fire that
  depletes across the week. Glanceable long-term fuel.
- **Opus weekly = a purple-tinted ember vein** in the coals, fading as it depletes.
- Precise numbers always live in slim bars under the canvas: SES / WEEK / OPUS with
  `t` toggling "resets at 4:38 PM" ↔ "in 2h 40m".

## Renderer notes

- Half-blocks `▀` with truecolor fg/bg = 2 px per cell → 44×~80 px canvas in the dock.
- Doom-fire CA: bottom row is the heat source; each frame pixels propagate upward
  losing a random amount of heat; mean decay tunes flame height. Horizontal jitter
  gives the licking motion. Usage % drives source intensity + decay rate.
- Sparks/smoke are tiny particle lists drawn over the CA buffer.
- All state-change moments (burnout, reignite) are *events with animations*, not
  instant redraws — that's what makes it feel alive.

## Demo

`python3 demo_burnout.py` — pure stdlib, no deps. Auto-plays the full story
(climb → inferno → burnout → countdown → reignite, looping). Keys: `←/→` scrub
usage manually, `a` auto-play, `b` force burnout, `t` toggle reset view, `q` quit.

---

## Concept #2: TOKENFALL (2026-06-12)

Where Burnout is an *energy* metaphor, Tokenfall is a *language* metaphor —
the most literally-AI animation possible: watch generation, tokenization, and
context consumption happen.

The loop: a prompt line at the bottom **types** a sentence (claude-isms:
"you're absolutely right!", "// TODO: remove before shipping"...) → the line
**tokenizes**: every token lights up as a colored pill, tokenizer-playground
style, long words splitting into subwords ("deep|ly.") → the tokens **lift
off** one by one and fly up into the **context wall**: a mosaic archive of
every token you've spent, growing DOWN from the top. The dark gap that
remains *is* your remaining quota. Old sentences stay faintly readable in
the wall — a glanceable history of what got generated.

| Signal | Visual |
|---|---|
| burn rate | typing speed + token emission rate ("you can see claude talking") |
| session % | wall depth — the sky closes in as you spend |
| 90%+ | thin glowing slit, pills shift hot-red, "context pressure" |
| 100% | **seal**: last gap whooshes shut, white seam flash, archive freezes cold-blue, lone caret blinks; emitted tokens bounce off as debris |
| reset | **compaction**: the wall shatters bottom-up and avalanches away past the prompt, then a fresh sentence types |

Extra life: landing tokens occasionally fire a faint blue *attention
glimmer* back to a random archived token; motion trails under flying pills;
ghost mortar between landed tokens spells "attention is all you need".

Engineering notes: mixed-mode renderer — half-block pixel layer (background,
trails, glimmers, debris) with a text-cell overlay (pills are real characters
with truecolor bg, so tokens are *readable*). Wall = per-column depth array +
tile dict; one reconcile engine grows it toward target (ghost accretion) and
sheds excess as falling debris — which means scrubbing usage down, the 100%
seal, and the reset avalanche are all the same mechanism.

`python3 demo_tokenfall.py` — auto-plays the story. Keys: `←/→` scrub, `↑/↓`
burn rate, `a` auto, `f` jump to full, `c` compact now, `q` quit.

## Concept #3: FIZZ (2026-06-12, user's idea)

A glass of water. The water level is your **remaining** session quota —
the only scene where the visual depletes instead of grows, and the glass
makes that legible at a glance. Bubbles rise and pop at the surface,
escaping past the rim as tiny sparkles: tokens flying away. Bubble rate =
live burn rate + busy state — idle claude is still water, working claude
is full carbonation.

| Signal | Visual |
|---|---|
| remaining session % | water level in the glass |
| burn rate / busy | bubble spawn rate (still → fizzing), surface chop |
| <12% left | "almost empty — sip wisely" |
| 100% used | **TAPPED OUT**: the glass drains dry with big glugs |
| waiting for reset | a pour stream runs and the level = real countdown progress — the glass hits full exactly when the window resets |
| mid-session rollover | tap opens, level pours up to the new quota ("topping up") |

Engineering: pure pixel scene (no text overlay) — glass walls/rim/base,
depth-shaded water with moving caustic shimmer, traveling surface waves
plus decaying pop ripples, bubbles with wobble + wall nucleation, spray
and escape-sparkle particles. Refill progress = `1 - secs_left/18000`
(fixed 5h window), so no window-start bookkeeping is needed.

---

## Concept #4: INVADERS (2026-06-12, user's idea)

Your quota is a fleet. Every token you burn, the turret shoots one down —
so "winning" the game means hitting the rate limit: **GAME OVER**. The
inversion is the joke, and it's the same joke as the app's name.

| Signal | Visual |
|---|---|
| remaining session % | invaders alive (6×5 formation, ~3.3% each) |
| usage climbs (poll) | turret hunts the bottom-most invader of a column, fires, pixel explosion |
| burn rate / busy | warning shots that always miss + firing tempo |
| fleet thins | the march speeds up (classic panic = fewer tokens left) |
| 5h window elapsing | the whole formation slowly sinks toward the turret |
| fresh data lands | a saucer crosses the top, light blinking |
| 100% used | chain-explosion finale → flashing GAME OVER |
| waiting for reset | reinforcements beam in (teleport shimmer) on the real countdown — full fleet exactly at reset |

**HD renderer (same day):** this scene runs at double resolution — an
88-wide virtual pixel grid composed with quadrant glyphs (2×2 sub-pixels
per cell; a per-cell quantizer splits the four sub-pixels into bright/dark
sets and picks among all 16 quadrant chars). That bought 11×8 sprites:
three original alien designs with eye-holes, antennae and two-frame
animated tentacles, a 14×6 turret, a 14×5 saucer with animated running
lights — and the shots became tiny 3×5 pixel **rockets**: bright nose
cone, shaded body, fins, flickering orange exhaust with a fading trail.
Formation adapts 2–5 rows to pane height (typically 18 big invaders,
~5.6% each). Mono-gray palette with explosions/exhaust as the one warm
accent, rare badly-aimed return fire, starfield.
A big kill backlog makes the turret frantic (fast slew, loose aim), so a
real burnout plays out as a ~15s staged collapse while `b`'s demo compresses
the whole arc into 12s.

---

## Concept #5: CUBE (2026-06-12, user's dare: "too crazy for AI?")

A real 3D Rubik's cube on the 88px quadrant grid: 54 stickers as
perspective-projected, flat-lit, painter-sorted quads (plastic underquad +
inset sticker = free grid lines), the whole cube tumbling on a precessing
axis while individual layers rotate through smoothstep-eased 90° turns.

| Signal | Visual |
|---|---|
| session % | scramble depth — one layer turn per ~1.5% (66 = chaos) |
| burn rate / busy | tumble speed, turn cadence |
| 100% | the colors drain to gray — SCRAMBLED |
| waiting for reset | it solves itself, paced by the countdown, pristine exactly at reset |
| rollover | speed-solve volley |

The solver is the scramble history replayed backwards — mathematically
exact, zero solver code, and the sticker permutation algebra (`_rot90` on
position + normal per layer) is verified by 4×quarter-turn = identity.

---

Resolved: **fire, tokenfall, invaders and cube live in the monitor as scenes**
(`s` cycles them; `claude-monitor --scene fire|tokens|invaders`, also with
`side`). FIZZ is parked by user verdict ("not sure it gives me what i
want") — out of the rotation, code kept, reachable via the hidden
`--scene water`. Live signals: typing speed = burn rate,
with an instant boost while the attached claude session is busy; seal /
CONTEXT FULL / compaction are driven by the real 5-hour window exactly like
the fire's burnout/reignite. `b` demos a full seal→freeze→avalanche cycle.

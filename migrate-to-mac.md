# Migrating development to a Mac (or any second machine)

How to continue Burnout development — and, optionally, the exact Claude Code
conversation — on another computer.

**There is no cloud sync.** Claude Code sessions and per-project memory are
machine-local files you copy by hand. The *code* just lives on GitHub, so most
of the work is already done. Three levels, in order of value:

1. **The code** — `git clone` (required).
2. **Project memory** — copy the `memory/` dir so future sessions auto-load the
   project's accumulated context (recommended, tiny, robust).
3. **This exact conversation** — copy the session `.jsonl` and `--resume` it
   (optional, with caveats).

---

## 1. The code (required)

```bash
git clone https://github.com/xyanwert/usage-monitor.git
cd usage-monitor
```

That's the whole project — a single Python file, pure stdlib. You'll log into
`claude` normally on the Mac; auth lives in the macOS Keychain and should **not**
be copied from the other machine.

To just *run* it without a git checkout: `brew install xyanwert/tap/claude-monitor`
(see `README.md`).

---

## 2. Project memory (recommended)

The highest-value, lowest-risk transfer. The per-project `memory/` dir
(`MEMORY.md` + notes) **auto-loads into every future session**, so a *fresh*
`claude` on the Mac already knows the project. It's a few KB.

The catch: Claude Code names each project folder after the **absolute path of
your clone**, munged with dashes (e.g. `/Users/you/code/usage-monitor` →
`-Users-you-code-usage-monitor`). Don't hand-build the name — let Claude create
it, then drop files in:

```bash
# On the Mac, from inside the clone:
cd ~/path/to/usage-monitor
claude            # start once so it creates the project dir, then /exit

# Find the folder it just made:
ls -d ~/.claude/projects/*usage-monitor*

# Copy the memory dir from the source machine into THAT folder:
scp -r you@source-host:'~/.claude/projects/-media-xyan-NewMedia-code-usage-monitor/memory' \
      ~/.claude/projects/<the-folder-from-above>/
```

(`scp` is one option — AirDrop, a USB stick, or a private gist work too; it's
just a directory of small `.md` files.)

New sessions on the Mac now carry all the accumulated context.

---

## 3. Resume this exact conversation (optional)

If you want to literally `--resume` the same thread instead of starting fresh:

```bash
# This session's transcript on the source machine:
#   ~/.claude/projects/-media-xyan-NewMedia-code-usage-monitor/4f3223ac-17a0-4ec9-a95b-06a3b9bd5035.jsonl
# (Not sure which one? It's the most-recently-modified .jsonl in that folder,
#  or run /status inside the live session to see the id.)

scp you@source-host:'~/.claude/projects/-media-xyan-NewMedia-code-usage-monitor/4f3223ac-17a0-4ec9-a95b-06a3b9bd5035.jsonl' \
    ~/.claude/projects/<mac-project-folder>/

cd ~/path/to/usage-monitor
claude --resume 4f3223ac-17a0-4ec9-a95b-06a3b9bd5035
```

**Two caveats:**

- The `.jsonl` must land in the folder that matches the **Mac clone's path**
  (the one from step 2), *not* the source machine's folder name — `--resume` is
  scoped to the current directory. A mismatch is why `--resume` would report
  "no conversation found."
- The transcript has the **source machine's absolute paths baked in**
  (`/media/xyan/NewMedia/...`). Harmless for reading history as context, but any
  old absolute path won't exist on the Mac — file paths are relative to the new
  clone now.

---

## Recommendation

Do **1 + 2** and start a fresh session on the Mac — clean, robust, and the
memory carries the real knowledge forward. Only bother with **3** if you
specifically want to scroll back through the exact dialogue.

> Note: the per-folder naming (folder = munged absolute cwd) is verified
> empirically, not from official docs — which is exactly why step 2 lets Claude
> create the folder rather than constructing the name by hand.

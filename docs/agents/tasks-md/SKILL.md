---
name: tasks-md
description: "Read and update the user's TASKS.md checklist (the talos-bar Tasks tab) safely: add tasks, claim, annotate, mark done."
version: 0.1.0
author: talos-bar
license: BSD-3-Clause
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Tasks, TODO, Productivity, talos-bar]
    related_skills: [weekly-review-planning]
---

# TASKS.md (talos-bar Tasks tab)

The user's to-do list is `~/dev/tasks/TASKS.md`, a git clone shown and edited in
the talos-bar **Tasks** tab. It is free-form Markdown: headings per project,
`- [ ]` checkboxes, plain bullets, nested notes, links to state docs. The human
may be typing in it right now.

**Use `talos-tasks` for every read and write. Never edit TASKS.md with file
tools, and never `git push` it** (a timer syncs it four times a day).

## When to use

- "What's on my list?", "what's open under nixos?"
- "Add X to my tasks", "put this under talos-hq"
- You start, progress or finish work that matches a task: claim it, note
  progress (link the state doc / session id), mark it done with evidence.

## Commands

All output is JSON. Writes need `--agent <you>` (or `TASKS_AGENT`); pass your
session with `--session <id>` (or `TASKS_SESSION`) so the user can find the
conversation.

```sh
talos-tasks show [--section nixos] [--open] [--claimed]   # {rev, items:[{id, section, text, done, claimed_by, notes, ...}]}
talos-tasks get <id> | --match "github url" [--in nixos]
talos-tasks add "text" [--section "Current work/nixos"] [--under <id>]
talos-tasks claim <id> [--note "plan"]
talos-tasks note <id> "progress, with links"
talos-tasks done <id> --note "evidence: PR #12 merged"
talos-tasks reopen <id>
talos-tasks release <id>
```

If `talos-tasks` is not on PATH yet, use `python -m talos_bar.tasks_md` from the
talos-bar checkout (`uv run python -m talos_bar.tasks_md ...`).

- **ids** are 8 hex digits (with `~2` for duplicates). They survive ticks, notes
  and edits elsewhere; they change only when the human rewords the task. Always
  `show`/`get` first in a session and use the ids it returns.
- `--match TEXT` works instead of an id when the text is unique.
- `--if-rev <rev from show>` makes the write fail unless the file is unchanged.

Exit codes: `0` ok · `1` io/git failure · `2` usage (e.g. no `--agent`) ·
`3` unknown or ambiguous item (candidates listed: pick one, or ask) ·
`4` stale `--if-rev` (run `show` again, re-decide, retry) · `5` refused.

## Rules

- You may: add tasks, add notes, claim/release, done/reopen checkboxes.
- Leave to the human: rewording, reordering, deleting, headings, prose,
  turning a bullet into a checkbox. The CLI refuses these (`done` on a plain
  bullet exits 5); tell the user what you suggest instead.
- Mark `done` only for work you actually finished or were told to close, and
  say how you know (`--note`).
- Claim before long work and release if you stop. Don't take an item someone
  else has claimed (exit 5): ask.
- New tasks without an obvious section go to `# Agent inbox`; the user files
  them.
- Keep notes short, one line; put details in a state doc and link it.

## What it looks like in the file

```markdown
- [ ] branded email server for the domain
    * **hermes** 2026-11-17: claimed (session `20261117_101500_ab12cd`)
    * **hermes** 2026-11-17: SPF/DKIM draft in [STATE.md](file:///home/me/dev/mail/STATE.md)
```

The bar shows a `⚙N` badge for open claimed items and marks them in the tab's
tooltip. If the human has the editor open, their next save merges your lines
in; only an edit to the very same line stops their save for review.

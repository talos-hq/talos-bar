# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py>=3"]
# ///
"""Backend for the Tasks panel widget: a Markdown checklist in a git repo.

The list is TASKS.md in a clone of github.com/zackees/tasks, one task per
``- [ ] text`` / ``- [x] text`` line. Edits are only committed locally; the
network is touched solely by ``sync``, which the tasks-sync timer runs four
times a day. A failed sync loses nothing: the commits wait for the next.

    tasks_cli.py list            JSON for the widget
    tasks_cli.py add TEXT
    tasks_cli.py toggle INDEX
    tasks_cli.py remove INDEX
    tasks_cli.py sync            pull (rebase) and push; the timer's job
"""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt

REPO = Path(os.environ.get("TASKS_REPO", Path.home() / "dev" / "tasks"))
FILE = REPO / "TASKS.md"
REMOTE = "https://github.com/zackees/tasks.git"
TASK = re.compile(r"^\s*[-*] \[( |x|X)\] (.*)$")


def git(*args: str, timeout: float = 20) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def ensure_repo() -> None:
    if (REPO / ".git").exists():
        return
    REPO.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", REMOTE, str(REPO)], check=False, timeout=60)


def ensure_union() -> None:
    # Edits from two places both land: git's built-in union driver keeps
    # the lines of both sides instead of stopping on a conflict. The cost
    # is a line changed on both sides appearing twice, which is visible
    # and easy to tidy; a conflict would stall every later sync.
    attrs = REPO / ".gitattributes"
    if attrs.exists() and "merge=union" in attrs.read_text():
        return
    attrs.write_text("TASKS.md merge=union\n")
    git("add", ".gitattributes")
    git("commit", "-q", "-m", "Merge TASKS.md by union so syncs never conflict")


def pull() -> bool:
    ensure_union()
    ok = git("pull", "-q", "--rebase", "--autostash", timeout=60)
    if not ok:
        git("rebase", "--abort")
    return ok


def push() -> bool:
    return git("push", "-q", "origin", "HEAD")


def read() -> tuple[list[str], list[tuple[int, bool, str]]]:
    lines = FILE.read_text().splitlines() if FILE.exists() else ["# Tasks", ""]
    tasks = []
    for i, line in enumerate(lines):
        m = TASK.match(line)
        if m:
            tasks.append((i, m.group(1) != " ", m.group(2)))
    return lines, tasks


def top_level() -> list[dict[str, object]]:
    """The file's outermost list items, for the widget's hover.

    markdown-it is CommonMark: there is no such thing as invalid input, so a
    half-typed or malformed file still yields whatever items it can. Only
    items of a list at the document root count; nested sub-items and lists
    inside quotes are skipped.
    """
    if not FILE.exists():
        return []
    tokens = MarkdownIt("commonmark").parse(FILE.read_text())
    items: list[dict[str, object]] = []
    depth = 0  # nesting of list items around the current token
    want = False  # the next inline token is a top-level item's first line
    for tok in tokens:
        if tok.type == "list_item_open":
            depth += 1
            want = depth == 1 and tok.level == 1
        elif tok.type == "list_item_close":
            depth -= 1
        elif tok.type == "inline" and want:
            want = False
            text = tok.content.split("\n")[0].strip()
            m = re.match(r"^\[( |x|X)\]\s*(.*)$", text)
            items.append(
                {"text": m.group(2) if m else text, "check": None if not m else m.group(1) != " "}
            )
    return items


def write(lines: list[str], message: str) -> None:
    FILE.write_text("\n".join(lines) + "\n")
    git("add", "TASKS.md")
    git("commit", "-q", "-m", message)


def unpushed() -> int:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), "rev-list", "--count", "@{u}..HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return int(r.stdout.strip() or 0)
    except (ValueError, subprocess.TimeoutExpired):
        return 0


def main(argv: list[str]) -> int:
    ensure_repo()
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "sync":
        ok = pull()
        return 0 if push() and ok else 1
    if cmd == "list":
        _, tasks = read()
        print(
            json.dumps(
                {
                    "tasks": [{"index": n, "done": d, "text": t} for n, (_, d, t) in enumerate(tasks)],
                    "open": sum(1 for _, d, _ in tasks if not d),
                    "synced": unpushed() == 0,
                    "top": top_level(),
                }
            )
        )
        return 0
    if cmd == "read":
        print(FILE.read_text() if FILE.exists() else "")
        return 0
    if cmd == "save64":
        # The widget's editor: the whole file, base64-encoded.
        text = base64.b64decode(argv[2] if len(argv) > 2 else "").decode()
        text = text.rstrip("\n") + "\n"
        if FILE.exists() and FILE.read_text() == text:
            return 0
        FILE.write_text(text)
        git("add", "TASKS.md")
        git("commit", "-q", "-m", "Edit tasks")
        return 0
    lines, tasks = read()
    if cmd in ("add", "add64"):
        # add64: the widget passes text base64-encoded, so no shell quoting.
        raw = " ".join(argv[2:])
        if cmd == "add64":
            raw = base64.b64decode(raw).decode()
        text = raw.strip().replace("\n", " ")
        if not text:
            return 1
        # New tasks go after the last task line, or at the end.
        at = tasks[-1][0] + 1 if tasks else len(lines)
        lines.insert(at, f"- [ ] {text}")
        write(lines, f"Add: {text}")
        return 0
    if cmd in ("toggle", "remove"):
        n = int(argv[2])
        if not 0 <= n < len(tasks):
            return 1
        i, done, text = tasks[n]
        if cmd == "toggle":
            lines[i] = f"- [{' ' if done else 'x'}] {text}"
            write(lines, f"{'Reopen' if done else 'Done'}: {text}")
        else:
            del lines[i]
            write(lines, f"Remove: {text}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

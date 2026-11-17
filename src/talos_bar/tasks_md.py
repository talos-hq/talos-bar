"""The agent interface to TASKS.md: `talos-tasks`.

Agents (Hermes, Claude, clud) read and change the Tasks tab's checklist through
this command instead of editing the file. It keeps the human's text intact:

- Items are addressed by a content id, sha256(section path + text) cut to 8
  hex digits. The id survives ticks, claims, notes and edits elsewhere in the
  file, and changes only when a human rewords the item.
- An agent never rewrites a line. It flips one checkbox character or adds and
  removes its own note lines, which look like
  ``* **hermes** 2026-11-17: claimed (session `abc`)``. Separate lines keep
  the git ``merge=union`` sync from duplicating the human's line.
- Every write runs under the tasks lock, re-reads the file, replaces it
  atomically and commits it locally with ``Agent:`` and ``Session:`` trailers.
  ``--if-rev`` turns it into a compare-and-swap. The tasks-sync timer pushes.

Exit codes: 0 ok, 1 io or git failure, 2 usage, 3 unknown or ambiguous item,
4 stale --if-rev, 5 refused (not allowed for agents).
See docs/agents/tasks-md/SKILL.md.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

INBOX = "Agent inbox"
CLAIM = "claimed"

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<bullet>[-*+]|\d{1,9}[.)])[ \t]+)?"
    r"(?:\[(?P<check>[ xX])\][ \t]+)?(?P<text>\S.*?)\s*$"
)
_NOTE = re.compile(r"^\*\*(?P<agent>[\w.-]+)\*\*\s+(?P<date>\d{4}-\d{2}-\d{2}):\s*(?P<body>.*)$")
_AGENT = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class NoteJSON(TypedDict):
    agent: str
    date: str
    text: str
    line: int


class ItemJSON(TypedDict):
    id: str
    section: list[str]
    depth: int
    kind: str  # "checkbox" | "bullet"
    done: bool | None
    text: str
    line: int
    claimed_by: list[str]
    notes: list[NoteJSON]


@dataclass
class Note:
    agent: str
    date: str
    body: str
    index: int  # 0-based line index


@dataclass
class Item:
    id: str
    section: list[str]
    depth: int
    check: str | None  # " ", "x", "X", or None for a plain bullet
    text: str
    index: int  # 0-based line index of the item line
    indent: int  # visual width of the leading whitespace
    content_col: int  # where the text starts, so children nest under it
    end: int  # index of the last line of the item's subtree
    bullet: str = "-"  # "-", "*", "+", "1." ...; "" for a bulletless "[ ]"
    notes: list[Note] = field(default_factory=list[Note])

    @property
    def claimed_by(self) -> list[str]:
        return sorted({n.agent for n in self.notes if n.body.startswith(CLAIM)})

    def json(self) -> ItemJSON:
        return {
            "id": self.id,
            "section": self.section,
            "depth": self.depth,
            "kind": "bullet" if self.check is None else "checkbox",
            "done": None if self.check is None else self.check != " ",
            "text": self.text,
            "line": self.index + 1,
            "claimed_by": self.claimed_by,
            "notes": [
                {"agent": n.agent, "date": n.date, "text": n.body, "line": n.index + 1} for n in self.notes
            ],
        }


@dataclass
class Doc:
    lines: list[str]  # with line endings, so untouched bytes survive exactly
    items: list[Item]
    headings: list[tuple[list[str], int]]  # (section path, line index)

    @property
    def text(self) -> str:
        return "".join(self.lines)


class TasksError(Exception):
    def __init__(self, code: int, error: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.error = error
        self.extra = extra


def _width(ws: str) -> int:
    return len(ws.expandtabs(4))


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def rev(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def parse(text: str) -> Doc:
    lines = text.splitlines(keepends=True)
    items: list[Item] = []
    headings: list[tuple[list[str], int]] = []
    section: list[tuple[int, str]] = []  # (level, title)
    stack: list[Item] = []  # open ancestors, by indent
    seen: dict[str, int] = {}
    fenced = False
    for i, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or not line.strip():
            continue
        h = _HEADING.match(line)
        if h and not line[0].isspace():
            level = len(h.group(1))
            section = [s for s in section if s[0] < level] + [(level, h.group(2))]
            headings.append(([t for _, t in section], i))
            stack.clear()
            continue
        m = _ITEM.match(line)
        if m is None or (m.group("bullet") is None and m.group("check") is None):
            continue  # prose; it still belongs to whatever item it sits under
        indent = _width(m.group("indent"))
        while stack and stack[-1].indent >= indent:
            stack.pop()
        body = m.group("text")
        note = _NOTE.match(body) if m.group("check") is None else None
        if note and stack:
            stack[-1].notes.append(Note(note.group("agent"), note.group("date"), note.group("body"), i))
            continue
        path = [t for _, t in section]
        key = "/".join(path) + "\x1f" + _norm(body)
        digest = hashlib.sha256(key.encode()).hexdigest()[:8]
        seen[digest] = seen.get(digest, 0) + 1
        ident = digest if seen[digest] == 1 else f"{digest}~{seen[digest]}"
        it = Item(
            id=ident,
            section=path,
            depth=len(stack),
            check=m.group("check"),
            text=body,
            index=i,
            indent=indent,
            content_col=_content_col(line, m),
            end=i,
            bullet=m.group("bullet") or "",
        )
        items.append(it)
        stack.append(it)
    for it in items:
        it.end = _subtree_end(lines, it)
    return Doc(lines, items, headings)


def _content_col(line: str, m: re.Match[str]) -> int:
    """Where a child line must start to nest under this item (CommonMark:
    just after the bullet marker; the "[ ]" is part of the content)."""
    if m.group("bullet") is None:
        return _width(m.group("indent")) + 2
    at = m.start("check") - 1 if m.group("check") is not None else m.start("text")
    return _width(line[:at])


def _subtree_end(lines: list[str], it: Item) -> int:
    end = it.index
    for j in range(it.index + 1, len(lines)):
        line = lines[j].rstrip("\r\n")
        if not line.strip():
            continue
        if _HEADING.match(line) and not line[0].isspace():
            break
        if _width(line[: len(line) - len(line.lstrip())]) <= it.indent:
            break
        end = j
    return end


def _eol(doc: Doc) -> str:
    return "\r\n" if doc.lines and doc.lines[0].endswith("\r\n") else "\n"


def resolve(doc: Doc, ref: str | None, match: str | None, section: str | None = None) -> Item:
    if ref:
        found = [it for it in doc.items if it.id == ref]
    elif match:
        needle = _norm(match)
        found = [it for it in doc.items if needle in _norm(it.text)]
        if section:
            found = [it for it in found if _in_section(it.section, section)]
    else:
        raise TasksError(2, "usage", "give an item id or --match TEXT")
    if len(found) == 1:
        return found[0]
    candidates = [it.json() for it in found[:10]]
    if not found:
        raise TasksError(3, "not_found", "no such item; run `talos-tasks show` for current ids")
    raise TasksError(3, "ambiguous", f"{len(found)} items match; pass one id", candidates=candidates)


def _in_section(path: list[str], want: str) -> bool:
    want_parts = [_norm(p) for p in want.split("/") if p.strip()]
    have = [_norm(p) for p in path]
    return bool(want_parts) and have[-len(want_parts) :] == want_parts


# ---------------------------------------------------------------- edits
# Each takes the parsed doc and returns new lines; nothing else is touched.


def _child_col(doc: Doc, it: Item) -> int:
    kids = [k for k in doc.items if it.index < k.index <= it.end and k.depth == it.depth + 1]
    return kids[0].indent if kids else it.content_col


def _note_line(col: int, agent: str, body: str, eol: str, today: str) -> str:
    return f"{' ' * col}* **{agent}** {today}: {body}{eol}"


def add_note(doc: Doc, it: Item, agent: str, body: str, today: str) -> list[str]:
    body = " ".join(body.split())
    if not body:
        raise TasksError(2, "usage", "the note is empty")
    lines = list(doc.lines)
    if not lines[it.end].endswith("\n"):
        lines[it.end] += _eol(doc)
    lines.insert(it.end + 1, _note_line(_child_col(doc, it), agent, body, _eol(doc), today))
    return lines


def set_check(doc: Doc, it: Item, done: bool) -> list[str]:
    if it.check is None:
        raise TasksError(5, "refused", "that item is a plain bullet, not a checkbox; ask the human")
    lines = list(doc.lines)
    line = lines[it.index]
    m = _ITEM.match(line.rstrip("\r\n"))
    assert m is not None and m.start("check") >= 0
    pos = m.start("check")
    lines[it.index] = line[:pos] + ("x" if done else " ") + line[pos + 1 :]
    return lines


def remove_claims(doc: Doc, it: Item, agent: str) -> list[str]:
    drop = {n.index for n in it.notes if n.agent == agent and n.body.startswith(CLAIM)}
    return [line for i, line in enumerate(doc.lines) if i not in drop]


def _new_line(col: int, like: Item | None, text: str, eol: str) -> str:
    bullet = like.bullet if like is not None and like.bullet in ("-", "*", "+") else "-"
    return f"{' ' * col}{bullet} [ ] {text}{eol}"


def add_item(doc: Doc, text: str, section: str | None, under: Item | None) -> tuple[list[str], int]:
    """Insert ``- [ ] text`` and return the new lines and the inserted index."""
    text = " ".join(text.split())
    if not text:
        raise TasksError(2, "usage", "the task text is empty")
    eol = _eol(doc)
    lines = list(doc.lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += eol
    if under is not None:
        kids = [it for it in doc.items if under.index < it.index <= under.end and it.depth == under.depth + 1]
        col = _child_col(doc, under)
        at = under.end + 1
        lines.insert(at, _new_line(col, kids[0] if kids else None, text, eol))
        return lines, at
    path = _find_section(doc, section) if section else None
    if path is None:
        path = _find_section(doc, INBOX)
    if path is None:
        if lines and lines[-1].strip():
            lines.append(eol)
        lines += [f"# {INBOX}{eol}", eol, f"- [ ] {text}{eol}"]
        return lines, len(lines) - 1
    heading = next(i for p, i in doc.headings if p == path)
    top = [it for it in doc.items if it.section == path and it.depth == 0]
    if top:
        last = top[-1]
        at = last.end + 1
        lines.insert(at, _new_line(last.indent, last, text, eol))
        return lines, at
    at = heading + 1
    while at < len(lines) and not lines[at].strip():
        at += 1
    if at == heading + 1:
        lines.insert(at, eol)
        at += 1
    lines.insert(at, f"- [ ] {text}{eol}")
    return lines, at


def _find_section(doc: Doc, want: str | None) -> list[str] | None:
    if not want:
        return None
    for path, _ in doc.headings:
        if _in_section(path, want):
            return path
    return None


# ---------------------------------------------------------------- merging


def merge(ours: str, base: str, theirs: str) -> str | None:
    """Apply base -> theirs to ours, line by line; None on a real conflict.

    Unlike diff3, a line inserted next to a line the other side edited is not
    a conflict: insertions are anchored to an unchanged neighbouring line.
    That is the common case here (an agent's note under one item while the
    human edits the next). Changed or deleted lines conflict only when ours
    changed them too.
    """
    b, o, t = (x.splitlines(keepends=True) for x in (base, ours, theirs))
    # Match on content only: the editor may drop the final newline.
    kb, ko, kt = ([line.rstrip("\r\n") for line in x] for x in (b, o, t))
    where: dict[int, int] = {}  # base line -> its index in ours, where unchanged
    for blk in difflib.SequenceMatcher(None, kb, ko, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            where[blk.a + k] = blk.b + k
    edits: list[tuple[int, int, list[str]]] = []  # (start, stop) in ours -> lines
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, kb, kt, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        new = t[j1:j2]
        if tag == "insert":
            if i1 == 0:
                at = 0
            elif i1 - 1 in where:
                at = where[i1 - 1] + 1
            elif i1 in where:
                at = where[i1]
            else:
                return None
            edits.append((at, at, new))
            continue
        span = [where.get(i) for i in range(i1, i2)]
        if any(x is None for x in span):
            if ko[slice(*_span(where, i1, i2, len(o)))] == kt[j1:j2]:
                continue  # both sides made the same change
            return None
        first, last = cast("int", span[0]), cast("int", span[-1])
        if last - first != i2 - i1 - 1:
            return None  # ours inserted lines inside the region theirs changed
        edits.append((first, last + 1, new))
    for start, stop, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if start > 0 and start <= len(o) and not o[start - 1].endswith("\n"):
            o[start - 1] += "\n"
        o[start:stop] = new
    return "".join(o)


def _span(where: dict[int, int], i1: int, i2: int, size: int) -> tuple[int, int]:
    """Ours' lines between the unchanged neighbours of base[i1:i2]."""
    lo = where[i1 - 1] + 1 if i1 - 1 in where else (0 if i1 == 0 else size)
    hi = where.get(i2, size)
    return lo, max(lo, hi)


# ---------------------------------------------------------------- the file


def repo() -> Path:
    return Path(os.environ.get("TASKS_REPO", Path.home() / "dev" / "tasks"))


@contextlib.contextmanager
def locked(root: Path) -> Generator[None]:
    """The lock tasks_cli's save64 also takes, so the bar and agents serialise."""
    git_dir = root / ".git"
    path = (git_dir if git_dir.is_dir() else root) / "tasks.lock"
    with path.open("a") as fh:
        if sys.platform == "win32":  # pragma: no cover - POSIX hosts run the tests
            yield
            return
        import fcntl

        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write(file: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".TASKS.", dir=file.parent)
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        if file.exists():
            os.chmod(tmp, file.stat().st_mode & 0o7777)
        os.replace(tmp, file)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _git(root: Path, *args: str) -> None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TasksError(1, "git", f"git {args[0]} failed: {exc}") from exc
    if r.returncode != 0:
        raise TasksError(1, "git", f"git {args[0]} failed: {r.stderr.strip() or r.stdout.strip()}")


def _commit(root: Path, subject: str, agent: str, session: str) -> None:
    message = f"tasks({agent}): {subject}"
    if len(message) > 100:
        message = message[:99] + "…"
    trailers = f"Agent: {agent}" + (f"\nSession: {session}" if session else "")
    _git(root, "add", "TASKS.md")
    _git(root, "commit", "-q", "--only", "-m", message, "-m", trailers, "--", "TASKS.md")


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talos-tasks", description="Agent interface to TASKS.md (JSON out).")
    sub = p.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="list items with ids")
    show.add_argument("--section")
    show.add_argument("--open", action="store_true", help="only unticked checkboxes")
    show.add_argument("--claimed", action="store_true", help="only items an agent has claimed")
    get = sub.add_parser("get", help="one item with its notes")
    _target(get)
    for name, needs_text in (
        ("add", True),
        ("note", True),
        ("claim", False),
        ("release", False),
        ("done", False),
        ("reopen", False),
    ):
        sp = sub.add_parser(name)
        if name == "add":
            sp.add_argument("text")
            sp.add_argument("--section", help="heading name or A/B path; default: # Agent inbox")
            sp.add_argument("--under", help="id of the parent item")
        else:
            _target(sp)
            if needs_text:
                sp.add_argument("text")
            if name in ("done", "claim"):
                sp.add_argument("--note", help="evidence or context, added as a note")
        sp.add_argument("--agent", default=os.environ.get("TASKS_AGENT", ""))
        sp.add_argument("--session", default=os.environ.get("TASKS_SESSION", ""))
        sp.add_argument("--if-rev", help="fail with exit 4 unless the file is still at this rev")
    return p


def _target(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("id", nargs="?")
    sp.add_argument("--match", help="unique substring of the item text, instead of an id")
    sp.add_argument("--in", dest="within", help="limit --match to this section")


def run(argv: Sequence[str], today: str | None = None) -> dict[str, object]:
    opts = cast("dict[str, object]", vars(_parser().parse_args(list(argv))))

    def opt(name: str) -> str | None:
        v = opts.get(name)
        return v if isinstance(v, str) and v else None

    cmd = opt("cmd") or ""
    root = repo()
    file = root / "TASKS.md"
    if not file.exists():
        raise TasksError(1, "missing", f"{file} does not exist")
    day = today or dt.date.today().isoformat()
    if cmd in ("show", "get"):
        doc = parse(file.read_text())
        if cmd == "get":
            item = resolve(doc, opt("id"), opt("match"), opt("within"))
            return {"ok": True, "rev": rev(doc.text), "item": item.json()}
        items = doc.items
        section = opt("section")
        if section:
            items = [it for it in items if _in_section(it.section, section)]
        if opts.get("open"):
            items = [it for it in items if it.check == " "]
        if opts.get("claimed"):
            items = [it for it in items if it.claimed_by]
        return {"ok": True, "rev": rev(doc.text), "items": [it.json() for it in items]}

    agent = opt("agent") or ""
    session = " ".join((opt("session") or "").split())
    if not _AGENT.match(agent):
        raise TasksError(2, "usage", "agent writes need --agent NAME (or TASKS_AGENT), letters/digits/._-")
    with locked(root):
        before = file.read_text()
        doc = parse(before)
        want = opt("if_rev")
        if want and not rev(before).startswith(want.lower()):
            raise TasksError(4, "stale_rev", "TASKS.md changed; run show again", rev=rev(before))
        claim_tail = f" (session `{session}`)" if session else ""
        target: Item | None = None
        if cmd == "add":
            under = resolve(doc, opt("under"), None) if opt("under") else None
            text = opt("text") or ""
            lines, at = add_item(doc, text, opt("section"), under)
            subject = "add: " + " ".join(text.split())
        else:
            target = resolve(doc, opt("id"), opt("match"), opt("within"))
            at = target.index
            if cmd == "note":
                lines = add_note(doc, target, agent, opt("text") or "", day)
                subject = f"note on: {target.text}"
            elif cmd == "claim":
                others = [a for a in target.claimed_by if a != agent]
                if others:
                    raise TasksError(5, "refused", f"already claimed by {', '.join(others)}")
                if agent in target.claimed_by:
                    return {"ok": True, "rev": rev(before), "changed": False, "item": target.json()}
                note = opt("note")
                body = CLAIM + claim_tail + (": " + note if note else "")
                lines = add_note(doc, target, agent, body, day)
                subject = f"claim: {target.text}"
            elif cmd == "release":
                lines = remove_claims(doc, target, agent)
                subject = f"release: {target.text}"
            else:
                done = cmd == "done"
                lines = set_check(doc, target, done)
                note = opt("note")
                if done and note:
                    lines = add_note(Doc(lines, doc.items, doc.headings), target, agent, note, day)
                subject = f"{cmd}: {target.text}"
        after = "".join(lines)
        if after == before:
            item = target.json() if target else None
            return {"ok": True, "rev": rev(before), "changed": False, "item": item}
        _write(file, after)
        _commit(root, subject, agent, session)
    new = parse(after)
    # The edited item, found again by line (its id may carry a new ~N suffix).
    found = next((it for it in new.items if it.index == at), None)
    return {"ok": True, "rev": rev(after), "changed": True, "item": found.json() if found else None}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        out = run(args)
    except TasksError as exc:
        print(json.dumps({"ok": False, "error": exc.error, "message": str(exc), **exc.extra}))
        return exc.code
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "io", "message": str(exc)}))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


def claimed_open(text: str) -> list[Item]:
    """Open checkboxes and bullets an agent has claimed, for the bar's badge."""
    return [it for it in parse(text).items if it.claimed_by and it.check != "x" and it.check != "X"]


if __name__ == "__main__":
    sys.exit(main())

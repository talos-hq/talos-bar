"""`talos-tasks`, the agent interface to TASKS.md, on a real git repo."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from talos_bar import tasks_md

SAMPLE = """# Top Items

Free prose that agents never touch.

# talos

  * a plain bullet
  - [ ] ship the bar
    * sub note by a human

## nixos
  * kitty term
    [ ] clickable github url
  - [x] already done

```
- [ ] not a task, inside a fence
```
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "TASKS.md").write_text(SAMPLE)
    _git(tmp_path, "add", "TASKS.md")
    _git(tmp_path, "commit", "-qm", "base")
    monkeypatch.setenv("TASKS_REPO", str(tmp_path))
    monkeypatch.delenv("TASKS_AGENT", raising=False)
    monkeypatch.delenv("TASKS_SESSION", raising=False)
    return tmp_path


def cli(*args: str) -> tuple[int, dict[str, object]]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = tasks_md.main(list(args))
    return code, json.loads(out.getvalue())


def ids(text: str) -> dict[str, str]:
    return {it.text: it.id for it in tasks_md.parse(text).items}


def item_id(repo: Path, needle: str) -> str:
    return next(v for k, v in ids((repo / "TASKS.md").read_text()).items() if needle in k)


def test_parse_sections_kinds_and_fences() -> None:
    doc = tasks_md.parse(SAMPLE)
    got = {it.text: (it.section, it.depth, it.check) for it in doc.items}
    assert got["ship the bar"] == (["talos"], 0, " ")
    assert got["sub note by a human"] == (["talos"], 1, None)
    assert got["clickable github url"] == (["talos", "nixos"], 1, " ")
    assert got["already done"] == (["talos", "nixos"], 0, "x")
    assert "not a task, inside a fence" not in got
    assert all("prose" not in t for t in got)


def test_ids_survive_ticks_notes_and_unrelated_edits_but_not_rewording() -> None:
    before = ids(SAMPLE)
    edited = (
        SAMPLE.replace("[ ] ship the bar", "[x] ship the bar")
        .replace("# Top Items\n", "# Top Items\n\n- [ ] new thing above\n")
        .replace("    * sub note by a human\n", "    * sub note by a human\n    * **a** 2026-11-17: hi\n")
    )
    after = ids(edited)
    for text in ("ship the bar", "clickable github url", "a plain bullet"):
        assert after[text] == before[text]
    assert "ship the boat" not in ids(SAMPLE)
    reworded = ids(SAMPLE.replace("ship the bar", "ship the boat"))
    assert reworded["ship the boat"] != before["ship the bar"]


def test_duplicate_items_get_suffixes() -> None:
    doc = tasks_md.parse("# a\n- [ ] same\n- [ ] same\n")
    first, second = (it.id for it in doc.items)
    assert second == f"{first}~2"


def test_claim_note_done_touch_only_their_lines_and_commit_with_trailers(repo: Path) -> None:
    target = item_id(repo, "clickable")
    code, out = cli("claim", target, "--agent", "hermes", "--session", "s1")
    assert code == 0 and out["changed"] is True
    code, out = cli("done", target, "--agent", "hermes", "--note", "kitty config updated")
    assert code == 0
    text = (repo / "TASKS.md").read_text()
    expected = SAMPLE.replace(
        "    [ ] clickable github url\n",
        "    [x] clickable github url\n"
        "      * **hermes** DATE: claimed (session `s1`)\n"
        "      * **hermes** DATE: kitty config updated\n",
    )
    import re

    assert re.sub(r"\d{4}-\d{2}-\d{2}", "DATE", text) == expected
    log = _git(repo, "log", "-2", "--format=%s%n%b")
    assert "tasks(hermes): done: clickable github url" in log
    assert "tasks(hermes): claim: clickable github url" in log
    assert "Agent: hermes" in log and "Session: s1" in log
    assert _git(repo, "status", "--porcelain") == ""
    item = out["item"]
    assert isinstance(item, dict)
    assert item["id"] == target and item["done"] is True and item["claimed_by"] == ["hermes"]


def test_release_removes_only_own_claim_and_others_are_refused(repo: Path) -> None:
    target = item_id(repo, "ship the bar")
    assert cli("claim", target, "--agent", "hermes")[0] == 0
    code, out = cli("claim", target, "--agent", "claude")
    assert code == 5 and out["error"] == "refused"
    assert cli("release", target, "--agent", "claude")[1]["changed"] is False
    assert cli("release", target, "--agent", "hermes")[0] == 0
    assert (repo / "TASKS.md").read_text() == SAMPLE


def test_done_on_plain_bullet_is_refused(repo: Path) -> None:
    code, out = cli("done", item_id(repo, "a plain bullet"), "--agent", "hermes")
    assert code == 5 and out["error"] == "refused"
    assert (repo / "TASKS.md").read_text() == SAMPLE


def test_writes_need_an_agent(repo: Path) -> None:
    code, _ = cli("note", item_id(repo, "ship"), "hello")
    assert code == 2
    assert (repo / "TASKS.md").read_text() == SAMPLE


def test_stale_rev_and_unknown_or_ambiguous_items(repo: Path) -> None:
    _, shown = cli("show")
    rev = str(shown["rev"])
    (repo / "TASKS.md").write_text(SAMPLE + "- [ ] human edit\n")
    code, out = cli("note", item_id(repo, "ship"), "x", "--agent", "hermes", "--if-rev", rev[:12])
    assert code == 4 and out["error"] == "stale_rev" and out["rev"] != rev
    assert cli("note", "deadbeef", "x", "--agent", "hermes")[0] == 3
    code, out = cli("get", "--match", "e")
    assert code == 3 and out["error"] == "ambiguous" and out["candidates"]
    code, out = cli("get", "--match", "github")
    assert code == 0


def test_add_lands_in_section_with_sibling_bullet_or_inbox(repo: Path) -> None:
    assert cli("add", "new nixos thing", "--section", "talos/nixos", "--agent", "hermes")[0] == 0
    assert cli("add", "homeless", "--section", "nope", "--agent", "hermes")[0] == 0
    assert cli("add", "child", "--under", item_id(repo, "ship the bar"), "--agent", "hermes")[0] == 0
    text = (repo / "TASKS.md").read_text()
    assert "  - [x] already done\n  - [ ] new nixos thing\n" in text
    assert "    * sub note by a human\n    * [ ] child\n" in text  # sibling bullet style
    assert text.endswith("\n# Agent inbox\n\n- [ ] homeless\n")
    doc = tasks_md.parse(text)
    assert next(it for it in doc.items if it.text == "child").depth == 1


def test_show_filters(repo: Path) -> None:
    cli("claim", item_id(repo, "ship"), "--agent", "hermes")
    _, out = cli("show", "--open")
    items = out["items"]
    assert isinstance(items, list)
    assert {i["text"] for i in items} == {"ship the bar", "clickable github url"}  # pyright: ignore[reportUnknownVariableType]
    _, out = cli("show", "--claimed")
    assert [i["text"] for i in out["items"]] == ["ship the bar"]  # pyright: ignore[reportUnknownVariableType, reportIndexIssue, reportGeneralTypeIssues]
    assert [it.text for it in tasks_md.claimed_open((repo / "TASKS.md").read_text())] == ["ship the bar"]


def test_git_failure_is_reported(repo: Path) -> None:
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    code, out = cli("note", item_id(repo, "ship"), "x", "--agent", "hermes")
    assert code == 1 and out["error"] == "git"


@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_concurrent_writers_all_land(repo: Path) -> None:
    target = item_id(repo, "ship")
    codes: list[int] = []

    def note(n: int) -> None:
        r = subprocess.run(
            [sys.executable, "-m", "talos_bar.tasks_md", "note", target, f"note {n}", "--agent", "hermes"],
            capture_output=True,
            check=False,
        )
        codes.append(r.returncode)

    threads = [threading.Thread(target=note, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [0, 0, 0, 0]
    text = (repo / "TASKS.md").read_text()
    assert all(f": note {n}\n" in text for n in range(4))


@pytest.mark.parametrize(
    ("ours", "theirs", "want"),
    [
        # An agent note right above a line the human edited: not a conflict.
        ("a\nB\nc\n", "a\n  * note\nb\nc\n", "a\n  * note\nB\nc\n"),
        # A tick on one line, a human edit on the next.
        ("[ ] a\n[ ] B\n", "[x] a\n[ ] b\n", "[x] a\n[ ] B\n"),
        # Same change on both sides.
        ("a\nX\nc\n", "a\nX\nc\n", "a\nX\nc\n"),
        # Appended at the end, with no trailing newline in ours.
        ("a\nb", "a\nb\nc\n", "a\nb\nc\n"),
    ],
)
def test_merge_keeps_both_sides(ours: str, theirs: str, want: str) -> None:
    base = "a\nb\nc\n" if "[" not in ours else "[ ] a\n[ ] b\n"
    if ours == "a\nb":
        base = "a\nb\n"
    assert tasks_md.merge(ours, base, theirs) == want


def test_merge_conflicts_on_the_same_line() -> None:
    assert tasks_md.merge("a\nB\nc\n", "a\nb\nc\n", "a\nb!\nc\n") is None
    assert tasks_md.merge("a\nc\n", "a\nb\nc\n", "a\nb!\nc\n") is None

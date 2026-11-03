"""Real command, script and git failures must preserve both sides of an edit."""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from talos_bar import glue


@pytest.mark.parametrize("exit_style", ["return 7", "raise SystemExit(7)"])
def test_script_failure_is_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_style: str,
) -> None:
    (tmp_path / "failed_save.py").write_text(f"def main(argv):\n    {exit_style}\n")
    monkeypatch.setattr(glue, "SCRIPTS", tmp_path)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    sys.modules.pop("failed_save", None)
    with pytest.raises(RuntimeError, match="7"):
        glue.run_script("failed_save")


def test_external_command_failure_is_not_success() -> None:
    with pytest.raises(RuntimeError, match="status 7"):
        glue.run([sys.executable, "-c", "raise SystemExit(7)"])


def test_save_checks_disk_and_retains_exact_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    file = repo / "TASKS.md"
    file.write_text("base\n")
    subprocess.run(["git", "-C", str(repo), "add", "TASKS.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    monkeypatch.setenv("TASKS_REPO", str(repo))
    monkeypatch.delenv("TALOS_BAR_TASKS_CMD", raising=False)
    glue.forget_scripts()
    items = glue.load_file(Path(__file__).parents[1] / "src/talos_bar/glue/builtin/tasks.py")
    item = next(i for i in items if i.id == "tasks")
    ctx = glue.Context()
    save = item.actions["save"]
    file.write_text("external\n")
    with pytest.raises(RuntimeError, match="changed"):
        save(ctx, json.dumps({"text": "draft\n\n", "base": "base\n"}))
    assert file.read_text() == "external\n"
    # A fresh base permits a deliberate new edit, with no newline normalization.
    save(ctx, json.dumps({"text": "draft\n\n", "base": "external\n"}))
    assert file.read_text() == "draft\n\n"
    # Queued autosaves can share the original base without conflicting with
    # their own predecessor, but cannot overwrite a subsequent external edit.
    save(ctx, json.dumps({"text": "newer", "base": "external\n"}))
    assert file.read_text() == "newer"
    # Hot reload replaces the dynamically loaded glue module, but pending
    # actions still carry bases from the same live editor/context.
    reloaded = glue.load_file(Path(__file__).parents[1] / "src/talos_bar/glue/builtin/tasks.py")
    save = next(i for i in reloaded if i.id == "tasks").actions["save"]
    # The GUI acknowledges the first save before the second completes, so
    # its third edit carries that acknowledged intermediate base.
    save(ctx, json.dumps({"text": "third", "base": "draft\n\n"}))
    assert file.read_text() == "third"
    file.write_text("another editor")
    with pytest.raises(RuntimeError, match="changed"):
        save(ctx, json.dumps({"text": "latest", "base": "external\n"}))
    assert file.read_text() == "another editor"
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="commit"):
        save(ctx, json.dumps({"text": "attempted", "base": "another editor"}))
    assert file.read_text() == "attempted"
    hook.write_text("#!/bin/sh\nexit 0\n")
    save(ctx, json.dumps({"text": "newer after failure", "base": "another editor"}))
    assert file.read_text() == "newer after failure"


@pytest.mark.parametrize("failure", ["stage", "commit"])
def test_git_failure_reaches_caller_and_retry_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    hook = tmp_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    lock = tmp_path / ".git/index.lock"
    if failure == "stage":
        lock.write_text("")
    monkeypatch.setenv("TASKS_REPO", str(tmp_path))
    glue.forget_scripts()
    encoded = base64.b64encode(b"draft\n\n").decode()
    with pytest.raises(RuntimeError, match=r"commit|stage"):
        glue.run_script("tasks_cli", "save64", encoded)
    assert (tmp_path / "TASKS.md").read_text() == "draft\n\n"
    hook.write_text("#!/bin/sh\nexit 0\n")
    if failure == "stage":
        lock.rename(tmp_path / ".git/old-lock")
    glue.run_script("tasks_cli", "save64", encoded)
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == ""
    committed = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "HEAD:TASKS.md"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert committed.stdout == "draft\n\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable fixture")
def test_legacy_external_command_round_trip_and_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = tmp_path / "TASKS.md"
    file.write_text("original\n\n")
    command = tmp_path / "tasks-command"
    command.write_text(
        f"#!{sys.executable}\n"
        "import base64, sys\n"
        "from pathlib import Path\n"
        f"file = Path({str(file)!r})\n"
        "if sys.argv[1] == 'list': print('{}')\n"
        "elif sys.argv[1] == 'read': print(file.read_text())\n"
        "elif sys.argv[1] == 'save64':\n"
        "    assert len(sys.argv) == 3, 'external interface changed'\n"
        "    file.write_text(base64.b64decode(sys.argv[2]).decode())\n"
    )
    command.chmod(0o755)
    monkeypatch.setenv("TALOS_BAR_TASKS_CMD", str(command))
    items = glue.load_file(Path(__file__).parents[1] / "src/talos_bar/glue/builtin/tasks.py")
    item = next(i for i in items if i.id == "tasks")
    assert item.popup is not None
    ctx = glue.Context()
    content = item.popup(ctx)
    assert content["text"] == "original\n\n"
    item.actions["save"](ctx, json.dumps({"text": content["text"], "base": content["text"]}))
    assert file.read_text() == "original\n\n"
    file.write_text("external")
    with pytest.raises(RuntimeError, match="changed"):
        item.actions["save"](ctx, json.dumps({"text": "draft", "base": content["text"]}))
    assert file.read_text() == "external"


def test_editor_commit_leaves_unrelated_staged_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "unrelated.txt").write_text("user's pending work")
    subprocess.run(["git", "-C", str(tmp_path), "add", "unrelated.txt"], check=True)
    monkeypatch.setenv("TASKS_REPO", str(tmp_path))
    glue.forget_scripts()
    glue.run_script("tasks_cli", "save64", base64.b64encode(b"tasks").decode())
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == "A  unrelated.txt\n"

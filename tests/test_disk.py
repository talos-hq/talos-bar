"""Disk item threshold, and the tree page the click opens."""

from pathlib import Path

import pytest

from talos_bar import diskview, glue
from talos_bar.glue import Context

BUILTIN = Path(__file__).resolve().parents[1] / "src" / "talos_bar" / "glue" / "builtin"


def _disk_module() -> glue.Item:
    """Load builtin/disk.py the way the bar does, without a Qt import."""
    items = {it.id: it for it in glue.load_file(BUILTIN / "disk.py")}
    return items["disk"]


def _fake_usage(monkeypatch: pytest.MonkeyPatch, used: int, total: int) -> None:
    import importlib

    disk = importlib.import_module("talos_bar.glue.builtin.disk")
    monkeypatch.setattr(disk.shutil, "disk_usage", lambda _p: (total, used, total - used))


def test_hidden_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALOS_BAR_DISK_THRESHOLD", "0.90")
    _fake_usage(monkeypatch, used=500, total=1_000)
    disk = _disk_module()
    assert disk.refresh(Context()) is None


def test_visible_at_threshold_and_red_when_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALOS_BAR_DISK_THRESHOLD", "0.90")
    _fake_usage(monkeypatch, used=900, total=1_000)
    out = _disk_module().refresh(Context())
    assert out is not None
    assert out["text"] == "90%"
    assert out["dot"] == "#f0a92a"

    _fake_usage(monkeypatch, used=970, total=1_000)
    out = _disk_module().refresh(Context())
    assert out["text"] == "97%"
    assert out["dot"] == "#e5322d"


def test_threshold_is_clamped_and_tolerates_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    disk = importlib.import_module("talos_bar.glue.builtin.disk")
    for raw, expected in (("2", 1.0), ("0", 0.1), ("nonsense", 0.9), ("0.5", 0.5)):
        monkeypatch.setenv("TALOS_BAR_DISK_THRESHOLD", raw)
        assert disk.threshold() == expected
    monkeypatch.delenv("TALOS_BAR_DISK_THRESHOLD")
    assert disk.threshold() == 0.9


def _tree() -> diskview.Tree:
    node = {
        "path": "/",
        "allocated": 3_000,
        "apparent": 3_000,
        "is_dir": True,
        "children": [
            {
                "path": "/home/<script>",
                "allocated": 2_000,
                "apparent": 2_000,
                "is_dir": True,
                "children": [
                    {
                        "path": "/home/<script>/a.bin",
                        "allocated": 2_000,
                        "apparent": 2_000,
                        "is_dir": False,
                        "children": [],
                    }
                ],
            },
            {
                "path": "/var",
                "allocated": 1_000,
                "apparent": 1_000,
                "is_dir": True,
                "children": [],
            },
        ],
    }
    return diskview.Tree(
        root="/",
        allocated=3_000,
        apparent=3_000,
        entries=3,
        errors=0,
        seconds=0.5,
        node=node,
    )


def test_render_escapes_names_and_orders_by_size() -> None:
    page = diskview.render(_tree())
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    # Rows show the basename; the bigger sibling comes first.
    assert page.index("&lt;script&gt;") < page.index(">var<")
    assert "2.9 KiB" in page  # 3,000 bytes


def test_human_units() -> None:
    assert diskview.human(999) == "999 B"
    assert diskview.human(1024) == "1.0 KiB"
    assert diskview.human(3 << 30) == "3.0 GiB"


def test_scan_is_read_only_and_ignores_mounts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    payload = {
        "allocated": 1,
        "apparent": 1,
        "entries": 1,
        "errors": 0,
        "seconds": 0.1,
        "root": {"path": "/", "allocated": 1, "apparent": 1, "is_dir": True, "children": []},
    }

    class Done:
        returncode = 0
        stdout = __import__("json").dumps(payload)
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> "Done":
        calls.append(command)
        return Done()

    monkeypatch.setattr(diskview.subprocess, "run", fake_run)
    monkeypatch.setenv("TALOS_BAR_JETTISON", "/usr/bin/true")
    tree = diskview.scan("/", depth=4, min_size=0)
    assert tree.root == "/"
    command = calls[0]
    assert "--one-filesystem" in command
    assert "--depth" in command
    assert "4" in command

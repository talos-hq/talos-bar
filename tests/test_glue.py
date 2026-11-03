import json
import sys
from pathlib import Path

import pytest

from talos_bar import glue


def test_builtin_glue_loads_and_refreshes() -> None:
    builtin = glue.glue_dirs()[0]
    ids: dict[str, glue.Item] = {}
    for f in sorted(builtin.glob("*.py")):
        for it in glue.load_file(f):
            ids[it.id] = it
    assert "clock" in ids
    assert ids["clock"].plain
    assert ids["clock"].interval == 1
    ctx = glue.Context()
    assert ids["clock"].refresh is not None
    assert "text" in ids["clock"].refresh(ctx)  # type: ignore[operator]


def test_broken_glue_is_isolated(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("raise RuntimeError('boom')\n")
    assert glue.load_file(bad) == []


def test_example_glue_loads() -> None:
    example = Path(__file__).parent.parent / "docs" / "examples" / "hello.py"
    assert [i.id for i in glue.load_file(example)] == ["hello"]


def test_run_script_captures_stdout() -> None:
    out = glue.run_script("calendar_agenda")
    assert "next_label" in json.loads(out)


def test_command_timeout_reports_failure_without_note_contents() -> None:
    draft = "private draft passed as an argument"
    with pytest.raises(RuntimeError, match="timed out") as error:
        glue.run([sys.executable, "-c", "import time; time.sleep(5)", draft], timeout=0.05)
    assert draft not in str(error.value)

from pathlib import Path

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

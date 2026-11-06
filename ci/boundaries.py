"""Module boundaries: an import across modules must be declared in ci.toml.

This is what makes partial invalidation sound. A lane skipped because module A
did not change is only safe if nothing in its closure reaches A undeclared.

Relative imports count like any other: `from .x import y` in `pkg/a.py` is an edge
to `pkg.x`. One that cannot be resolved inside the package is rejected rather than
ignored, because an edge the gate cannot see is a lane it may wrongly skip.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath


class UnresolvableImport(ValueError):
    """A relative import with no meaning here: outside the package, or in a non-package file."""


def _relative_base(node: ast.ImportFrom, importer: str | None, is_package: bool) -> str:
    """The absolute package a relative import starts from."""
    if not importer:
        raise UnresolvableImport(
            f"line {node.lineno}: relative import in a file outside the package"
        )
    parts = importer.split(".")
    # In pkg/a.py `.` is pkg; in pkg/__init__.py, whose module name is pkg, `.` is pkg too.
    drop = node.level - 1 if is_package else node.level
    if drop >= len(parts):
        raise UnresolvableImport(f"line {node.lineno}: relative import leaves the package")
    return ".".join(parts[: len(parts) - drop])


def imported_modules(
    source: str, known: set[str], importer: str | None = None, is_package: bool = False
) -> set[str]:
    """Package modules a source imports, lazily or not, resolved against `known`.

    `importer` is the source's own dotted name, which relative imports resolve from.
    Raises UnresolvableImport when one cannot be resolved.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = _relative_base(node, importer, is_package)
                module = f"{base}.{module}" if module else base
            names = [module] + [f"{module}.{a.name}" for a in node.names]
        found.update(name for name in names if name in known)
    return found


def referenced_modules(
    source: str, known: set[str], package: str, importer: str | None = None
) -> set[str]:
    """As imported_modules, plus dotted references in strings (mock.patch targets)."""
    found = imported_modules(source, known, importer)
    # A slash means a path or repo slug ("owner/pkg"), not a dotted module reference.
    reference = re.compile(r"(?<![\w/-])" + re.escape(package) + r"((?:\.[A-Za-z_][A-Za-z_0-9]*)*)")
    for match in reference.finditer(source):
        name = package + match.group(1)
        # "pkg.booking.db.connect" means module pkg.booking.db.
        while name and name not in known:
            name = name.rpartition(".")[0]
        if name:
            found.add(name)
    return found


def python_module_name(path: str, source_root: str = "src") -> str:
    """src/pkg/booking/db.py -> pkg.booking.db"""
    parts = PurePosixPath(path).relative_to(source_root or ".").with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def package_sources(files: Iterable[str], package: str, source_root: str = "src") -> list[str]:
    """The package's Python files; a source_root of "" means the repository root."""
    prefix = f"{source_root}/{package}/" if source_root else f"{package}/"
    return [f for f in files if f.startswith(prefix) and f.endswith(".py")]


def check_source_imports(
    files: Iterable[str],
    module_of: Callable[[str], str],
    closure: Callable[[str], set[str]],
    read: Callable[[str], str],
    package: str,
    source_root: str = "src",
) -> list[str]:
    """Violations: a package file importing outside its module's declared closure."""
    sources = package_sources(files, package, source_root)
    owner = {python_module_name(f, source_root): module_of(f) for f in sources}
    problems = []
    for path in sources:
        mine = module_of(path)
        try:
            names = imported_modules(
                read(path),
                set(owner),
                python_module_name(path, source_root),
                PurePosixPath(path).name == "__init__.py",
            )
        except UnresolvableImport as error:
            problems.append(f"{path}: {error}")
            continue
        for name in sorted(names):
            if owner[name] not in closure(mine):
                problems.append(
                    f"{path}: {mine} imports {name} ({owner[name]}); "
                    "declare it in ci.toml or remove it"
                )
    return problems


def check_test_imports(
    tests: Iterable[str],
    allowed: set[str],
    owner: dict[str, str],
    read: Callable[[str], str],
    package: str,
) -> list[str]:
    """Violations: a lane's test that references a module outside the lane's inputs.

    A relative import in a test is rejected: tests sit outside the package, so the
    gate cannot say what it reaches.
    """
    problems = []
    for path in tests:
        try:
            names = referenced_modules(read(path), set(owner), package)
        except UnresolvableImport as error:
            problems.append(f"{path}: {error}")
            continue
        for name in sorted(names):
            if owner[name] not in allowed:
                problems.append(f"{path}: uses {name} ({owner[name]}), outside its lane's inputs")
    return problems

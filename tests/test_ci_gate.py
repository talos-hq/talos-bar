"""The gate's selection and boundary logic, on synthetic contracts (no Docker).

Generic: nothing here names this repository's package or modules.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import boundaries, gate
from ci.gate import Contract

CONTRACT = """
[gate]
status_context = "local-gate"
base = "origin/main"
package = "pkg"
source_root = "src"
[modules.low]
deps = []
paths = ["src/pkg/low.py"]
[modules.mail]
deps = ["low"]
paths = ["src/pkg/mail.py"]
[modules.web]
deps = ["low", "mail"]
paths = ["src/pkg/**"]
[modules.repo]
deps = []
paths = ["**"]
[lanes.low-unit]
modules = ["low"]
tests = ["tests/test_low.py"]
run = ["true"]
[lanes.e2e]
modules = ["web"]
run = ["true"]
[lanes.always]
always = true
run = ["true"]
"""
TREE = {
    "src/pkg/low.py": "a",
    "src/pkg/mail.py": "b",
    "src/pkg/app.py": "c",
    "readme.md": "d",
    "tests/test_low.py": "e",
}
PATHS = {
    "low": "src/pkg/low.py",
    "mail": "src/pkg/mail.py",
    "app": "src/pkg/app.py",
    "docs": "readme.md",
    "test": "tests/test_low.py",
}


def changed(**blobs: str) -> dict[str, str]:
    return {**TREE, **{PATHS[k]: v for k, v in blobs.items()}}


class DigestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = Contract.parse(CONTRACT)

    def same(self, lane: str, tree: dict[str, str]) -> bool:
        return self.contract.digest(lane, TREE) == self.contract.digest(lane, tree)

    def test_first_matching_module_wins(self) -> None:
        self.assertEqual("low", self.contract.module_of("src/pkg/low.py"))
        self.assertEqual("web", self.contract.module_of("src/pkg/app.py"))
        self.assertEqual("repo", self.contract.module_of("anything"))

    def test_change_invalidates_lanes_above_it(self) -> None:
        self.assertFalse(self.same("low-unit", changed(low="x")))
        self.assertFalse(self.same("e2e", changed(low="x")))

    def test_change_leaves_lanes_below_it_alone(self) -> None:
        self.assertTrue(self.same("low-unit", changed(app="x")))

    def test_every_module_in_the_closure_invalidates(self) -> None:
        self.assertFalse(self.same("e2e", changed(mail="x")))

    def test_docs_invalidate_only_the_always_lane(self) -> None:
        self.assertTrue(self.same("low-unit", changed(docs="x")))
        self.assertTrue(self.same("e2e", changed(docs="x")))
        self.assertFalse(self.same("always", changed(docs="x")))

    def test_lane_own_test_invalidates_it(self) -> None:
        self.assertFalse(self.same("low-unit", changed(test="x")))
        self.assertTrue(self.same("e2e", changed(test="x")))

    def test_lane_definition_invalidates_it(self) -> None:
        before = self.contract.digest("e2e", TREE)
        self.contract.lanes["e2e"]["run"] = ["false"]
        self.assertNotEqual(before, self.contract.digest("e2e", TREE))


class SettingsTest(unittest.TestCase):
    def test_package_comes_from_the_contract(self) -> None:
        contract = Contract.parse(CONTRACT)
        self.assertEqual("pkg", contract.package)
        self.assertEqual("src", contract.source_root)

    def test_package_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            Contract.parse(CONTRACT.replace('package = "pkg"\n', ""))

    def test_source_root_defaults_and_can_be_changed(self) -> None:
        self.assertEqual(
            "src", Contract.parse(CONTRACT.replace('source_root = "src"\n', "")).source_root
        )
        self.assertEqual("pkg.a.b", boundaries.python_module_name("lib/pkg/a/b.py", "lib"))
        self.assertEqual("pkg.a", boundaries.python_module_name("pkg/a/__init__.py", ""))
        self.assertEqual(
            ["lib/pkg/a.py"],
            boundaries.package_sources(
                ["lib/pkg/a.py", "tests/b.py", "lib/pkg/c.txt"], "pkg", "lib"
            ),
        )

    def test_test_file_pattern_reaches_every_test_directory(self) -> None:
        for path in ("tests/test_a.py", "tests/e2e/test_a.py", "tests/scripts/test_a.py"):
            self.assertTrue(gate.TEST_FILE.fullmatch(path), path)
        for path in ("tests/e2e/run_e2e.py", "src/test_a.py"):
            self.assertFalse(gate.TEST_FILE.fullmatch(path), path)


class BoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = Contract.parse(CONTRACT)

    def violations(self, sources: dict[str, str]) -> list[str]:
        return boundaries.check_source_imports(
            sources, self.contract.module_of, self.contract.closure, sources.__getitem__, "pkg"
        )

    def test_declared_import_passes(self) -> None:
        self.assertEqual(
            [], self.violations({"src/pkg/mail.py": "from pkg import low", "src/pkg/low.py": ""})
        )

    def test_upward_import_fails(self) -> None:
        found = self.violations({"src/pkg/low.py": "import pkg.mail", "src/pkg/mail.py": ""})
        self.assertEqual(1, len(found))
        self.assertIn("low imports pkg.mail", found[0])

    def test_lazy_import_counts(self) -> None:
        found = self.violations(
            {"src/pkg/low.py": "def f():\n    from pkg import mail\n", "src/pkg/mail.py": ""}
        )
        self.assertEqual(1, len(found))

    def test_mock_patch_target_counts_for_a_test(self) -> None:
        names = {"pkg.low": "low", "pkg.mail": "mail"}
        found = boundaries.check_test_imports(
            ["t.py"], {"low"}, names, lambda _: 'patch("pkg.mail.send")', "pkg"
        )
        self.assertEqual(1, len(found))

    def test_package_name_is_not_hardwired(self) -> None:
        names = {"other.mail": "mail"}
        found = boundaries.check_test_imports(
            ["t.py"], {"low"}, names, lambda _: 'patch("other.mail.send")', "other"
        )
        self.assertEqual(1, len(found))


class RelativeImportTest(unittest.TestCase):
    """Before this was fixed, every violation below passed `check` silently."""

    def setUp(self) -> None:
        self.contract = Contract.parse(CONTRACT)

    def violations(self, sources: dict[str, str]) -> list[str]:
        return boundaries.check_source_imports(
            sources, self.contract.module_of, self.contract.closure, sources.__getitem__, "pkg"
        )

    def test_relative_upward_import_fails(self) -> None:
        for source in ("from .mail import send", "from . import mail", "from .mail import *"):
            found = self.violations({"src/pkg/low.py": source, "src/pkg/mail.py": ""})
            self.assertEqual(1, len(found), source)
            self.assertIn("low imports pkg.mail", found[0])

    def test_lazy_relative_import_fails(self) -> None:
        found = self.violations(
            {"src/pkg/low.py": "def f():\n    from . import mail\n", "src/pkg/mail.py": ""}
        )
        self.assertEqual(1, len(found))

    def test_declared_relative_import_passes(self) -> None:
        self.assertEqual(
            [], self.violations({"src/pkg/mail.py": "from .low import x", "src/pkg/low.py": ""})
        )

    def test_level_is_resolved_from_a_nested_module(self) -> None:
        known = {"pkg.low", "pkg.sub.near", "pkg.sub"}
        self.assertEqual(
            {"pkg.sub.near"},
            boundaries.imported_modules("from .near import x", known, "pkg.sub.mod"),
        )
        self.assertEqual(
            {"pkg.low"}, boundaries.imported_modules("from ..low import x", known, "pkg.sub.mod")
        )

    def test_level_is_resolved_from_a_package_init(self) -> None:
        known = {"pkg.sub.near", "pkg.low"}
        self.assertEqual(
            {"pkg.sub.near"},
            boundaries.imported_modules("from .near import x", known, "pkg.sub", True),
        )
        self.assertEqual(
            {"pkg.low"}, boundaries.imported_modules("from ..low import x", known, "pkg.sub", True)
        )

    def test_relative_import_leaving_the_package_is_rejected(self) -> None:
        found = self.violations({"src/pkg/low.py": "from ...elsewhere import x"})
        self.assertEqual(1, len(found))
        self.assertIn("leaves the package", found[0])

    def test_relative_import_in_a_test_is_rejected(self) -> None:
        found = boundaries.check_test_imports(
            ["t.py"], set(), {"pkg.low": "low"}, lambda _: "from .low import x", "pkg"
        )
        self.assertEqual(1, len(found))
        self.assertIn("outside the package", found[0])


def commit(path: Path, files: dict[str, str]) -> str:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    if not (path / ".git").exists():
        run("init", "-q", "-b", "main")
    for name, content in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
    run("add", "-A")
    run("commit", "-qm", "c", "--allow-empty")
    return run("rev-parse", "HEAD")


def set_ref(path: Path, ref: str, sha: str) -> None:
    subprocess.run(["git", "update-ref", ref, sha], cwd=path, check=True)


class GitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = mock.patch.object(gate, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)


class CheckTest(GitTestCase):
    def problems(self, toml: str, files: dict[str, str]) -> list[str]:
        sha = commit(self.root, {"ci.toml": toml, **files})
        return gate.check(gate.load_contract(sha), sha)

    def test_relative_import_is_caught_end_to_end(self) -> None:
        found = self.problems(
            CONTRACT,
            {
                "src/pkg/low.py": "from . import mail\n",
                "src/pkg/mail.py": "",
                "tests/test_low.py": "",
            },
        )
        self.assertTrue(any("low imports pkg.mail" in p for p in found), found)

    def test_nested_test_outside_every_lane_is_caught(self) -> None:
        found = self.problems(
            CONTRACT,
            {"tests/test_low.py": "", "tests/e2e/test_x.py": "", "tests/scripts/test_y.py": ""},
        )
        self.assertIn("tests/e2e/test_x.py: not in any lane", found)
        self.assertIn("tests/scripts/test_y.py: not in any lane", found)

    def test_exclude_is_rejected(self) -> None:
        toml = CONTRACT.replace("[lanes.e2e]\n", '[lanes.e2e]\nexclude = ["mail"]\n')
        found = self.problems(toml, {"tests/test_low.py": ""})
        self.assertTrue(any("exclude is not supported" in p for p in found), found)

    def test_a_lane_needs_a_command(self) -> None:
        toml = CONTRACT.replace(
            '[lanes.always]\nalways = true\nrun = ["true"]\n', "[lanes.always]\nalways = true\n"
        )
        found = self.problems(toml, {"tests/test_low.py": ""})
        self.assertIn("lane always: has no run command", found)


UNIT_ONLY = """
[gate]
status_context = "local-gate"
base = "origin/main"
package = "pkg"
[modules.repo]
deps = []
paths = ["**"]
[lanes.unit]
modules = ["repo"]
run = ["true"]
"""
WITH_NETWORK = (
    UNIT_ONLY + '[lanes.drift]\nnetwork = true\nrun = ["sh", "-c", "echo x >> ran.log"]\n'
)


class NetworkLaneTest(GitTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sha = commit(self.root, {"ci.toml": WITH_NETWORK, ".gitignore": "ran.log\n"})
        self.contract = gate.load_contract(self.sha)

    def test_a_network_lane_is_never_reused_from_a_receipt(self) -> None:
        digest = self.contract.digest("drift", gate.tree_at(self.sha))
        gate.write_receipt("drift", digest, self.sha, 0.0)
        self.assertEqual("live", gate.plan(self.contract, self.sha)["drift"][0])

    def test_a_network_lane_is_never_unchanged_from_the_base(self) -> None:
        set_ref(self.root, "refs/remotes/origin/main", self.sha)
        states = gate.plan(self.contract, self.sha)
        self.assertEqual("live", states["drift"][0])
        self.assertEqual("unchanged", states["unit"][0])

    def test_verify_runs_the_network_lane_each_time_and_never_records_it(self) -> None:
        digest = self.contract.digest("unit", gate.tree_at(self.sha))
        gate.write_receipt("unit", digest, self.sha, 0.0)
        args = mock.Mock(sha=self.sha, remote=None)
        self.assertEqual(0, gate.cmd_verify(args))
        self.assertEqual(0, gate.cmd_verify(args))
        self.assertEqual(2, len((self.root / "ran.log").read_text().splitlines()))
        self.assertFalse(list((gate.state_dir() / "receipts").glob("drift-*")))

    def test_a_failing_network_lane_fails(self) -> None:
        contract = gate.load_contract(self.sha)
        contract.lanes["drift"]["run"] = ["false"]
        self.assertEqual(["drift"], gate.run_live(contract, self.sha))

    def test_a_network_lane_needs_the_commit_checked_out(self) -> None:
        other = commit(self.root, {"a.txt": "a"})
        self.assertTrue(gate.run_live(self.contract, self.sha))
        self.assertEqual([], gate.run_live(self.contract, other))


class RemoteBaseTest(GitTestCase):
    def test_a_mirror_does_not_inherit_unchanged_from_origin(self) -> None:
        first = commit(self.root, {"ci.toml": UNIT_ONLY})
        head = commit(self.root, {"src.txt": "changed"})
        set_ref(self.root, "refs/remotes/origin/main", head)
        set_ref(self.root, "refs/remotes/prod/main", first)
        contract = gate.load_contract(head)
        self.assertEqual("unchanged", gate.plan(contract, head)["unit"][0])
        self.assertEqual("run", gate.plan(contract, head, remote="prod")["unit"][0])

    def test_an_unknown_remote_falls_back_to_the_base(self) -> None:
        sha = commit(self.root, {"ci.toml": UNIT_ONLY})
        contract = gate.load_contract(sha)
        self.assertEqual("origin/main", gate.resolve_base(contract, "nosuch"))


if __name__ == "__main__":
    unittest.main()

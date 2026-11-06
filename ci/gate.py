"""Local CI gate: lane selection by content digest, receipts and attestation.

The gate runs on the developer's machine and the proof is a commit status on the
pushed SHA, required on the default branch. Everything specific to a repository
is in ci.toml; this file and boundaries.py are meant to be copied unchanged.

A lane's digest covers its definition and the blobs of its module closure at a
commit, so a lane is satisfied when a receipt holds that digest (it passed here
before) or the digest matches the base commit (nothing it depends on changed).
A lane with `network = true` depends on something outside the tree, so neither
applies: it runs on every gate, every verify and every attest.
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ci import boundaries

ROOT = Path(__file__).resolve().parents[1]
# Any depth under tests/, including tests/e2e and tests/scripts.
TEST_FILE = re.compile(r"tests/(?:[^/]+/)*test_[^/]*\.py")


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def glob_match(path: str, pattern: str) -> bool:
    """fnmatch, with `dir/**` meaning everything under dir/."""
    if pattern == "**":
        return True
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern)


@dataclass
class Contract:
    modules: dict[str, dict]
    lanes: dict[str, dict]
    gate: dict

    @classmethod
    def parse(cls, text: str) -> Contract:
        data = tomllib.loads(text)
        gate = data["gate"]
        if not gate.get("package"):
            raise SystemExit("ci.toml: [gate] needs package, the importable Python package name")
        gate.setdefault("source_root", "src")
        return cls(data["modules"], data["lanes"], gate)

    @property
    def package(self) -> str:
        return self.gate["package"]

    @property
    def source_root(self) -> str:
        return self.gate["source_root"]

    def module_of(self, path: str) -> str:
        for name, module in self.modules.items():
            if any(glob_match(path, p) for p in module["paths"]):
                return name
        return ""

    def closure(self, module: str) -> set[str]:
        seen: set[str] = set()
        stack = [module]
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.modules[current]["deps"])
        return seen

    def lane_modules(self, lane: str) -> set[str]:
        spec = self.lanes[lane]
        found: set[str] = set()
        for module in spec.get("modules", []):
            found |= self.closure(module)
        return found

    def inputs(self, lane: str, tree: dict[str, str]) -> dict[str, str]:
        """The blobs that decide whether this lane must run."""
        if self.lanes[lane].get("always"):
            return tree
        modules = self.lane_modules(lane)
        own = set(self.lanes[lane].get("tests", []))
        return {p: b for p, b in tree.items() if p in own or self.module_of(p) in modules}

    def digest(self, lane: str, tree: dict[str, str]) -> str:
        blob = json.dumps(
            [self.lanes[lane], sorted(self.inputs(lane, tree).items())], sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def command(self, lane: str) -> list[str]:
        return self.lanes[lane]["run"]


def tree_at(rev: str) -> dict[str, str]:
    """Tracked path -> object id at rev (a gitlink's id is its pinned commit)."""
    tree = {}
    for entry in filter(None, git("ls-tree", "-r", "-z", rev).split("\0")):
        meta, path = entry.split("\t", 1)
        tree[path] = meta.split()[2]
    return tree


def load_contract(rev: str | None = None) -> Contract:
    if rev:
        return Contract.parse(git("show", f"{rev}:ci.toml"))
    return Contract.parse((ROOT / "ci.toml").read_text(encoding="utf-8"))


def state_dir() -> Path:
    # Shared by every worktree of a clone.
    common = Path(git("rev-parse", "--git-common-dir"))
    path = (ROOT / common if not common.is_absolute() else common) / "ci-gate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def has_receipt(lane: str, digest: str) -> bool:
    return (state_dir() / "receipts" / f"{lane}-{digest}.json").exists()


def write_receipt(lane: str, digest: str, sha: str, seconds: float) -> None:
    folder = state_dir() / "receipts"
    folder.mkdir(exist_ok=True)
    record = {
        "lane": lane,
        "digest": digest,
        "sha": sha,
        "seconds": round(seconds, 1),
        "at": int(time.time()),
    }
    (folder / f"{lane}-{digest}.json").write_text(json.dumps(record), encoding="utf-8")


def resolve_base(contract: Contract, remote: str | None) -> str:
    """The ref a head is compared with: the default branch of the repo being pushed to.

    Without this, a commit already on origin's default branch would be its own merge
    base, and so "unchanged", when it is pushed to a second repository that requires
    the status (a deploy mirror).
    """
    if remote:
        branch = contract.gate["base"].partition("/")[2]
        ref = f"refs/remotes/{remote}/{branch}"
        if git("rev-parse", "--verify", "--quiet", ref, check=False):
            return ref
    return contract.gate["base"]


def plan(
    contract: Contract, sha: str, full: bool = False, remote: str | None = None
) -> dict[str, tuple[str, str]]:
    """Lane -> (state, digest); state is run, live, reused or unchanged."""
    tree = tree_at(sha)
    base = git("merge-base", resolve_base(contract, remote), sha, check=False)
    base_contract = None
    base_tree: dict[str, str] = {}
    # Before this gate existed the base has no contract to compare against.
    if base and git("cat-file", "-t", f"{base}:ci.toml", check=False):
        base_contract = load_contract(base)
        base_tree = tree_at(base)
    result = {}
    for lane, spec in contract.lanes.items():
        digest = contract.digest(lane, tree)
        if spec.get("network"):
            state = "live"
        elif not full and has_receipt(lane, digest):
            state = "reused"
        elif (
            not full
            and not spec.get("always")
            and base_contract
            and lane in base_contract.lanes
            and base_contract.digest(lane, base_tree) == digest
        ):
            state = "unchanged"
        else:
            state = "run"
        result[lane] = (state, digest)
    return result


def run_live(contract: Contract, sha: str) -> list[str]:
    """Run the network lanes now; returns the failures. They read the checked-out tree."""
    lanes = [lane for lane, spec in contract.lanes.items() if spec.get("network")]
    if lanes and sha != git("rev-parse", "HEAD"):
        return [f"{lane}: check out {sha[:12]} to prove it" for lane in lanes]
    return [
        lane
        for lane in lanes
        if subprocess.run(contract.command(lane), cwd=ROOT, check=False).returncode != 0
    ]


def check(contract: Contract, rev: str = "HEAD") -> list[str]:
    """Contract violations: unmapped files, undeclared imports, unassigned tests."""
    tree = tree_at(rev)

    def read(path: str) -> str:
        return git("show", f"{rev}:{path}")

    problems = [f"{path}: not in any module" for path in tree if not contract.module_of(path)]
    problems += boundaries.check_source_imports(
        tree,
        contract.module_of,
        contract.closure,
        read,
        contract.package,
        contract.source_root,
    )

    sources = boundaries.package_sources(tree, contract.package, contract.source_root)
    owner = {
        boundaries.python_module_name(f, contract.source_root): contract.module_of(f)
        for f in sources
    }
    seen: dict[str, str] = {}
    for lane, spec in contract.lanes.items():
        if "exclude" in spec:
            problems.append(
                f"lane {lane}: exclude is not supported; a lane reads its whole closure"
            )
        if not spec.get("run"):
            problems.append(f"lane {lane}: has no run command")
        tests = spec.get("tests", [])
        for test in tests:
            if test not in tree:
                problems.append(f"lane {lane}: {test} does not exist")
            elif test in seen:
                problems.append(f"{test}: in lanes {seen[test]} and {lane}")
            seen[test] = lane
        present = [t for t in tests if t in tree]
        problems += boundaries.check_test_imports(
            present, contract.lane_modules(lane), owner, read, contract.package
        )
    named_in_run = {a for spec in contract.lanes.values() for a in spec.get("run", [])}
    for path in tree:
        if (
            TEST_FILE.fullmatch(path)
            and path not in seen
            and path[:-3].replace("/", ".") not in named_in_run
        ):
            problems.append(f"{path}: not in any lane")
    return problems


def install_hooks() -> None:
    if git("rev-parse", "--is-inside-work-tree", check=False) == "true":
        git("config", "core.hooksPath", "ci/hooks/git")


def post_status(contract: Contract, sha: str, summary: str, remote: str | None = None) -> bool:
    # The status belongs on the repository pushed to; a push by bare URL names itself.
    target = remote or "origin"
    match = re.search(
        r"github\.com[:/](.+?)(?:\.git)?$",
        git("remote", "get-url", target, check=False) or target,
    )
    if not match:
        return False
    proc = subprocess.run(
        ["gh", "api", f"repos/{match.group(1)}/statuses/{sha}", "-X", "POST", "-f", "state=success",
         "-f", f"context={contract.gate['status_context']}", "-f", f"description={summary[:140]}"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return proc.returncode == 0


def summarize(states: dict[str, tuple[str, str]], ran: set[str] | None = None) -> str:
    ran = ran or set()
    counts = {"ran": len(ran), "live": 0, "reused": 0, "unchanged": 0}
    for lane, (state, _) in states.items():
        if lane not in ran and state in counts:
            counts[state] += 1
    return ", ".join(f"{n} {name}" for name, n in counts.items())


def cmd_plan(args: argparse.Namespace) -> int:
    for lane, (state, digest) in plan(
        load_contract(args.sha), args.sha, args.full, args.remote
    ).items():
        print(f"{state:10} {lane:18} {digest}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems = check(load_contract(args.sha), args.sha)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"contract: {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace) -> int:
    if git("status", "--porcelain", "--ignore-submodules=dirty"):
        print(
            "gate: commit your changes first; the gate proves a commit, not a working tree",
            file=sys.stderr,
        )
        return 2
    sha = git("rev-parse", "HEAD")
    contract = load_contract()
    problems = check(contract, sha)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    # One gate per clone: lanes share Docker images and ports.
    with open(state_dir() / "lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        states = plan(contract, sha, args.full, args.remote)
        only = set(args.lanes)
        failed: list[str] = []
        ran: set[str] = set()
        for lane, (state, digest) in states.items():
            if state not in ("run", "live") or (only and lane not in only):
                print(
                    f"-- {lane}: {state if state not in ('run', 'live') else 'not selected'}",
                    flush=True,
                )
                continue
            print(f"== {lane}: {' '.join(contract.command(lane))}", flush=True)
            start = time.monotonic()
            code = subprocess.run(contract.command(lane), cwd=ROOT, check=False).returncode
            seconds = time.monotonic() - start
            print(
                f"== {lane}: {'ok' if code == 0 else f'FAILED ({code})'} in {seconds:.0f}s",
                flush=True,
            )
            if code == 0:
                if state == "run":
                    write_receipt(lane, digest, sha, seconds)
                ran.add(lane)
            else:
                failed.append(lane)
    if failed:
        print(f"gate: failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    missing = [
        lane
        for lane, (state, _) in plan(contract, sha, remote=args.remote).items()
        if state == "run"
    ]
    if missing:
        print(f"gate: not run: {', '.join(missing)}")
        return 0
    print(f"gate: {sha[:12]} satisfied ({summarize(states, ran)})")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    contract = load_contract(args.sha)
    problems = check(contract, args.sha)
    missing = [
        lane
        for lane, (state, _) in plan(contract, args.sha, remote=args.remote).items()
        if state == "run"
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems and not missing:
        failed = run_live(contract, args.sha)
        if failed:
            print(f"gate: live checks failed: {', '.join(failed)}", file=sys.stderr)
            return 1
    if missing:
        print(
            f"gate: {args.sha[:12]} has unproven lanes: {', '.join(missing)}\nRun: ./ci.py run",
            file=sys.stderr,
        )
    return 1 if missing or problems else 0


def cmd_attest(args: argparse.Namespace) -> int:
    code = cmd_verify(args)
    if code:
        return code
    contract = load_contract(args.sha)
    summary = "local gate: " + summarize(plan(contract, args.sha, remote=args.remote))
    # The push that triggered this may still be in flight.
    for attempt in range(max(1, args.retries)):
        if post_status(contract, args.sha, summary, args.remote):
            print(f"gate: attested {args.sha[:12]} ({summary})")
            return 0
        if attempt + 1 < args.retries:
            time.sleep(5)
    print(
        f"gate: could not post the status for {args.sha[:12]}; is it pushed?",
        file=sys.stderr,
    )
    return 1


def cmd_install(_: argparse.Namespace) -> int:
    install_hooks()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("plan", cmd_plan),
        ("check", cmd_check),
        ("verify", cmd_verify),
        ("attest", cmd_attest),
    ):
        item = sub.add_parser(name)
        item.add_argument("--sha", default="HEAD")
        item.set_defaults(func=func)
        if name != "check":
            item.add_argument("--remote", help="compare with this remote's default branch")
        if name == "plan":
            item.add_argument("--full", action="store_true", help="ignore receipts and the base")
        if name == "attest":
            item.add_argument("--retries", type=int, default=1)
    run = sub.add_parser("run")
    run.add_argument("lanes", nargs="*", help="only these lanes (diagnostic; never a full proof)")
    run.add_argument("--full", action="store_true", help="ignore receipts and the base")
    run.add_argument("--remote", help="compare with this remote's default branch")
    run.set_defaults(func=cmd_run)
    sub.add_parser("install-hooks").set_defaults(func=cmd_install)
    args = parser.parse_args(argv)
    install_hooks()
    if getattr(args, "sha", None):
        args.sha = git("rev-parse", args.sha)
    return args.func(args)

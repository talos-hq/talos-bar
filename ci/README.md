# Local CI gate

PRs are proven on the developer's machine, not on GitHub Actions.

1. Commit, then run `flock ~/.local/state/ci-gate.lock ./ci.py run`. It may take
   minutes: run it in the background.
2. `git push`. The `pre-push` hook refuses a head the gate has not proven and,
   after the push, posts the `local-gate` commit status to the repository pushed to.
3. `local-gate` is the required check where branch protection is available.

`./ci.py plan` shows what would run. `./ci.py run --full` ignores receipts and the
base. `./ci.py run <lane>` runs one lane for diagnosis. `git push --no-verify` backs
up unfinished work, but such a head has no status and cannot land where it is required.

## How selection works

`ci.toml` assigns every tracked file to a module and lists lanes. A lane's digest
covers its definition and the git blobs of its module closure. A lane runs unless a
receipt holds that digest (`.git/ci-gate/receipts`, shared by worktrees) or the digest
equals the merge-base's (it landed green and nothing it reads changed).

A lane with `network = true` reads something outside the tree, so neither shortcut
applies. It runs on every `run`, `verify` and `attest`, needs the commit checked out,
and never writes a receipt. If it cannot reach the network, the push is refused.

## Keeping skips sound

`./ci.py check` runs before every gate and fails on: a file in no module, an import
(even lazy, even relative) outside a module's declared `deps`, a relative import that
cannot be resolved, a test in no lane, a test using a module (even in a `mock.patch`
string) outside its lane's inputs, a lane with no `run`, or a lane with `exclude`.

## Pushing to a second repository

The status is per repository, and a commit already on `origin/main` is its own merge
base. So when a mirror requires the status, add it as a named remote, compare with its
branch, and land the commit on a side branch first so that the status exists before
the protected push:

    git fetch prod && git checkout --detach origin/main
    flock ~/.local/state/ci-gate.lock ./ci.py run --remote prod
    git push prod HEAD:refs/heads/gate/$(git rev-parse --short HEAD)
    gh api repos/OWNER/REPO/commits/$(git rev-parse HEAD)/status --jq .state   # success
    git push prod HEAD:main

`attest` runs in the background after the first push, so repeat the status check until
it reports success.

## Copying this gate to another repository

`ci.py`, `ci/gate.py`, `ci/boundaries.py`, `ci/__init__.py`, `ci/hooks/git/pre-push`,
this file and `tests/test_ci_gate.py` are generic: copy them unchanged, so that a later
diff between repositories shows only real changes. Only `ci.toml` is per repository.

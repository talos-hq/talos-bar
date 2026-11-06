#!/usr/bin/env bash
# The Linux checks that used to run on GitHub Actions. Windows and macOS are not
# covered here: run those on their own hosts.
# Usage: ci.sh [lane]. One lane of ci.toml, or every lane with no argument.
set -euo pipefail
cd /repo

crate() {
  echo "== $1"
  export CARGO_TARGET_DIR="/build/target/$1"
  (cd "$1" && soldr cargo build --locked && soldr cargo test --locked)
}

clippy() {
  (cd "$1" && CARGO_TARGET_DIR="/build/target/$1" soldr cargo clippy --locked --all-targets -- -D warnings)
}

build() {
  (cd "$1" && CARGO_TARGET_DIR="/build/target/$1" soldr cargo build --locked)
}

lane_gate() {
  echo "== gate"
  # `python -m` puts /repo on sys.path, where `ci` lives. --isolated: these files are
  # copied unchanged from another repository and are checked by that repository's style.
  uv run --locked python -m pytest -q tests/test_ci_gate.py
  uv run --locked ruff check --isolated --line-length 100 ci ci.py tests/test_ci_gate.py
  uv run --locked ruff format --isolated --line-length 100 --check ci ci.py tests/test_ci_gate.py
}

lane_searchd() {
  crate searchd
}

lane_popover() {
  crate popover
  clippy popover
}

lane_bar() {
  crate bar
  clippy bar
}

lane_python() {
  echo "== python"
  uv run --locked pytest -q tests --ignore=tests/test_ci_gate.py
  uv run --locked ruff check src tests
  uv run --locked ruff format --check src tests
  uv run --locked basedpyright
}

lane_rendering() {
  echo "== rendering"
  build searchd
  build popover
  build bar
  # Mesa lavapipe only: wgpu's GL backend on NVIDIA's EGL fails with "Parent device is lost".
  export WGPU_BACKEND=vulkan
  VK_ICD_FILENAMES=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json | head -n1)
  export VK_ICD_FILENAMES
  out=$(mktemp -d)
  chmod 700 "$out"
  /build/target/searchd/debug/talos-searchd --no-hotkey --endpoint "$out/sock" &
  daemon=$!
  trap 'kill "$daemon" 2>/dev/null || true' EXIT
  for _ in $(seq 100); do test -S "$out/sock" && break; sleep 0.1; done
  test -S "$out/sock" || { echo "talos-searchd did not bind within 10s"; exit 1; }
  unset WAYLAND_DISPLAY

  xvfb-run -a /build/target/popover/debug/talos-popover --oneshot --query a --screenshot "$out/popover.png" --endpoint "$out/sock"
  test "$(stat -c %s "$out/popover.png")" -gt 5000
  xvfb-run -a /build/target/bar/debug/talos-bar-rs --screenshot "$out/bar.png" --endpoint "$out/sock"
  test "$(stat -c %s "$out/bar.png")" -gt 3000
}

if [ $# -gt 0 ]; then
  "lane_$1"
else
  for lane in gate searchd popover bar python rendering; do "lane_$lane"; done
fi
echo "bosn ci: PASS"

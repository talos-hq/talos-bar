#!/usr/bin/env bash
# The Linux checks that used to run on GitHub Actions. Windows and macOS are not
# covered here: run those on their own hosts.
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

crate searchd
crate popover
clippy popover
crate bar
clippy bar

echo "== python"
uv run --locked pytest -q tests
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked basedpyright

echo "== rendering"
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
echo "bosn ci: PASS"

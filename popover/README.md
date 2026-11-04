# talos-popover

The Super+Space search popover drawn with `kernal_api::gfx` (`gfx-egui`
feature) instead of Qt. Like `searchd/`, every OS touchpoint goes through
kernal-api; this crate names no GUI toolkit.

## What it renders

- A borderless, always-on-top 640x400 window, centred by default. `--at X,Y`
  asks for `Placement::At`; kernal-api rates absolute placement `BestEffort`,
  so the popover says so on stderr and the compositor may ignore it (Wayland
  does). Window-level requests also follow `gfx::capabilities()`.
- A query field (focused on open), a scrolling results list made of
  `Frame::selectable_row`s inside `Frame::scroll`/`Frame::list`, and
  "Type to search" / "No results" states. Daemon errors are shown under the list.
- Up/Down move the selection, Enter or a click launches, Esc and focus loss close.

## Modes

    talos-popover [--endpoint E] [--at X,Y] [--oneshot] [--query TEXT] [--screenshot PNG]

- **Resident (default).** No window at start. A background thread sends
  `{"op":"subscribe"}` to `talos-searchd` and waits for `{"event":"show"}`
  (searchd owns the global hotkey; this process registers none). Each `show`
  opens the popover focused with an empty query; Esc, focus loss or a launch
  closes the window and the process goes back to waiting. Presses that arrive
  while it is open are dropped. If searchd is absent or restarts, the thread
  reconnects with backoff (250 ms doubling to 5 s) and says so once on stderr.
- **`--oneshot`.** Open one popover now and exit when it closes.
- **`--query TEXT`.** Prefill the field and search immediately.
- **`--screenshot PNG`.** Render the popover for `--query` without opening a
  window (`kernal_api::gfx::snapshot`, an offscreen render, not a compositor
  tool), write a PNG, print its path and exit 0; exit 1 on failure. With no
  window and no compositor, it works on a locked Wayland session, under Xvfb,
  and on a display-less CI host with Mesa's software Vulkan:

      xvfb-run -a talos-popover --oneshot --query fire --screenshot pop.png --endpoint "$dir/sock"

#### Offscreen rendering: pin the adapter

`--screenshot` goes through `gfx::snapshot`, which renders on whatever adapter
wgpu picks. On a host whose GL comes from NVIDIA's EGL (any NVIDIA box where
the binary cannot load `libvulkan.so.1`, which includes NixOS outside a dev
shell), it fails with `Parent device is lost`. Pin Mesa's software Vulkan
(lavapipe) instead, as CI does:

    # Debian/Ubuntu (mesa-vulkan-drivers, libvulkan1)
    WGPU_BACKEND=vulkan VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json xvfb-run -a talos-popover --oneshot --query fire --screenshot out.png ...
    # NixOS
    LD_LIBRARY_PATH=/run/current-system/sw/share/nix-ld/lib WGPU_BACKEND=vulkan \
      VK_ICD_FILENAMES=/run/opengl-driver/share/vulkan/icd.d/lvp_icd.x86_64.json talos-popover --oneshot --query fire --screenshot out.png ...

The experiments behind this are in kernal-api's `docs/gfx-backend.md`
("Which adapter a snapshot uses").

## Data source

Real IPC: `talos-searchd`'s line-JSON protocol (`query`, `activate`,
`subscribe`) on its default per-user endpoint, or `--endpoint E`. Launching is
the daemon's `activate`; there is no second launcher here. If the daemon is not
running the popover shows the connection error rather than empty results.

## The endpoint directory must be yours and 0700

kernal-api IPC binds a Unix socket only inside a directory that is owned by you
and has mode exactly `0700`; otherwise talos-searchd fails to start with
`IPC endpoint parent is not owned by the current user` or
`IPC endpoint parent is not owner-private`. So `--endpoint /tmp/sock`
(`/tmp` is root's, `1777`) and `--endpoint ~/.cache/sock` (usually `0755`)
are both refused. Use the default (`$XDG_RUNTIME_DIR/talos-searchd.sock`;
that directory is already `0700`) or a dedicated directory:

    dir=/tmp/talos-$UID; mkdir -p -m 700 "$dir"; chmod 700 "$dir"
    talos-searchd --no-hotkey --endpoint "$dir/sock" &
    talos-popover --endpoint "$dir/sock"

A missing directory is created `0700` for you; an existing one is never chmod-ed.

## Wayland note

On Wayland a window gets frames only while the compositor paints it. On a
locked KDE session the popover window drew one frame and then nothing, because
KWin sends no `wl_surface.frame` callbacks under the lock screen (see
forge-ci/kernal-api#561). That is why `--screenshot` renders offscreen. Resident
mode is unaffected while the session is unlocked: the popover only matters when
it can be seen.

## What it does not do

- No icons, no dictation button, no go/ link styling.
- It does not replace anything: the Python/QML bar and its Qt popup are
  untouched, and `pyproject.toml`/`flake.nix` do not build this. CI's
  `popover` lane builds, tests, clippies and screenshots it.
- Resident mode re-creates the window per show (one `gfx::run` per show)
  rather than hiding it; the facade has no show/hide window command yet.

## Build and test

    soldr cargo build && soldr cargo test && soldr cargo clippy --all-targets -- -D warnings

The tests drive the IPC client against a peer on a real kernal-api endpoint
and the selection/close logic without a window.

kernal-api is pinned by `rev` to the `feat/gfx-egui` commit that adds
the screenshot capability; move it to a `main` rev once that branch merges.

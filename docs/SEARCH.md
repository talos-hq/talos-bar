# Search bar + talos-vox

Super+Space opens a talos-bar search popup you can type or talk into. It replaces the KRunner
binding (and the go-runner plugin) on NixOS, and must also work on Windows and macOS.

## Components

| Piece | Language | Built on | Role |
|---|---|---|---|
| `talos-searchd` | Rust | kernal-api | Daemon: global hotkey, app catalog, go/* links, launch/open, IPC server |
| `talos-vox` | Rust | kernal-api | Speech-to-text library + CLI; fork of peteonrails/voxtype (MIT), re-platformed |
| talos-bar search popup | QML/Python | Qt (optional component, not on kernal-api yet) | UI client of `talos-searchd` over IPC |

## talos-vox

Upstream voxtype is already Rust (tokio, cpal, whisper-rs/ONNX), but its OS layer is Linux-first:
evdev/compositor hotkeys, cpal audio, ydotool/wtype output. talos-vox keeps the transcription
engine and moves **every** OS touchpoint behind kernal-api so one crate builds on Linux, Windows
and macOS:

| voxtype today | talos-vox |
|---|---|
| cpal capture | kernal-api microphone capture (forge-ci/kernal-api#534) |
| evdev / compositor hotkey | kernal-api global hotkey (#533) |
| ydotool / wtype typing | not needed: text returns to the caller over IPC |
| config/cache dirs via `directories` | kernal-api `fs` paths |
| daemon/socket plumbing | kernal-api `ipc-async` + `daemon-*` |

Shape: a `talos-vox` library (`Transcriber`, streaming `listen()` yielding partial/final text)
plus a thin CLI. `talos-searchd` links the library directly, with no subprocess. Until a
kernal-api gap lands, an interim shim lives behind a `shim-<os>` feature and is deleted when the
kernal-api feature ships. All builds go through `soldr`.

## Flow

1. Super+Space → `talos-searchd` (hotkey) → IPC `show` → popup.
2. Typing → IPC `query` → ranked results (apps, go/* links, files).
3. Holding the mic key → `talos-vox` streams partials into the query box.
4. Enter → IPC `activate` → kernal-api launch/open (#535).

## Install (NixOS)

Flake package for `talos-searchd`, a systemd user unit, and plasma-manager drops KRunner's
Meta+Space and go-runner. The voxtype-popups wrapper retires once talos-vox replaces it.

## CI

Following forge-ci/ci.yml conventions, Linux, Windows and macOS jobs build `talos-searchd` and
`talos-vox` with soldr, run IPC round-trip tests (`query`/`activate` against a fixture catalog),
and transcribe a bundled WAV through talos-vox (file input, since runners have no microphone).

## kernal-api gaps

#533 global hotkey · #534 microphone capture · #535 app catalog / launch / open · #536 Python
bindings (would let talos-bar reach kernal-api without the daemon).

# Jettison: disk pressure in the bar

`glue/builtin/disk.py` + `talos_bar/diskview.py`.

## What the user sees

A **Disk** tab exists in the bar's item list but is hidden while the root
filesystem has room. It appears when usage crosses `TALOS_BAR_DISK_THRESHOLD`
(default **0.90**), showing the used percentage, the free space as subtext, a
`drive-harddisk` icon and a status dot (amber at 90–95%, red at 95%+). It hides
itself again on the next refresh once space is back under the threshold — the
bar carries a warning only when there is one.

Clicking it opens the **tree view**: one read-only HTML page, starting at `/`,
listing directories by allocated size with a size bar per row. It is served on
loopback (`http://127.0.0.1:<port>/`) and opened with the session's default
browser.

## Why a page, and why not an embedded view

The bar is a Qt Quick scene. Rendering a tree inside it would mean either a
QML tree model with its own layout code, or a `WebEngineView` — a second browser
runtime in the Nix package, just to draw a list. A loopback page:

- opens in the browser the session already has, with real scrolling, find, zoom
  and history;
- survives a bar restart or a glue hot-reload;
- can be reloaded while a long scan settles.

Nothing on the page deletes anything. A size list is evidence; turning evidence
into an action needs its own confirmation path (see "Cleanup" below).

## Hiding an item

The glue API's `Output` is `str | Rich | None`. `None` now means **hide**: the
bar records `hidden` in the item view and `Tab.qml` sets `visible: !model.hidden`,
so the `Row` collapses it to zero width. Before this change `None` was coerced
to `""` and drew an empty tab.

## Ignoring mounted drives

Every scan passes `--one-filesystem`. The walk stays on the root's own
filesystem, so a separate `/home` disk, a network share or a FUSE mount is
neither counted against `/` nor traversed — which is what "ignore mounted
drives" has to mean for the numbers to describe one disk.

## Cost, measured on this machine

`diskview.scan("/")`, depth 6, minimum 8 MiB, `--one-filesystem`:

| | |
|---|---:|
| entries seen | 16,295,566 |
| wall clock (cold) | 69.7 s |
| allocated | 1.1 TiB |
| unreadable | 98 |
| page | 436 KiB, 4 top-level rows |
| top rows | `/home` 1.0 TiB, `/tmp` 87.7 GiB, `/var` 8.3 GiB |

The scan runs on the bar's thread pool, so the UI stays live; the browser opens
as soon as the page is ready.

## Requires the scanner

`jettison-cli` must be on `PATH` (the `jettison` wheel bundles it), or
`TALOS_BAR_JETTISON=/path/to/jettison-cli` must point at one. Without either the
click raises `ScannerMissing` with that instruction and the bar prints the
traceback — it does not open a blank page.

## Not done yet

- **The "more" arrow.** Items that do not fit, plus optional features like this
  one, belong behind an overflow control that folds a second row out below the
  main line. That is bar-core work (measure `row.implicitWidth` against
  `screen.width`, move the tail into a second `Row` inside the same panel, and
  keep the panel's height animating with it), not a glue module. Until it
  exists the Disk tab simply occupies its slot when visible.
- **Cleanup.** The click-through is analysis only. Deleting needs a
  server-side action with an explicit confirmation per path, a dry-run mode and
  a trash-first policy; the bar should never delete on a single click.

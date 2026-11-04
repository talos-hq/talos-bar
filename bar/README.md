# talos-bar-rs

talos-bar's top bar drawn with `kernal_api::gfx` (`gfx-egui` feature) instead
of Qt. It is the first bar surface to leave Qt. This crate names no GUI toolkit:
everything goes through kernal-api's facade, the same as `popover/` and `searchd/`.

## What v1 draws

One 36 px dock across the top edge (`Placement::Dock { edge: Edge::Top, thickness: 36 }`).
On Wayland this is a layer-shell surface with an exclusive zone, so the compositor
keeps maximised windows below it. Left to right:

- **Left:** `talos` and a **Search** button. A click sends `{"op":"show"}` to
  talos-searchd. searchd then sends subscribers the same `{"event":"show"}` that
  its Meta+Space hotkey does, so the resident `talos-popover` opens just as it
  does for the hotkey. The bar never starts a popover itself.
- **Centre:** the clock. First local time and date (`h:MM AM  mm/dd/yy`, as the
  Qt bar shows them), then one `NYC 2:30a` cell per configured zone. A cell
  gets the weekday (`Mon`) when that zone's date differs from local. Labels are
  short by default (`NYC CHI DEN SYD LUX MSK TYO UTC`, compact `2:30a` times)
  so all nine cells fit at 1280 px beside the Tasks button. `[clock] short =
  false` brings back city names and `2:30 AM`, and `[clock] labels =
  { "Asia/Tokyo" = "Tokyo" }` sets any zone's label. A zone without a built-in
  short label shows its city. All cells
  come from one instant. The local zone is not repeated. Cells are measured
  with `Frame::text_width` and laid out against `Frame::available_width` (the
  width the compositor gave the dock). Zones that do not fit are dropped from
  the end; the bar logs `layout width N px, local clock + n of m zones shown`
  once at start, then a `WARNING:` line naming every zone it dropped. A test
  (`default_config_at_default_width_shows_every_zone`) lays out the default
  config at 1280 px with the real font, in the widest state (every zone on
  another day, `Tasks 99 •`, `no popover`), and fails if any zone is dropped.
  It currently needs 1236 of 1264 px.
- **Left, third:** a **Tasks N** button: the open count from `tasks_cli.py
  list` (a `•` while edits wait for the next sync, the Qt item's amber dot), refreshed every
  `[panels] refresh_secs`. A click opens the tasks panel.
- **The local clock is a button:** a click opens the agenda panel.
- **Right:** status, from `{"op":"status"}` every 2 s. It shows
  `popover subscribed` / `no popover` (the count of searchd's open
  `subscribe` streams) or `searchd down`.

If the session cannot dock, the bar prints the reason and exits 1. Reasons are:
no Wayland, a compositor without `zwlr_layer_shell_v1` (GNOME), or macOS/Windows,
where kernal-api docks are not wired yet. It never falls back to a floating window.

## Panels

Drop-downs are kernal-api panels (`Frame::show_panel` + `App::panel`): a
second layer-shell surface hung from the top edge at the clicked cell's x,
kept below the bar by the compositor, focused on open. Esc, a click on the
same cell, or a click into any other window closes it.

- **Tasks** (`TASKS.md`, `synced` / `saved, syncs at 0/6/12/18h`, then the
  file's top-level items marked `•`, `[ ]`, `[x]`), from `tasks_cli.py list`,
  re-run on every open. The Qt glue's tooltip uses ☐/☑; the default font has
  no ballot boxes, so the panel writes them the way TASKS.md does.
  **A click on a `[ ]` / `[x]` row (or Enter on the focused row) toggles it.**
  This saves the way the Qt tasks editor does: `tasks_cli.py read`, flip that
  one line, then `tasks_cli.py save64 <whole file>`, which commits locally;
  the sync timer pushes. Then it `read`s again to prove the edit landed,
  since `save64` exits 0 even when its git commit fails. The matching
  (`panel::toggle_line`) is pure and unit-tested. It flips the single
  checkbox line indented at most 3 spaces whose text and state match the row
  shown. If the file changed, or the text appears twice at any depth, nothing
  is written and the panel says so. Plain `•` bullets are inert. While it
  saves, the sync line reads `saving…`, then `saved: marked done` / `saved:
  reopened`, or `could not toggle: <the CLI's error>`. The notice takes the
  sync line's slot, so rows do not move under the pointer. Either way the
  list reloads from the file. A long error can still wrap to two lines.
- **Agenda** (`Today's meetings`, the date, the next-up block
  `10:00 AM · in 7h 22m` / `Next up: …` / its time range, then Tomorrow /
  today's rows with `NOW · ` on the one in progress / `Done · N`), from
  `calendar_agenda.py` (which parses the iCal file with `calendar_ical.py`),
  re-run on every open, at most `[panels] agenda_rows` rows. Wording follows
  `glue/builtin/Agenda.qml`.
  **Actions, as Agenda.qml and `agenda.py` do them** (`src/launch.rs`):
  - *New Zoom Meeting* opens `https://zoom.us/start/videomeeting`.
  - *Open calendar* runs `kioclient exec org.kde.korganizer.desktop`. If
    `kioclient` is not on `PATH` it runs `xdg-open https://calendar.google.com`
    (the glue's `open_calendar`).
  - *Join Zoom meeting now* appears under the next-up block for a Zoom meeting
    that is live or starts within 5 minutes (`joinLeadSeconds`). It opens
    `join_url` (the `zoommtg://` app link) or `url`. As in the glue, the app
    link is used only when `xdg-mime query default x-scheme-handler/zoommtg`
    names a handler; with no `xdg-mime` the bar can't tell and keeps the app
    link.
  - *A click on a meeting row* pins it and shows its details card below the
    list, so the rows never move under the pointer. The card holds the title,
    the time range, the location (unless it is a Zoom meeting), a *Join Zoom
    meeting* button, contact buttons (`mailto:` / `tel:` with only the
    digits and `+`) and the description. This is `fullDetails(row)`. The QML
    draws this card as a side panel and also shows it on hover; this bar
    shows it on click only.

  URLs open with `xdg-open <url>`, which is what `Qt.openUrlExternally` runs
  on Linux. On Plasma, `xdg-open` hands the URL to KIO. Every launch closes
  the panel, like the QML's `bar.close()`. Children start detached, in their
  own process group. The argv building is pure and unit-tested. Launching
  goes through `launch::Spawn`, which the tests replace with a recorder, so
  `cargo test` never starts a browser or KOrganizer.

Both scripts run as subprocesses with the Qt glue's contract (same JSON,
same 25 s timeout, `TALOS_BAR_TASKS_CMD` / `TALOS_BAR_AGENDA_CMD`
overrides), from `TALOS_BAR_SCRIPTS` (default: this checkout's
`src/talos_bar/scripts`) with `TALOS_BAR_PYTHON` (default `uv run
--no-project --with icalendar --with markdown-it-py python`). They run on a
worker thread; the panel says `loading…` until the result lands. Parsing and
wording (`src/panel.rs`) are unit-tested on the scripts' JSON shapes;
`src/feeds.rs` tests the subprocess runner with `sh`.

## What it deliberately does not do (yet)

See `ROLLOUT.md` for the full parity table. In short: tray, plugins/glue
items, whole-file task editing, or a bottom or side edge.

## Right-click screen menu

A right-click anywhere on the bar opens *Show bar on*: one row per output
from `Frame::monitors()`, the current one ticked and highlighted. Picking one
saves it where the Qt bar saves its choice, in the same format
(`$XDG_STATE_HOME/talos-bar/screen`, else `~/.local/state/talos-bar/screen`,
holding the name and a newline), so switching between the bars keeps it, and
moves the running bar there (`Frame::set_monitor`). While the wanted output is
gone the bar waits on another one and goes back once it reappears, like the
Qt bar. `[bar] screen` in the config still wins at start-up. Proven under
nested KWin with two outputs and real injected clicks: the menu opened, the
`Virtual-1` row moved the bar (`MaximizeArea` went from
`Virtual-0 0,36 1280x764` to `Virtual-1 1280,36 1280x764`), the file read
`Virtual-1`, and the same menu on `Virtual-1` moved it back (file:
`Virtual-0`).

## Fit width, the agenda cell, editing tasks

- `[bar] width = "fit"` makes the bar only as wide as its content (measured
  with `text_width`), placed by `[bar] align = "left" | "center" | "right"`;
  `"full"` (default) spans the edge, and a number still means full with that
  snapshot width. The desktop reserves exactly the bar's 36 px either way.
  Under nested KWin, 1280x800: full `0,0 1280x36`, fit `2,0 1276x36`
  (with a meeting cell the content nearly fills 1280); 1920x800: fit
  centred at `311,0 1298x36`. `MaximizeArea` is `y=36 h=764` in every case.
- **Agenda cell:** the next meeting (`10:00 AM · Title`, `Now · Title` while
  one runs), right of the clock zones, refreshed every 60 s; a click opens
  the agenda panel. It is laid out from the width the zones leave, measured
  in the frame, and cut with `…`, so it can never drop a zone; a test
  asserts the zone count at 1280 px is the same with and without it.
- **Task editing:** the tasks panel has a field above the list. Type and
  press Enter (or *Add task*) to add (`tasks_cli.py add64`); *Edit…* makes
  rows pick a task, which fills the field, then *Save* rewrites its text and
  *Remove* deletes it, both as `read` → change that one line → `save64` →
  `read`, like the toggle, so a list that changed underneath is refused, not
  edited at a stale index. Every change goes through the same `tasks_cli`
  seam; a failure is shown in the panel's second line and the typed text is
  kept. Panels take typed text through the desktop's keyboard layout, and a
  held key repeats at the desktop's repeat delay and rate (nested KWin: 50/s
  after 250 ms).

## Screen, translucency, world clock

- `[bar] screen = "HDMI-A-1"` picks the output by connector name. Unset, the
  Qt bar's saved choice (`$XDG_STATE_HOME/talos-bar/screen`) and then
  `TALOS_BAR_SCREEN` apply, as in the Qt bar; nothing set is the desktop's
  primary output. A named output that is not connected falls back to the
  primary one and logs a WARNING; the log always names the output the bar
  landed on (`talos-bar-rs: on output "…"`).
- `[bar] opacity = 0.7` (default 1.0) makes the bar's and panels' fill
  translucent, the Qt bar's `panelOpacity`; text stays opaque. A `#rrggbbaa`
  `[theme]` colour works on its own too.
- Clicking the date opens the world-clock table (Clock.qml's rows: local time,
  then every zone with seconds, weekday when it differs, and "N hours later");
  the time still opens the agenda. A click is used because the facade has no
  per-widget hover, and the date is the natural "more about time" cell.

## Idle cost

The bar asks for its next frame at the next second boundary
(`Frame::request_repaint_after`), like the Qt bar's 1 s tick, instead of every
frame. Under nested `kwin_wayland --virtual` (1280x800), `/proc/<pid>/stat`
utime+stime over 10 s went from 988-993 ticks (~99 % of a core, repaint every
frame) to 3 ticks (~0.3 %).

Phase 7 (panels), same harness on 2026-11-02 with the host at load average
47-83: the pre-panel binary (95e48d4) and this one, alternated twice, each
measured 30 s after an 8 s settle with a live searchd + popover: old 22 / 22
ticks, new 23 / 21 ticks (~0.7 % of a core both; the 0.3 % above was on an
idler host). With the tasks or agenda panel open, 15 / 14 ticks per 20 s,
the same as closed: an open panel only draws on its own input. While a
script is still running the bar and panel poll every 150 ms for its result
(a second or so per open). The scripts themselves run as child processes
(`uv` + Python, ~0.4 s CPU per run) on open and every `refresh_secs` for the
tasks count; that CPU is the child's, not counted above.

## Relation to the Qt bar

It is a separate binary. The Python/QML bar is unchanged and stays the running
bar. Do not run both at the top edge, because both reserve 36 px. Both read the
same config file, and each ignores the other's tables.

## Config: `~/.config/talos-bar/config.toml`

(`$XDG_CONFIG_HOME/talos-bar/config.toml`; `--config PATH` overrides.) If the
file is missing, the defaults below apply. If it is invalid, the bar exits 1
with the parse error. Unknown zone names are skipped and reported once on stderr.

```toml
[clock]
# IANA names, after the local time. Default: the Qt clock's list
# (src/talos_bar/glue/builtin/clock.py).
zones = ["America/New_York", "America/Chicago", "America/Denver", "Australia/Sydney",
         "Europe/Luxembourg", "Europe/Moscow", "Asia/Tokyo", "UTC"]
seconds = false

[bar]
width = 1280   # logical px used to split left/centre/right

[panels]
agenda_rows = 12      # meeting rows in the agenda panel
refresh_secs = 300    # tasks cell re-runs tasks_cli this often without a click
max_height = 520      # tallest a panel grows before it scrolls

[theme]               # every key optional; no table = kernal-api's default theme
mode = "dark"         # "dark" | "light" | "system"
accent = "#3daee9"    # selection, the highlighted row, pressed buttons
background = "#202326" # the dock's fill
foreground = "#fcfcfc" # all text
panel = "#2a2e32"     # drop-down panels' fill (default: background)
```

Colours are `#rgb`, `#rrggbb` or `#rrggbbaa`. An unknown key in `[theme]`, a
bad colour or a bad mode makes the bar exit 1 and name the key. With
`mode = "system"`, the bar reads KDE's `kdeglobals`
(`$XDG_CONFIG_HOME/kdeglobals`): `[General] ColorScheme`, then `[KDE]
LookAndFeelPackage`. A name containing "dark" or "light" picks that mode.
Anything else stays dark, the Qt bar's own Breeze Dark. The keys map onto
kernal-api `Theme` tokens: `mode`, `accent`, `background`, and
`foreground`/`panel`, which were added to the facade for this bar.
`Theme::for_panel()` is what a panel surface draws with, and
`--screenshot --panel` uses it too.

The local zone is `TZ`, then the OS setting, then UTC.

## Usage

    talos-bar-rs [--endpoint E] [--config PATH] [--no-tray] [--screenshot PNG [--panel tasks|agenda|world]]

`--screenshot` renders the bar offscreen (`gfx::snapshot`) to a PNG and exits;
with `--panel` it runs that panel's script and renders the panel instead.
It needs no compositor, so it works in CI under Xvfb/Mesa and on a locked session.
The endpoint defaults to talos-searchd's (`$XDG_RUNTIME_DIR/talos-searchd.sock`).

The bar also puts an item in the desktop's status area (`kernal_api::system_tray`,
StatusNotifierItem on KDE): left click searches, middle click toggles the Tasks panel, and
its menu has "Search…", "Tasks panel" and "Quit bar". `--no-tray` leaves it out; without a
status-area host the bar logs that and runs without it. See `src/tray.rs`.

### Offscreen rendering: pin the adapter

`--screenshot` goes through `gfx::snapshot`, which renders on whatever adapter
wgpu picks. On a host whose GL comes from NVIDIA's EGL (any NVIDIA box where
the binary cannot load `libvulkan.so.1`, which includes NixOS outside a dev
shell), it fails with `Parent device is lost`. Pin Mesa's software Vulkan
(lavapipe) instead, as CI does:

    # Debian/Ubuntu (mesa-vulkan-drivers, libvulkan1)
    WGPU_BACKEND=vulkan VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json xvfb-run -a talos-bar-rs --screenshot out.png ...
    # NixOS
    LD_LIBRARY_PATH=/run/current-system/sw/share/nix-ld/lib WGPU_BACKEND=vulkan \
      VK_ICD_FILENAMES=/run/opengl-driver/share/vulkan/icd.d/lvp_icd.x86_64.json talos-bar-rs --screenshot out.png ...

The experiments behind this are in kernal-api's `docs/gfx-backend.md`
("Which adapter a snapshot uses").

## Tests

`cargo test` covers the clock (cities, one instant across zones, DST, day labels,
unknown zones), the layout split, the config parsing, searchd reply parsing,
the panels' parsing and wording, panel placement / toggling / Esc handling, the task toggle (line matching, base64, the read → save64 → read flow against a fake CLI, failures surfacing in the panel), every default zone fitting at 1280 px, and
the script runner, all without a compositor or the real scripts. The windowed proof (dock, exclusive zone, a real click
opening the popover) runs under a nested `kwin_wayland --virtual`. CI cannot run that.

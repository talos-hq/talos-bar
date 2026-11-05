//! The bar's per-frame UI: [talos | search | tasks]  ·  [local clock date · zones]  ·  [status],
//! plus the drop-down panel under the tasks cell or the clock.

use crate::clock::{self, Cell};
use crate::config::Config;
use crate::disk::{self, Pressure, Scanner};
use crate::diskview;
use crate::feeds::{Cli, Feed, Job};
use crate::layout;
use crate::launch::Launcher;
use crate::panel::{self, Act, Agenda, Change, Editor, Line, Meeting, TaskLine, TaskList};
use crate::screen::{self, Output};
use crate::searchd::Status;
use chrono::{DateTime, Utc};
use chrono_tz::Tz;
use kernal_api::gfx::{App, DockLength, Edge, Event, Frame, Key, Monitor, PanelOptions, PointerButton, Theme};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const SEARCH: &str = "Search";

/// The gfx frame's panel margin (8 px), between the surface edge
/// and the first widget.
const MARGIN: f32 = 8.0;
/// A button's horizontal padding on both sides together.
const BUTTON_PAD: f32 = 8.0;
pub const TASKS_WIDTH: f32 = 420.0;
pub const AGENDA_WIDTH: f32 = 460.0;
pub const WORLD_WIDTH: f32 = 440.0;
pub const SCREENS_WIDTH: f32 = 300.0;
pub const DISK_WIDTH: f32 = 560.0;
/// The widest the agenda cell grows; a long title is cut with `…` before this.
pub const AGENDA_CELL_MAX: f32 = 300.0;
/// Kept free before the status label (logical px), so rounding never clips it.
const AGENDA_SLACK: f32 = 12.0;
/// How often the agenda cell re-runs the calendar script without a click (the Qt item: 30 s).
const AGENDA_REFRESH: Duration = Duration::from_secs(60);
/// How often an open but still-loading panel checks for the script's result.
const LOADING_POLL: Duration = Duration::from_millis(150);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Tasks,
    Agenda,
    /// The world-clock table, under the date.
    World,
    /// The right-click menu: which output the bar is on.
    Screens,
    /// Where the space went, when the disk is nearly full.
    Disk,
}

impl Kind {
    pub fn width(self) -> f32 {
        match self {
            Self::Tasks => TASKS_WIDTH,
            Self::Agenda => AGENDA_WIDTH,
            Self::World => WORLD_WIDTH,
            Self::Screens => SCREENS_WIDTH,
            Self::Disk => DISK_WIDTH,
        }
    }
}

/// The two scripts' feeds, injected so tests and snapshots can run without the scripts.
pub struct Feeds {
    pub tasks: Feed<TaskList>,
    pub agenda: Feed<Agenda>,
    /// `tasks_cli.py` itself, for edits (`read`, `save64`).
    pub tasks_cli: Cli,
    /// Opens meeting links and the calendar (see `launch`).
    pub launch: Launcher,
}

/// One change to TASKS.md from the tasks panel.
#[derive(Clone, Debug, PartialEq)]
pub enum Edit {
    Toggle(TaskLine),
    /// Rewrite a task's text.
    Retext(TaskLine, String),
    Remove(TaskLine),
    /// A new open task (`tasks_cli add64`).
    Add(String),
    /// The whole file for the editor (`tasks_cli read`).
    LoadFile,
    /// Replace the whole file with the editor's text (`tasks_cli save64`), as Tasks.qml saves.
    SaveFile(String),
}

impl Edit {
    /// The verb the panel's failure notice uses.
    fn verb(&self) -> &'static str {
        match self {
            Self::Toggle(_) => "toggle",
            Self::Retext(..) => "save",
            Self::Remove(_) => "remove",
            Self::Add(_) => "add",
            Self::LoadFile => "load",
            Self::SaveFile(_) => "save the file",
        }
    }
}

/// Toggle `item` the way the Qt tasks editor saves: `read` the file, flip the one line
/// (`panel::toggle_line`), `save64` the whole file, then `read` again to prove it landed
/// (`save64` exits 0 even when its git commit fails, so its exit code alone proves nothing).
#[cfg(test)]
pub fn toggle_task(cli: &Cli, item: &TaskLine) -> Result<String, String> {
    edit_task(cli, &Edit::Toggle(item.clone()))
}

/// Make `edit` through `tasks_cli.py`, the one seam every task change goes through, and prove
/// it landed with a second `read`. A rewrite or removal is `read` → change the one matching line
/// ([`panel::change_line`]) → `save64`, like a toggle, so a list that changed under the panel
/// is refused rather than edited at a stale index (`tasks_cli remove INDEX` counts every
/// checkbox, nested ones too, which the panel does not show). A new task is `add64`, which
/// places it after the last task as the Qt bar's add does.
pub fn edit_task(cli: &Cli, edit: &Edit) -> Result<String, String> {
    let read = |cli: &Cli| cli(&["read"]).map(|raw| raw.strip_suffix('\n').unwrap_or(&raw).to_owned());
    if *edit == Edit::LoadFile {
        // Tasks.qml shows `read` without its trailing newline.
        return read(cli);
    }
    if let Edit::SaveFile(text) = edit {
        cli(&["save64", &panel::base64(text.as_bytes())])?;
        if read(cli)?.trim_end_matches('\n') != text.trim_end_matches('\n') {
            return Err("tasks_cli save64 returned but TASKS.md does not have the edit".to_owned());
        }
        return Ok("saved".to_owned());
    }
    if let Edit::Add(text) = edit {
        let text = panel::one_line(text).ok_or("type the task first")?;
        let line = format!("- [ ] {text}");
        let count = |file: &str| file.lines().filter(|l| l.trim_end() == line).count();
        let before = count(&read(cli)?);
        cli(&["add64", &panel::base64(text.as_bytes())])?;
        if count(&read(cli)?) != before + 1 {
            return Err("tasks_cli add64 returned but TASKS.md does not have the new task".to_owned());
        }
        return Ok("saved: added".to_owned());
    }
    let (item, change, done) = match edit {
        Edit::Toggle(item) => {
            let done = if item.check == Some(true) { "saved: reopened" } else { "saved: marked done" };
            (item, Change::Toggle, done)
        }
        Edit::Retext(item, text) => (item, Change::Retext(text.clone()), "saved: edited"),
        Edit::Remove(item) => (item, Change::Remove, "saved: removed"),
        Edit::Add(_) | Edit::LoadFile | Edit::SaveFile(_) => unreachable!("handled above"),
    };
    if matches!(change, Change::Toggle) && item.check.is_none() {
        return Err(format!("\"{}\" is not a checkbox", item.text));
    }
    let before = read(cli)?;
    let after = panel::change_line(&before, item, &change)?;
    cli(&["save64", &panel::base64(after.as_bytes())])?;
    let landed = read(cli)?;
    if landed.trim_end_matches('\n') != after.trim_end_matches('\n') {
        return Err("tasks_cli save64 returned but TASKS.md does not have the edit".to_owned());
    }
    // Short, so it fits the one line it replaces; the log names the task.
    Ok(done.to_owned())
}

pub struct Bar {
    pub config: Config,
    pub local: Tz,
    pub status: Arc<Mutex<Status>>,
    pub text_size: f32,
    /// The theme's item spacing between labels in a row.
    pub spacing: f32,
    /// Called on a search click (sends `show` to searchd off the UI thread).
    pub on_search: Box<dyn FnMut()>,
    pub feeds: Feeds,
    /// The open panel and its left edge (logical px from the screen's left).
    pub open: Option<(Kind, f32)>,
    /// The agenda row whose details card shows (Agenda.qml's `pinnedRow`).
    pub selected: Option<Meeting>,
    /// A clock override (tests); `None` is the wall clock.
    pub now: Option<DateTime<Utc>>,
    /// What the last frame laid out: (zones shown, zones configured and valid).
    pub laid_out: Option<(usize, usize)>,
    /// The agenda cell's text as last drawn (after truncation), if shown.
    pub agenda_shown: Option<String>,
    /// `[bar] width = "fit"`: the dock length last asked for.
    fit_asked: Option<f32>,
    /// The widest surface seen: the screen's edge, since a fit bar starts full.
    edge_seen: f32,
    /// The tasks panel's editor mode and its text field.
    pub editor: Editor,
    pub draft: String,
    /// The whole-file editor's text as last loaded or saved, to tell an unsaved edit.
    file_base: Option<String>,
    /// The disk panel's scan, kept across frames so reopening does not rescan.
    pub disk: Scanner,
    /// The last `statvfs` reading (one syscall, retaken every frame).
    pub pressure: Option<Pressure>,
    /// Actions from the desktop's tray item (`tray`), if one registered.
    pub tray: Option<std::sync::mpsc::Receiver<crate::tray::Act>>,
    /// The running task edit (and its verb), and the line the tasks panel shows about the last one.
    edit: Job,
    edit_verb: &'static str,
    notice: Option<String>,
    warned: bool,
    reported: bool,
    panel_error: bool,
    /// The output the bar should be on (config, the saved choice, then `TALOS_BAR_SCREEN`;
    /// a pick in the screen menu replaces it); `None` is the desktop's primary.
    pub wanted: Option<String>,
    /// The outputs as last listed by the dock (`Frame::monitors`).
    pub outputs: Vec<Output>,
    /// A pick from the screen menu, applied (saved and moved) on the next dock frame.
    picked: Option<String>,
    /// A move asked for and not yet reported done (`Event::Monitor`).
    moving: Option<String>,
}

/// The x of a right-click released on the bar this frame (the Qt bar's right-button
/// `TapHandler` on the whole bar), if any.
pub fn right_click(events: &[Event]) -> Option<f32> {
    events.iter().rev().find_map(|event| match event {
        Event::PointerButton { button: PointerButton::Secondary, pressed: false, x, .. } => Some(*x),
        _ => None,
    })
}

/// Where a panel `width` wide opens under a cell starting at `x`, kept on a `screen`-wide edge.
pub fn panel_offset(x: f32, width: f32, screen: f32) -> f32 {
    x.min(screen - width).max(0.0)
}

/// The next panel state after a click on `kind`'s cell at `x`: a second click closes it.
pub fn toggle(open: Option<(Kind, f32)>, kind: Kind, x: f32) -> Option<(Kind, f32)> {
    match open {
        Some((current, _)) if current == kind => None,
        _ => Some((kind, x)),
    }
}

/// Whether `events` close the panel: Esc pressed in it, or the backend dismissing it.
pub fn closes(events: &[Event]) -> bool {
    events.iter().any(|event| {
        matches!(event, Event::PanelDismissed | Event::Key { key: Key::Escape, pressed: true, .. })
    })
}

/// The disk panel: totals, then one row per directory, indented by depth.
fn disk_lines(
    pressure: Option<Pressure>,
    rows: &[disk::Row],
    running: bool,
    error: Option<&str>,
) -> Vec<Line> {
    let mut lines = vec![Line::Heading("Disk".to_owned())];
    if let Some(pressure) = pressure {
        lines.push(Line::Text(format!(
            "{} used of {} · {} free",
            disk::human(pressure.used()),
            disk::human(pressure.total),
            disk::human(pressure.free),
        )));
    }
    if let Some(error) = error {
        lines.push(Line::Text(error.to_owned()));
        return lines;
    }
    if rows.is_empty() {
        lines.push(Line::Text(if running {
            "scanning…".to_owned()
        } else {
            format!("nothing above {}", disk::human(disk::MIN_BYTES))
        }));
        return lines;
    }
    for row in rows {
        let indent = "  ".repeat(row.depth.saturating_sub(1));
        lines.push(Line::Pair(format!("{indent}{}", row.name), disk::human(row.bytes)));
    }
    if running {
        lines.push(Line::Separator);
        lines.push(Line::Text("rescanning…".to_owned()));
    }
    lines.push(Line::Separator);
    lines.push(Line::Text("read-only: nothing is deleted here".to_owned()));
    lines
}

impl Bar {
    pub fn new(
        config: Config,
        local: Tz,
        status: Arc<Mutex<Status>>,
        theme: Theme,
        on_search: Box<dyn FnMut()>,
        feeds: Feeds,
    ) -> Self {
        Self {
            config,
            local,
            status,
            text_size: theme.text_size,
            spacing: theme.spacing,
            on_search,
            feeds,
            open: None,
            selected: None,
            now: None,
            laid_out: None,
            agenda_shown: None,
            fit_asked: None,
            edge_seen: 0.0,
            editor: Editor::Off,
            draft: String::new(),
            file_base: None,
            tray: None,
            edit: Job::default(),
            edit_verb: "toggle",
            notice: None,
            warned: false,
            reported: false,
            panel_error: false,
            wanted: None,
            outputs: Vec::new(),
            picked: None,
            moving: None,
            disk: Scanner::default(),
            pressure: None,
        }
    }

    /// The open panel's lines and whether its script is still running.
    pub fn panel_lines(&self, kind: Kind) -> (Vec<Line>, bool) {
        match kind {
            Kind::Tasks => {
                let (value, loading) = self.feeds.tasks.get();
                let mut lines = match &value {
                    Some(Ok(list)) => panel::tasks_editor_lines(list, &self.editor),
                    Some(Err(err)) => panel::status_lines("TASKS.md", loading, Some(err)),
                    None => panel::status_lines("TASKS.md", loading, None),
                };
                // The notice takes the sync line's slot rather than adding one, so the rows do
                // not move under the pointer between a click and its result.
                let notice = if self.edit.running() { Some(if self.edit_verb == "load" { "loading…" } else { "saving…" }) } else { self.notice.as_deref() };
                if let (Some(notice), Some(slot)) = (notice, lines.get_mut(1)) {
                    *slot = Line::Text(notice.to_owned());
                }
                (lines, loading || self.edit.running())
            }
            Kind::Agenda => {
                let (value, loading) = self.feeds.agenda.get();
                let lines = match &value {
                    Some(Ok(agenda)) => {
                        panel::agenda_lines(agenda, self.config.panels.agenda_rows, self.epoch(), self.selected.as_ref())
                    }
                    Some(Err(err)) => panel::status_lines("Today's meetings", loading, Some(err)),
                    None => panel::status_lines("Today's meetings", loading, None),
                };
                (lines, loading)
            }
            Kind::Screens => (panel::screen_lines(&self.outputs), false),
            Kind::Disk => {
                // Opening the panel is the request to look: a stale scan is
                // replaced in the background, and what exists is drawn until
                // the new one lands.
                self.disk.refresh(&disk::root());
                let (rows, running, error) = self.disk.peek();
                (disk_lines(self.pressure, &rows, running, error.as_deref()), running)
            }
            Kind::World => {
                let table = clock::table(self.now.unwrap_or_else(Utc::now), self.local, &self.config.clock.zones);
                (panel::world_lines(&table), false)
            }
        }
    }

    /// The clock in epoch seconds (Join's lead time).
    fn epoch(&self) -> f64 {
        #[allow(clippy::cast_precision_loss, reason = "epoch seconds fit an f64 exactly")]
        let secs = self.now.unwrap_or_else(Utc::now).timestamp() as f64;
        secs
    }

    fn close(&mut self) {
        self.save_file_if_edited();
        self.open = None;
        self.selected = None;
        self.editor = Editor::Off;
    }

    /// Tasks.qml saves when its popup closes; so does the whole-file editor.
    fn save_file_if_edited(&mut self) {
        if self.editor == Editor::File(true) && self.file_base.as_deref() != Some(self.draft.as_str()) {
            if self.edit.running() {
                eprintln!("talos-bar-rs: WARNING: TASKS.md edit not saved: another save is still running");
            }
            self.start_edit(Edit::SaveFile(self.draft.clone()));
        }
    }

    /// Do what a click in a panel asked. Launches close the panel, as the QML's `bar.close()`.
    pub fn act(&mut self, act: Act) {
        let launched = match act {
            Act::Toggle(index) => return self.toggle(index),
            Act::Pick(index) => {
                if let Some(item) = self.top(index) {
                    self.editor = Editor::On(Some(index));
                    self.draft = item.text;
                }
                return;
            }
            Act::EditFile => {
                self.editor = Editor::File(false);
                self.draft.clear();
                self.file_base = None;
                return self.start_edit(Edit::LoadFile);
            }
            Act::SaveFile => {
                if self.editor == Editor::File(true) {
                    self.start_edit(Edit::SaveFile(self.draft.clone()));
                }
                return;
            }
            Act::EditMode(on) => {
                // Leaving the file editor saves it, as closing Tasks.qml does.
                self.save_file_if_edited();
                self.editor = if on { Editor::On(None) } else { Editor::Off };
                self.draft.clear();
                return;
            }
            Act::Cancel => {
                self.editor = Editor::On(None);
                self.draft.clear();
                return;
            }
            Act::Add => return self.start_edit(Edit::Add(self.draft.clone())),
            Act::Save | Act::Remove => {
                let Editor::On(Some(index)) = self.editor else { return };
                let Some(item) = self.top(index) else { return };
                let edit = if act == Act::Save { Edit::Retext(item, self.draft.clone()) } else { Edit::Remove(item) };
                return self.start_edit(edit);
            }
            Act::Select(row) => {
                self.selected = Some(*row);
                return;
            }
            Act::Join(row) => self.feeds.launch.join(&row),
            Act::Open(url) => self.feeds.launch.open(&url),
            Act::OpenCalendar => self.feeds.launch.calendar(),
            Act::Screen(name) => {
                self.picked = Some(name);
                self.close();
                return;
            }
        };
        eprintln!("talos-bar-rs: launch: {launched:?}");
        self.close();
    }

    /// The tasks panel's `top[index]`, as last listed.
    fn top(&self, index: usize) -> Option<TaskLine> {
        let Some(Ok(list)) = self.feeds.tasks.get().0 else { return None };
        list.top.get(index).cloned()
    }

    /// Start toggling the tasks panel's `top[index]`, unless an edit is already running.
    pub fn toggle(&mut self, index: usize) {
        if let Some(item) = self.top(index) {
            self.start_edit(Edit::Toggle(item));
        }
    }

    /// Run `edit` off the UI thread, unless one is already running.
    pub fn start_edit(&mut self, edit: Edit) {
        let cli = std::sync::Arc::clone(&self.feeds.tasks_cli);
        let verb = edit.verb();
        let work = move || {
            let result = edit_task(&cli, &edit);
            eprintln!("talos-bar-rs: task edit {edit:?}: {result:?}");
            result
        };
        if self.edit.start(work) {
            self.notice = None;
            self.edit_verb = verb;
        }
    }

    /// Land a finished edit: say what happened and reload the list either way, so the panel
    /// shows the file as it is, not as the click hoped.
    pub fn poll_edit(&mut self) {
        if let Some(result) = self.edit.take() {
            if self.edit_verb == "load" {
                match result {
                    Ok(text) if self.editor == Editor::File(false) => {
                        self.draft.clone_from(&text);
                        self.file_base = Some(text);
                        self.editor = Editor::File(true);
                        self.notice = Some("editing the whole file".to_owned());
                    }
                    Ok(_) => {}
                    Err(err) => {
                        self.editor = Editor::Off;
                        self.notice = Some(format!("could not load: {err}"));
                    }
                }
                return;
            }
            if let ("save the file", Ok(_), Editor::File(true)) = (self.edit_verb, &result, &self.editor) {
                self.file_base = Some(self.draft.clone());
            }
            let file = self.edit_verb == "save the file";
            self.notice = Some(match result {
                Ok(done) => {
                    // Landed: the field and the pick are spent (a toggle has neither).
                    if self.edit_verb != "toggle" && !file {
                        self.draft.clear();
                        if let Editor::On(Some(_)) = self.editor {
                            self.editor = Editor::On(None);
                        }
                    }
                    done
                }
                Err(err) => format!("could not {}: {err}", self.edit_verb),
            });
            self.feeds.tasks.refresh();
        }
    }

    /// Apply a screen-menu pick (save it where the Qt bar does, then move), or go back to the
    /// wanted output once it is connected again.
    fn place(&mut self, frame: &mut Frame<'_>) {
        if let Some(name) = self.picked.take() {
            match screen::save(&name) {
                Ok(path) => eprintln!("talos-bar-rs: screen {name:?} saved to {}", path.display()),
                Err(err) => eprintln!("talos-bar-rs: cannot save the screen choice: {err}"),
            }
            self.wanted = Some(name);
        }
        let Some(target) = screen::return_to(self.wanted.as_deref(), &self.outputs) else { return };
        if self.moving.as_deref() == Some(target) {
            return;
        }
        match frame.set_monitor(Monitor::Named(target.to_owned())) {
            Ok(()) => {
                eprintln!("talos-bar-rs: moving to output {target:?}");
                self.moving = Some(target.to_owned());
            }
            Err(err) => eprintln!("talos-bar-rs: cannot move to {target:?}: {err}"),
        }
    }

    fn open_kind(&mut self, kind: Kind, x: f32) {
        self.open = toggle(self.open, kind, x);
        self.selected = None;
        if self.open.is_some() {
            // Refresh on open, as the Qt popups do.
            match kind {
                Kind::Tasks => self.feeds.tasks.refresh(),
                Kind::Agenda => self.feeds.agenda.refresh(),
                Kind::World | Kind::Screens | Kind::Disk => {}
            }
        }
    }
}

impl App for Bar {
    fn frame(&mut self, frame: &mut Frame<'_>) {
        if closes(frame.events()) {
            self.close();
        }
        for event in frame.events() {
            if let Event::Monitor { name, fallback } = event {
                eprintln!("talos-bar-rs: on output {name:?}");
                self.moving = None;
                if *fallback {
                    eprintln!(
                        "talos-bar-rs: WARNING: the configured screen is not connected; using the desktop's primary output {name:?}"
                    );
                }
            }
        }
        self.outputs = frame.monitors().iter().map(Output::from).collect();
        self.place(frame);
        self.poll_edit();
        // The cell's count stays fresh without the panel; the panel refreshes on open.
        self.feeds.tasks.refresh_if_older(Duration::from_secs(self.config.panels.refresh_secs));
        self.feeds.agenda.refresh_if_older(AGENDA_REFRESH);
        let agenda = panel::agenda_cell(self.feeds.agenda.get().0.as_ref());
        let fit = self.config.bar.fit;
        let labels = clock::Labels { short: self.config.clock.short, explicit: &self.config.clock.labels };
        let now = self.now.unwrap_or_else(Utc::now);
        let world = clock::world(now, self.local, &self.config.clock.zones, self.config.clock.seconds, labels);
        if !self.warned && !world.bad.is_empty() {
            eprintln!("talos-bar-rs: unknown time zones in config, skipped: {:?}", world.bad);
        }
        self.warned = true;
        let status = self.status.lock().map_or(Status::Unknown, |s| *s).text();
        // One statvfs per frame: the tab exists only above the threshold, so
        // the bar says nothing about a disk that still has room.
        self.pressure = Pressure::read(&disk::root()).ok();
        let disk_cell = self.pressure.and_then(|p| disk::cell(p, disk::threshold()));
        let tasks = panel::tasks_cell(self.feeds.tasks.get().0.as_ref());
        let (size, spacing) = (self.text_size, self.spacing);
        let (time, date) = (world.local.time.clone(), world.date.clone());
        let zone_texts: Vec<String> = world.zones.iter().map(Cell::text).collect();
        let mut search = false;
        let mut clicked: Option<(Kind, f32)> = None;
        let mut shown = 0;
        let mut measured = 0.0;
        let mut cells = (0.0, 0.0, 0.0);
        let mut content = 0.0;
        let mut agenda_shown = None;
        let mut edge = self.edge_seen;
        frame.horizontal(|row| {
            // The real surface width, not `[bar] width`: the compositor sizes a dock.
            let full = row.available_width();
            let x = |row: &Frame<'_>| MARGIN + full - row.available_width();
            row.label("talos");
            search = row.button(SEARCH);
            let tasks_x = x(row);
            cells.0 = tasks_x;
            if row.button(&tasks) {
                clicked = Some((Kind::Tasks, tasks_x));
            }
            if let Some(text) = &disk_cell
                && row.button(text)
            {
                // The click opens the tree as a web page, per the item's spec:
                // the dock is too narrow for a nested tree, and a page survives
                // the bar restarting. Serving is a loopback socket; opening is
                // the session's own browser.
                self.disk.refresh(&disk::root());
                let (rows, _, _) = self.disk.peek();
                let pressure = self.pressure;
                std::thread::spawn(move || {
                    if let Err(error) = diskview::open(&rows, pressure) {
                        eprintln!("talos-bar-rs: disk view failed: {error}");
                    }
                });
            }
            // What the left group actually took, button padding included.
            let left = full - row.available_width();
            let measure = |text: &str| layout::item(row.text_width(text, size), spacing);
            let zone_widths: Vec<f32> = zone_texts.iter().map(|t| measure(t)).collect();
            // The last item has no spacing after it.
            let right = row.text_width(&status, size);
                // Two buttons: the time (agenda) and the date (world clock).
            let centre = measure(&time) + measure(&date) + 2.0 * BUTTON_PAD;
            // The agenda cell only gets what the clock zones leave: lay out without it, then
            // give it at most the free space, so it can never push a zone out.
            // The zones are decided without the agenda cell, so it can never cost one.
            let split = layout::split(full, left, centre, &zone_widths, right);
            edge = edge.max(full + 2.0 * MARGIN);
            shown = split.zones;
            measured = full;
            row.space(split.gap_left);
            let clock_x = x(row);
            cells.1 = clock_x;
            if row.button(&time) {
                clicked = Some((Kind::Agenda, clock_x));
            }
            let date_x = x(row);
            cells.2 = date_x;
            if row.button(&date) {
                clicked = Some((Kind::World, date_x));
            }
            for zone in &zone_texts[..split.zones] {
                row.label(zone);
            }
            // The agenda cell gets what is really left (measured now, not estimated), less the
            // status and slack; a fit bar instead gets what the screen's edge leaves.
            let left_now = row.available_width() - right - spacing - AGENDA_SLACK;
            let free = if fit {
                let rest = left + centre + zone_widths.iter().sum::<f32>() + right + 2.0 * MARGIN + AGENDA_SLACK;
                (edge - rest).min(AGENDA_CELL_MAX)
            } else {
                left_now.min(AGENDA_CELL_MAX)
            };
            // A button's padding and the spacing after it come out of the budget.
            let budget = free - BUTTON_PAD - spacing;
            let agenda_text = agenda.as_deref().and_then(|text| layout::truncate(text, budget, |t| row.text_width(t, size)));
            let agenda_width = agenda_text.as_deref().map_or(0.0, |t| layout::item(row.text_width(t, size), spacing) + BUTTON_PAD);
            // Fit: everything, no gaps (the surface is resized to it after this frame).
            content = left + centre + zone_widths.iter().sum::<f32>() + right + agenda_width + AGENDA_SLACK;
            row.space((left_now - agenda_width).max(0.0));
            if let Some(text) = &agenda_text {
                let agenda_x = x(row);
                if row.button(text) {
                    clicked = Some((Kind::Agenda, agenda_x));
                }
            }
            agenda_shown = agenda_text;
            row.label(&status);
        });
        self.laid_out = Some((shown, zone_texts.len()));
        self.agenda_shown = agenda_shown;
        self.edge_seen = edge;
        if fit {
            // The frame's margin on both sides, rounded up so the last label never wraps.
            let wanted = (content + 2.0 * MARGIN).ceil() + 1.0;
            if self.fit_asked.is_none_or(|asked| (asked - wanted).abs() >= 1.0) {
                self.fit_asked = Some(wanted);
                match frame.set_dock_length(DockLength::Fixed(wanted)) {
                    Ok(()) => eprintln!("talos-bar-rs: fit: bar length {wanted:.0} px"),
                    // Offscreen (snapshot) there is no dock to resize.
                    Err(err) => eprintln!("talos-bar-rs: fit: {err}"),
                }
            }
        }
        if !self.reported {
            // Once: what the layout saw, so a dropped zone is explained rather than silent.
            eprintln!(
                "talos-bar-rs: layout width {measured:.0} px, local clock + {shown} of {} zones shown; tasks cell x={:.0}, clock cell x={:.0}, date cell x={:.0}",
                zone_texts.len(),
                cells.0,
                cells.1,
                cells.2
            );
            if shown < zone_texts.len() {
                eprintln!(
                    "talos-bar-rs: WARNING: {} zone(s) did not fit at {measured:.0} px and are not shown: {}; shorten them with [clock] labels",
                    zone_texts.len() - shown,
                    world.names[shown..].join(", ")
                );
            }
            self.reported = true;
        }
        // The tray's actions, at the latest one clock tick after the click.
        while let Some(act) = self.tray.as_ref().and_then(|tray| tray.try_recv().ok()) {
            eprintln!("talos-bar-rs: tray: {act:?}");
            match act {
                crate::tray::Act::Search => search = true,
                crate::tray::Act::Tasks => clicked = clicked.or(Some((Kind::Tasks, cells.0))),
                crate::tray::Act::Quit => {
                    self.close();
                    frame.close();
                }
            }
        }
        if search {
            (self.on_search)();
        }
        if let Some((kind, x)) = clicked {
            self.open_kind(kind, x);
        } else if let Some(x) = right_click(frame.events()) {
            // Under the pointer, like the Qt menu under the bar.
            self.open_kind(Kind::Screens, x - SCREENS_WIDTH / 2.0);
        }
        let screen = measured + 2.0 * MARGIN;
        let mut next = layout::until_next_second(Utc::now().timestamp_subsec_nanos());
        if let Some((kind, x)) = self.open {
            let (lines, loading) = self.panel_lines(kind);
            let width = kind.width();
            // Text wraps inside the panel's margins and the scroll bar's gutter.
            let inner = width - 2.0 * MARGIN - 12.0;
            let wraps = |text: &str| panel::wrapped(frame.text_width(text, size), inner);
            let height = panel::height(&lines, frame.line_height(size), spacing, MARGIN, self.config.panels.max_height, wraps);
            let options = PanelOptions::new(width, height).edge(Edge::Top).offset(panel_offset(x, width, screen));
            if let Err(err) = frame.show_panel(&options) {
                if !self.panel_error {
                    eprintln!("talos-bar-rs: cannot open a panel here: {err}");
                    self.panel_error = true;
                }
                self.open = None;
            }
            if loading {
                // The script's (or an edit's) result resizes the panel; look for it soon.
                next = next.min(LOADING_POLL);
            }
        }
        // The clock changes on second boundaries; nothing else needs a frame without input.
        frame.request_repaint_after(next);
    }

    fn panel(&mut self, frame: &mut Frame<'_>) {
        if closes(frame.events()) {
            self.close();
            return;
        }
        self.poll_edit();
        let Some((kind, _)) = self.open else { return };
        let (lines, loading) = self.panel_lines(kind);
        let height = frame.available_height();
        let mut acted = None;
        let size = self.text_size;
        let draft = &mut self.draft;
        frame.scroll(height, |frame| acted = panel::draw(frame, &lines, size, draft));
        if let Some(act) = acted {
            self.act(act);
            // A toggle's result, a selection's card or a closed panel: draw again soon.
            frame.request_repaint_after(LOADING_POLL);
        }
        if loading {
            frame.request_repaint_after(LOADING_POLL);
        }
        if kind == Kind::World {
            // Its seconds tick.
            frame.request_repaint_after(layout::until_next_second(Utc::now().timestamp_subsec_nanos()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kernal_api::gfx::Modifiers;

    #[test]
    fn a_panel_stays_on_screen() {
        assert!((panel_offset(100.0, 420.0, 1280.0) - 100.0).abs() < f32::EPSILON);
        assert!((panel_offset(1000.0, 420.0, 1280.0) - 860.0).abs() < f32::EPSILON);
        assert!(panel_offset(10.0, 2000.0, 1280.0).abs() < f32::EPSILON);
    }

    #[test]
    fn a_second_click_closes_and_another_cell_switches() {
        let open = toggle(None, Kind::Tasks, 50.0);
        assert_eq!(open, Some((Kind::Tasks, 50.0)));
        assert_eq!(toggle(open, Kind::Tasks, 50.0), None);
        assert_eq!(toggle(open, Kind::Agenda, 400.0), Some((Kind::Agenda, 400.0)));
    }

    #[test]
    fn esc_and_dismissal_close_other_input_does_not() {
        let key = |key, pressed| Event::Key { key, pressed, repeat: false, modifiers: Modifiers::default() };
        assert!(closes(&[key(Key::Escape, true)]));
        assert!(!closes(&[key(Key::Escape, false)]));
        assert!(!closes(&[key(Key::ArrowDown, true), Event::PointerLeft]));
        assert!(closes(&[Event::PanelDismissed]));
    }

    use crate::config::Config;
    use crate::searchd::Status;
    use kernal_api::gfx::{self, WindowOptions};
    use std::sync::Arc;

    fn wait<T>(mut ready: impl FnMut() -> Option<T>) -> T {
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(v) = ready() {
                return v;
            }
            assert!(std::time::Instant::now() < deadline, "timed out");
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn feeds(tasks_json: &'static str, cli: Cli) -> Feeds {
        Feeds {
            tasks: Feed::new(Arc::new(move || Ok(tasks_json.to_owned())), panel::parse_tasks),
            agenda: Feed::new(Arc::new(|| Ok("{}".to_owned())), panel::parse_agenda),
            tasks_cli: cli,
            launch: crate::launch::tests::recording(&[], true).0,
        }
    }

    fn no_cli() -> Cli {
        Arc::new(|_: &[&str]| Err("not in this test".to_owned()))
    }

    fn bar(config: Config, feeds: Feeds) -> Bar {
        Bar::new(config, Tz::America__Los_Angeles, Arc::new(Mutex::new(Status::Up { subscribers: 0 })), Theme::default(), Box::new(|| {}), feeds)
    }

    /// The default config at the default width lays out every zone, in the widest state the
    /// bar has: every zone showing a weekday, the widest tasks cell and status. Text is measured
    /// by the real font through `gfx::snapshot`'s frames, so this fails if a label grows.
    #[test]
    fn default_config_at_default_width_shows_every_zone() {
        let config = Config::default();
        let (width, zones) = (config.bar.width, config.clock.zones.len());
        let mut bar = bar(config, feeds(r#"{"open": 99, "synced": false}"#, no_cli()));
        bar.feeds.tasks.refresh();
        let _ = wait(|| bar.feeds.tasks.get().0);
        assert_eq!(panel::tasks_cell(bar.feeds.tasks.get().0.as_ref()), "Tasks 99 •");
        // 23:30 Sunday in Los Angeles: every configured zone is already on Monday.
        bar.now = Some(DateTime::parse_from_rfc3339("2026-11-02T07:30:00Z").expect("time").with_timezone(&Utc));
        let options = WindowOptions::new("talos-bar").size(width, crate::THICKNESS);
        // Painting needs an adapter; layout (all this test reads) runs before it, so a host
        // without one still answers.
        let _ = gfx::snapshot(&options, &mut bar);
        let (shown, laid_out) = bar.laid_out.expect("the bar laid out at least one frame");
        assert_eq!(laid_out, zones, "every configured zone is valid and not the local one");
        assert_eq!(shown, zones, "only {shown} of {zones} zones fit at {width} px");
    }

    /// The agenda cell is laid out from what the zones leave, so at 1280 px it can never cost a
    /// clock zone (that regression happened once with the tasks cell). Same widest state as
    /// above, plus the longest meeting title the calendar could send.
    #[test]
    fn the_agenda_cell_cannot_reduce_the_clock_zones_at_1280() {
        let shown = |agenda: &'static str, width: f32| {
            let config = Config::default();
            let mut feeds = feeds(r#"{"open": 99, "synced": false}"#, no_cli());
            feeds.agenda = Feed::new(Arc::new(move || Ok(agenda.to_owned())), panel::parse_agenda);
            let mut bar = bar(config, feeds);
            bar.feeds.tasks.refresh();
            bar.feeds.agenda.refresh();
            let _ = wait(|| bar.feeds.tasks.get().0);
            let _ = wait(|| bar.feeds.agenda.get().0);
            bar.now = Some(DateTime::parse_from_rfc3339("2026-11-02T07:30:00Z").expect("time").with_timezone(&Utc));
            let options = WindowOptions::new("talos-bar").size(width, crate::THICKNESS);
            let _ = gfx::snapshot(&options, &mut bar);
            let (shown, zones) = bar.laid_out.expect("laid out");
            (shown, zones, bar.agenda_shown.clone())
        };
        let (without, zones, none) = shown("{}", 1280.0);
        assert_eq!(none, None, "no next meeting, no cell");
        let long = r#"{"next_label": "10:00 AM", "next": {"title": "Quarterly planning review with the whole extended leadership team and every partner org, part 2 of 3", "status": "upcoming"}}"#;
        let (with, _, cell) = shown(long, 1280.0);
        eprintln!("1280 px: zones {without} of {zones} without the agenda cell, {with} with it; cell {cell:?}");
        assert_eq!(without, zones);
        assert!(with >= without, "the agenda cell cost {} zone(s)", without - with);
        // Wider, the cell appears, cut to its budget, and still costs nothing.
        let (wide, _, cell) = shown(long, 1600.0);
        eprintln!("1600 px: zones {wide} of {zones} with the agenda cell; cell {cell:?}");
        assert_eq!(wide, zones);
        let cell = cell.expect("room for the cell at 1600 px");
        assert!(cell.starts_with("10:00 AM · Quarterly") && cell.ends_with('…'), "{cell}");
    }

    const LIST: &str = r#"{"open": 1, "top": [{"text": "ship it", "check": false}, {"text": "note", "check": null}]}"#;

    /// A fake tasks_cli over an in-memory TASKS.md; `save64` can be made to lie or fail.
    fn fake_cli(file: Arc<Mutex<String>>, save: &'static str) -> Cli {
        Arc::new(move |args: &[&str]| {
            let mut file = file.lock().expect("file");
            match (args, save) {
                (["read"], _) => Ok(format!("{file}\n")),
                (["save64", _], "fail") => Err("tasks_cli exited with exit status: 1: Traceback".to_owned()),
                (["save64", _], "ignore") => Ok(String::new()),
                (["save64", b64], _) => {
                    assert_eq!(*b64, panel::base64("# T\n- [x] ship it\n- note\n".as_bytes()));
                    *file = "# T\n- [x] ship it\n- note\n".to_owned();
                    Ok(String::new())
                }
                _ => Err(format!("unexpected {args:?}")),
            }
        })
    }

    #[test]
    fn toggling_saves_the_whole_file_and_proves_it_landed() {
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note\n".to_owned()));
        let cli = fake_cli(Arc::clone(&file), "ok");
        let item = TaskLine { text: "ship it".into(), check: Some(false) };
        assert_eq!(toggle_task(&cli, &item), Ok("saved: marked done".into()));
        assert_eq!(*file.lock().expect("file"), "# T\n- [x] ship it\n- note\n");
    }

    #[test]
    fn a_failed_or_silent_save_is_reported_not_pretended() {
        let item = TaskLine { text: "ship it".into(), check: Some(false) };
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note\n".to_owned()));
        let err = toggle_task(&fake_cli(Arc::clone(&file), "fail"), &item).expect_err("fails");
        assert!(err.contains("exit status: 1"), "{err}");
        let err = toggle_task(&fake_cli(Arc::clone(&file), "ignore"), &item).expect_err("did not land");
        assert!(err.contains("does not have the edit"), "{err}");
        assert_eq!(*file.lock().expect("file"), "# T\n- [ ] ship it\n- note\n", "unchanged");
    }

    #[test]
    fn the_panel_shows_saving_then_the_error_and_reloads() {
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note\n".to_owned()));
        let mut bar = bar(Config::default(), feeds(LIST, fake_cli(file, "fail")));
        bar.feeds.tasks.refresh();
        let _ = wait(|| bar.feeds.tasks.get().0);
        bar.toggle(1); // the bullet: inert at the panel, and toggle_line refuses it too
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
        assert!(bar.notice.as_deref().is_some_and(|n| n.contains("not a checkbox")), "{:?}", bar.notice);
        let rows_before = bar.panel_lines(Kind::Tasks).0.len();
        bar.toggle(0);
        let (lines, _) = bar.panel_lines(Kind::Tasks);
        assert_eq!(lines.len(), rows_before, "the notice does not move the rows");
        if bar.edit.running() {
            assert_eq!(lines[1], Line::Text("saving…".into()));
        }
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
        let (lines, _) = bar.panel_lines(Kind::Tasks);
        assert!(matches!(&lines[1], Line::Text(t) if t.starts_with("could not toggle:") && t.contains("exit status: 1")), "{lines:?}");
        assert!(lines.contains(&Line::Row { text: "[ ] ship it".into(), current: false, act: Some(Act::Toggle(0)) }), "still open");
    }

    fn unbase64(text: &str) -> String {
        const ABC: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut bits = Vec::new();
        for c in text.bytes().filter(|c| *c != b'=') {
            let v = ABC.iter().position(|a| *a == c).expect("base64");
            bits.extend((0..6).rev().map(|i| u8::from((v >> i) & 1 == 1)));
        }
        let bytes: Vec<u8> = bits.as_chunks::<8>().0.iter().map(|b| b.iter().fold(0u8, |n, bit| n << 1 | bit)).collect();
        String::from_utf8(bytes).expect("utf8")
    }

    /// `tasks_cli.py` over an in-memory TASKS.md: `read`, `save64`, and `add64` placing the task
    /// after the last task line, as the script does. `fail` makes every write exit 1.
    fn script_cli(file: Arc<Mutex<String>>, fail: bool) -> Cli {
        Arc::new(move |args: &[&str]| {
            let mut file = file.lock().expect("file");
            match args {
                ["read"] => Ok(format!("{file}\n")),
                [_, _] if fail => Err("tasks_cli exited with exit status: 1".to_owned()),
                ["save64", b64] => {
                    *file = unbase64(b64).trim_end_matches('\n').to_owned();
                    Ok(String::new())
                }
                ["add64", b64] => {
                    let text = unbase64(b64);
                    let mut lines: Vec<String> = file.split('\n').map(str::to_owned).collect();
                    let at = lines.iter().rposition(|l| l.trim_start().starts_with("- [")).map_or(lines.len(), |i| i + 1);
                    lines.insert(at, format!("- [ ] {text}"));
                    *file = lines.join("\n");
                    Ok(String::new())
                }
                _ => Err(format!("unexpected {args:?}")),
            }
        })
    }

    #[test]
    fn add_rewrite_and_remove_go_through_tasks_cli_and_round_trip() {
        let original = "# Tasks\n\n- [ ] ship it\n- note\n- [x] done thing\n\nfooter";
        let file = Arc::new(Mutex::new(original.to_owned()));
        let cli = script_cli(Arc::clone(&file), false);
        let get = || file.lock().expect("file").clone();
        assert_eq!(edit_task(&cli, &Edit::Add("  buy milk \n".into())), Ok("saved: added".into()));
        assert_eq!(get(), "# Tasks\n\n- [ ] ship it\n- note\n- [x] done thing\n- [ ] buy milk\n\nfooter");
        let milk = TaskLine { text: "buy milk".into(), check: Some(false) };
        assert_eq!(edit_task(&cli, &Edit::Retext(milk, "buy oat milk".into())), Ok("saved: edited".into()));
        assert!(get().contains("\n- [ ] buy oat milk\n"), "{}", get());
        let note = TaskLine { text: "note".into(), check: None };
        assert_eq!(edit_task(&cli, &Edit::Retext(note, "a note".into())), Ok("saved: edited".into()));
        assert!(get().contains("\n- a note\n"), "plain bullets keep their marker");
        let done = TaskLine { text: "done thing".into(), check: Some(true) };
        assert_eq!(edit_task(&cli, &Edit::Retext(done, "done, renamed".into())), Ok("saved: edited".into()));
        assert!(get().contains("\n- [x] done, renamed\n"), "the checkbox is kept");
        // Every change reverted through the same seam: byte-identical.
        let oat = TaskLine { text: "buy oat milk".into(), check: Some(false) };
        assert_eq!(edit_task(&cli, &Edit::Remove(oat.clone())), Ok("saved: removed".into()));
        edit_task(&cli, &Edit::Retext(TaskLine { text: "a note".into(), check: None }, "note".into())).expect("revert");
        edit_task(&cli, &Edit::Retext(TaskLine { text: "done, renamed".into(), check: Some(true) }, "done thing".into())).expect("revert");
        assert_eq!(get(), original);
        // A stale pick is refused, not applied elsewhere.
        let err = edit_task(&cli, &Edit::Remove(oat)).expect_err("gone");
        assert!(err.contains("no longer a top-level task"), "{err}");
        assert!(edit_task(&cli, &Edit::Add("  ".into())).is_err(), "empty text never reaches the script");
        assert!(edit_task(&cli, &Edit::Retext(TaskLine { text: "ship it".into(), check: Some(false) }, " ".into())).is_err());
        assert_eq!(get(), original);
    }

    #[test]
    fn a_failing_script_is_shown_in_the_panel_and_keeps_the_draft() {
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note".to_owned()));
        let mut bar = bar(Config::default(), feeds(LIST, script_cli(Arc::clone(&file), true)));
        bar.feeds.tasks.refresh();
        let _ = wait(|| bar.feeds.tasks.get().0);
        bar.draft = "new thing".into();
        bar.act(Act::Add);
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
        let (lines, _) = bar.panel_lines(Kind::Tasks);
        assert!(matches!(&lines[1], Line::Text(t) if t.starts_with("could not add:") && t.contains("exit status: 1")), "{lines:?}");
        assert_eq!(bar.draft, "new thing", "the text survives a failed add");
        // Edit mode: pick, then a failed remove keeps the pick.
        bar.act(Act::EditMode(true));
        bar.act(Act::Pick(0));
        assert_eq!((bar.editor.clone(), bar.draft.as_str()), (Editor::On(Some(0)), "ship it"));
        bar.act(Act::Remove);
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
        let (lines, _) = bar.panel_lines(Kind::Tasks);
        assert!(matches!(&lines[1], Line::Text(t) if t.starts_with("could not remove:")), "{lines:?}");
        assert_eq!(bar.editor, Editor::On(Some(0)));
        assert_eq!(*file.lock().expect("file"), "# T\n- [ ] ship it\n- note", "nothing written");
    }

    #[test]
    fn a_landed_edit_clears_the_field_and_the_pick() {
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note".to_owned()));
        let mut bar = bar(Config::default(), feeds(LIST, script_cli(Arc::clone(&file), false)));
        bar.feeds.tasks.refresh();
        let _ = wait(|| bar.feeds.tasks.get().0);
        bar.act(Act::EditMode(true));
        bar.act(Act::Pick(0));
        bar.draft = "ship it today".into();
        bar.act(Act::Save);
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
        assert_eq!((bar.editor.clone(), bar.draft.as_str()), (Editor::On(None), ""));
        assert_eq!(*file.lock().expect("file"), "# T\n- [ ] ship it today\n- note");
        assert_eq!(bar.notice.as_deref(), Some("saved: edited"));
    }

    /// Settle the running edit and land it, as the next panel frame does.
    fn settle(bar: &mut Bar) {
        wait(|| (!bar.edit.running()).then_some(()));
        bar.poll_edit();
    }

    #[test]
    fn the_whole_file_editor_loads_saves_and_proves_it_landed() {
        let file = Arc::new(Mutex::new("# T\n- [ ] ship it\n- note".to_owned()));
        let mut bar = bar(Config::default(), feeds(LIST, script_cli(Arc::clone(&file), false)));
        bar.feeds.tasks.refresh();
        let _ = wait(|| bar.feeds.tasks.get().0);
        bar.open = Some((Kind::Tasks, 0.0));
        bar.act(Act::EditFile);
        assert_eq!(bar.editor, Editor::File(false));
        assert!(bar.panel_lines(Kind::Tasks).0.contains(&Line::Text("loading…".into())));
        settle(&mut bar);
        assert_eq!((bar.editor.clone(), bar.draft.as_str()), (Editor::File(true), "# T\n- [ ] ship it\n- note"));
        assert!(bar.panel_lines(Kind::Tasks).0.contains(&Line::Area { rows: panel::FILE_ROWS }));
        bar.draft.push_str("\n\n## Later\n- [ ] free text, any shape");
        bar.act(Act::SaveFile);
        settle(&mut bar);
        assert_eq!(bar.notice.as_deref(), Some("saved"));
        assert_eq!(bar.editor, Editor::File(true), "saving keeps the editor open");
        assert_eq!(*file.lock().expect("file"), "# T\n- [ ] ship it\n- note\n\n## Later\n- [ ] free text, any shape");
    }

    #[test]
    fn closing_the_file_editor_saves_an_unsaved_edit_as_tasks_qml_does() {
        let file = Arc::new(Mutex::new("# T".to_owned()));
        let mut bar = bar(Config::default(), feeds(LIST, script_cli(Arc::clone(&file), false)));
        bar.open = Some((Kind::Tasks, 0.0));
        bar.act(Act::EditFile);
        settle(&mut bar);
        bar.close(); // nothing edited: nothing saved
        assert!(!bar.edit.running());
        bar.act(Act::EditFile);
        settle(&mut bar);
        bar.draft = "# T\nedited".into();
        bar.close();
        settle(&mut bar);
        assert_eq!(*file.lock().expect("file"), "# T\nedited");
    }

    #[test]
    fn a_failed_load_or_save_is_reported() {
        let mut bar = bar(Config::default(), feeds(LIST, no_cli()));
        bar.act(Act::EditFile);
        settle(&mut bar);
        assert_eq!(bar.editor, Editor::Off);
        assert_eq!(bar.notice.as_deref(), Some("could not load: not in this test"));
        let file = Arc::new(Mutex::new("# T".to_owned()));
        let mut bar = super::tests::bar(Config::default(), feeds(LIST, script_cli(Arc::clone(&file), true)));
        bar.act(Act::EditFile);
        settle(&mut bar);
        bar.draft = "# T\nx".into();
        bar.act(Act::SaveFile);
        settle(&mut bar);
        assert_eq!(bar.notice.as_deref(), Some("could not save the file: tasks_cli exited with exit status: 1"));
        assert_eq!(bar.draft, "# T\nx", "the text survives a failed save");
    }

    #[test]
    fn open_on_github_goes_through_the_launch_seam() {
        let (launch, log) = crate::launch::tests::recording(&["xdg-open"], true);
        let mut feeds = feeds(LIST, no_cli());
        feeds.launch = launch;
        let mut bar = bar(Config::default(), feeds);
        bar.open = Some((Kind::Tasks, 0.0));
        bar.act(Act::Open(panel::TASKS_WEB.into()));
        assert_eq!(*log.lock().expect("log"), [["xdg-open", "https://github.com/zackees/tasks/blob/main/TASKS.md"]]);
        assert_eq!(bar.open, None);
    }

    const DAY: &str = r#"{"date": "Monday", "live": false, "next_label": "10:00 AM", "countdown": "in 3m",
        "next": {"title": "Sync", "time": "10:00 AM – 10:30 AM", "start": 1793642400.0, "status": "upcoming",
                 "link_kind": "Zoom", "url": "https://zoom.us/j/123456789", "join_url": "zoommtg://zoom.us/join?action=join&confno=123456789"},
        "events": [{"title": "Sync", "time": "10:00 AM – 10:30 AM", "start": 1793642400.0, "status": "upcoming",
                 "link_kind": "Zoom", "url": "https://zoom.us/j/123456789", "join_url": "zoommtg://zoom.us/join?action=join&confno=123456789",
                 "emails": ["ann@example.com"]}]}"#;

    #[test]
    fn agenda_clicks_select_then_launch_through_the_seam_and_close() {
        let (launch, log) = crate::launch::tests::recording(&["kioclient", "xdg-open"], false);
        let feeds = Feeds {
            tasks: Feed::new(Arc::new(|| Ok("{}".to_owned())), panel::parse_tasks),
            agenda: Feed::new(Arc::new(|| Ok(DAY.to_owned())), panel::parse_agenda),
            tasks_cli: no_cli(),
            launch,
        };
        let mut bar = bar(Config::default(), feeds);
        // 3 minutes before the meeting: inside Join's 5-minute lead.
        bar.now = Some(DateTime::from_timestamp(1_793_642_400 - 180, 0).expect("time"));
        bar.open_kind(Kind::Agenda, 300.0);
        let _ = wait(|| bar.feeds.agenda.get().0);
        let (lines, _) = bar.panel_lines(Kind::Agenda);
        let select = lines.iter().find_map(|l| match l {
            Line::Row { act: Some(act @ Act::Select(_)), .. } => Some(act.clone()),
            _ => None,
        }).expect("a selectable row");
        bar.act(select);
        assert_eq!(bar.selected.as_ref().map(|m| m.title.as_str()), Some("Sync"));
        assert!(bar.open.is_some(), "selecting keeps the panel open");
        let (lines, _) = bar.panel_lines(Kind::Agenda);
        let card = lines.iter().position(|l| *l == Line::Heading("Sync".into())).expect("details card");
        assert!(lines[card..].contains(&Line::Buttons(vec![("ann@example.com".into(), Act::Open("mailto:ann@example.com".into()))])));
        let join = lines[card..].iter().find_map(|l| match l {
            Line::Buttons(b) if b[0].0 == "Join Zoom meeting" => Some(b[0].1.clone()),
            _ => None,
        }).expect("card join");
        bar.act(join);
        assert_eq!(bar.open, None, "a launch closes the panel");
        assert_eq!(bar.selected, None);
        bar.open_kind(Kind::Agenda, 300.0);
        bar.act(Act::OpenCalendar);
        assert_eq!(*log.lock().expect("log"), [
            // No Zoom app (the recording launcher said so): the web link, as the glue blanks join_url.
            vec!["xdg-open".to_owned(), "https://zoom.us/j/123456789".to_owned()],
            vec!["kioclient".to_owned(), "exec".to_owned(), "org.kde.korganizer.desktop".to_owned()],
        ]);
    }

    #[test]
    fn the_disk_panel_prints_totals_rows_and_state() {
        let pressure = Some(Pressure { total: 1_000, free: 50 });
        let rows = vec![
            disk::Row { depth: 1, name: "/home".to_owned(), bytes: 800 },
            disk::Row { depth: 2, name: "/home/video".to_owned(), bytes: 600 },
        ];
        let lines = disk_lines(pressure, &rows, false, None);
        assert_eq!(lines[0], Line::Heading("Disk".to_owned()));
        assert_eq!(lines[1], Line::Text("950 B used of 1000 B · 50 B free".to_owned()));
        assert_eq!(lines[2], Line::Pair("/home".to_owned(), "800 B".to_owned()));
        assert_eq!(lines[3], Line::Pair("  /home/video".to_owned(), "600 B".to_owned()));
        assert!(lines.contains(&Line::Text("read-only: nothing is deleted here".to_owned())));

        // Before the first scan lands there is nothing to show but the wait.
        let pending = disk_lines(None, &[], true, None);
        assert!(pending.iter().any(|line| *line == Line::Text("scanning…".to_owned())));
        let failed = disk_lines(None, &[], false, None);
        assert!(failed.iter().any(|line| *line == Line::Text("nothing above 64.0 MiB".to_owned())));
    }
}

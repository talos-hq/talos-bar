//! Drop-down panel content: the tasks list and the agenda, as the Qt bar's widgets word them.
//!
//! Parsing (the scripts' JSON) and formatting (to [`Line`]s) are pure and tested here with no
//! script, compositor or GPU; [`draw`] only maps lines onto `Frame` widgets.

use kernal_api::gfx::{Event, Frame, Key};
use serde::Deserialize;

/// What clicking a row or button asks for.
#[derive(Clone, Debug, PartialEq)]
pub enum Act {
    /// Toggle `TaskList::top[index]`.
    Toggle(usize),
    /// Edit mode: pick `TaskList::top[index]` to rewrite or remove (fills the field with it).
    Pick(usize),
    /// Turn the tasks panel's edit mode on or off.
    EditMode(bool),
    /// Add the field's text as a new task.
    Add,
    /// Replace the picked task's text with the field's.
    Save,
    /// Remove the picked task.
    Remove,
    /// Drop the pick and the field's text.
    Cancel,
    /// Edit the whole TASKS.md in the panel (Tasks.qml's text editor): load it with `read`.
    EditFile,
    /// Save the whole-file editor's text with `save64`.
    SaveFile,
    /// Show this meeting's details card (Agenda.qml's pinned row).
    Select(Box<Meeting>),
    /// Agenda.qml's `openMeeting(row)`.
    Join(Box<Meeting>),
    /// `Qt.openUrlExternally(url)`: New Zoom Meeting, `mailto:`, `tel:`.
    Open(String),
    /// The glue's `open_calendar` action.
    OpenCalendar,
    /// Move the bar to the output with this name (the right-click screen menu).
    Screen(String),
    /// Open the disk tree as a web page (the More panel's entry, and the tab).
    DiskView,
}

/// One line of a panel.
#[derive(Clone, Debug, PartialEq)]
pub enum Line {
    Heading(String),
    Text(String),
    /// A list row; `current` highlights it (a meeting in progress, or the selected one). A
    /// click (or Enter on the focused row) does `act`; `None` is inert.
    Row { text: String, current: bool, act: Option<Act> },
    /// Push buttons side by side.
    Buttons(Vec<(String, Act)>),
    /// Two labels in a table row: every `left` in a panel is right-aligned to the widest one
    /// (the world clock's city column).
    Pair(String, String),
    /// The panel's one text field (the tasks editor's draft); Enter in it does `act`.
    Field { act: Act },
    /// The whole-file editor over the same draft, `rows` lines tall; Enter is a newline.
    Area { rows: usize },
    Separator,
}

fn text(s: impl Into<String>) -> Line {
    Line::Text(s.into())
}

// ── tasks_cli.py list ────────────────────────────────────────────────────────────────────────

/// `top[]`: a top-level list item; `check` is `None` for a plain bullet.
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaskLine {
    pub check: Option<bool>,
    pub text: String,
}

/// `tasks[]`: a checkbox line.
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Task {
    pub index: usize,
    pub done: bool,
    pub text: String,
}

/// `tasks_cli.py list` output (the glue's `TaskList`).
#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaskList {
    pub tasks: Vec<Task>,
    pub open: usize,
    pub synced: bool,
    pub top: Vec<TaskLine>,
}

impl Default for TaskList {
    fn default() -> Self {
        // The glue reads a missing `synced` as true.
        Self { tasks: Vec::new(), open: 0, synced: true, top: Vec::new() }
    }
}

/// Empty output is an empty list, as the glue's `json.loads(... or "{}")`.
pub fn parse_tasks(raw: &str) -> Result<TaskList, String> {
    let raw = if raw.trim().is_empty() { "{}" } else { raw };
    serde_json::from_str(raw).map_err(|err| format!("tasks_cli output: {err}"))
}

/// The bar cell: the open count, as the Qt item's text; unpushed edits get a dot, as the Qt
/// item's amber one (the panel spells it out).
pub fn tasks_cell(list: Option<&Result<TaskList, String>>) -> String {
    match list {
        Some(Ok(list)) if list.synced => format!("Tasks {}", list.open),
        Some(Ok(list)) => format!("Tasks {} •", list.open),
        Some(Err(_)) => "Tasks !".to_owned(),
        None => "Tasks".to_owned(),
    }
}

/// The agenda cell on the bar: the next meeting's time and title, as the Qt bar's agenda item
/// says it (`Now · title` while one is in progress). `None` when there is nothing to show (no
/// meeting left today, the script not answered yet, or failed: the panel says why).
pub fn agenda_cell(agenda: Option<&Result<Agenda, String>>) -> Option<String> {
    let Some(Ok(agenda)) = agenda else { return None };
    let next = agenda.next.as_ref()?;
    if agenda.live || next.status == "current" {
        return Some(format!("Now · {}", next.title));
    }
    let when = if agenda.next_label.is_empty() { next.time.split(" – ").next().unwrap_or_default() } else { &agenda.next_label };
    Some(format!("{when} · {}", next.title).trim_start_matches(" · ").to_owned())
}

/// Tasks panel: Tasks.qml's header and sync note, then the glue's `_tooltip` rows. The glue's
/// ☐/☑ marks are written as TASKS.md writes them (`[ ]`/`[x]`): the bar's font has no ballot box.
/// What the tasks editor is doing: browsing (rows toggle), or editing (rows pick one to
/// rewrite or remove). The field's text lives with the bar; this is only the mode.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum Editor {
    #[default]
    Off,
    /// Edit mode; `Some(index)` once a row is picked.
    On(Option<usize>),
    /// The whole file in a text area (Tasks.qml); `false` until `read` has filled it.
    File(bool),
}

/// Tasks.qml's "Open on GitHub" (`Qt.openUrlExternally`).
pub const TASKS_WEB: &str = "https://github.com/zackees/tasks/blob/main/TASKS.md";

/// Rows the whole-file editor shows before it scrolls.
pub const FILE_ROWS: usize = 18;

/// [`tasks_lines`] plus the editor: in browse mode a field to add a task and an Edit button;
/// in edit mode rows pick, and the picked task gets Save / Remove.
pub fn tasks_editor_lines(list: &TaskList, editor: &Editor) -> Vec<Line> {
    if let Editor::File(loaded) = editor {
        // Tasks.qml: the header and sync line, then the file as plain text.
        let mut lines = tasks_lines(list);
        lines.truncate(3);
        lines.push(if *loaded { Line::Area { rows: FILE_ROWS } } else { text("loading…") });
        lines.push(Line::Buttons(vec![
            ("Save".to_owned(), Act::SaveFile),
            ("Close editor".to_owned(), Act::EditMode(false)),
            ("Open on GitHub".to_owned(), Act::Open(TASKS_WEB.to_owned())),
        ]));
        return lines;
    }
    let mut lines = tasks_lines(list);
    // The editor goes above the list (after the heading, the sync line and the rule), so it is
    // in view however long the list is.
    let mut ed = Vec::new();
    let button = |label: &str, act: Act| (label.to_owned(), act);
    match editor {
        Editor::Off => {
            ed.push(Line::Field { act: Act::Add });
            ed.push(Line::Buttons(vec![button("Add task", Act::Add), button("Edit…", Act::EditMode(true))]));
            ed.push(Line::Buttons(vec![
                button("Edit file", Act::EditFile),
                button("Open on GitHub", Act::Open(TASKS_WEB.to_owned())),
            ]));
        }
        Editor::File(_) => unreachable!("returned above"),
        Editor::On(picked) => {
            // Every top-level row picks, plain bullets included (`tasks_lines` has one per item).
            let mut row = 0;
            for line in &mut lines {
                if let Line::Row { act, current, .. } = line {
                    *current = *picked == Some(row);
                    *act = Some(Act::Pick(row));
                    row += 1;
                }
            }
            match picked {
                Some(_) => {
                    ed.push(Line::Field { act: Act::Save });
                    ed.push(Line::Buttons(vec![
                        button("Save", Act::Save),
                        button("Remove", Act::Remove),
                        button("Cancel", Act::Cancel),
                    ]));
                }
                None => {
                    ed.push(text("Pick a task to edit or remove"));
                    ed.push(Line::Buttons(vec![button("Done", Act::EditMode(false))]));
                }
            }
        }
    }
    ed.push(Line::Separator);
    let at = 3.min(lines.len());
    lines.splice(at..at, ed);
    lines
}

pub fn tasks_lines(list: &TaskList) -> Vec<Line> {
    let mut lines = vec![
        Line::Heading("TASKS.md".to_owned()),
        text(if list.synced { "synced" } else { "saved, syncs at 0/6/12/18h" }),
        Line::Separator,
    ];
    if list.top.is_empty() {
        lines.push(text("Nothing yet"));
    }
    for (index, item) in list.top.iter().enumerate() {
        let mark = match item.check {
            None => "•",
            Some(true) => "[x]",
            Some(false) => "[ ]",
        };
        // Only checkboxes toggle; a plain bullet has no done state.
        let act = item.check.map(|_| Act::Toggle(index));
        lines.push(Line::Row { text: format!("{mark} {}", item.text), current: false, act });
    }
    lines
}

/// `TASKS.md` (as `tasks_cli.py read` prints it) with `item`'s checkbox flipped, ready for
/// `save64`. The item is the file's one `- [ ] text` / `* [x] text` line, indented at most three
/// spaces (CommonMark's top level), whose text and state match what the panel showed. A same
/// line at any depth counts as a duplicate. If the file no longer has exactly one such line it
/// was edited since it was listed, and nothing is written.
#[cfg(test)]
pub fn toggle_line(file: &str, item: &TaskLine) -> Result<String, String> {
    if item.check.is_none() {
        return Err(format!("\"{}\" is not a checkbox", item.text));
    }
    change_line(file, item, &Change::Toggle)
}

/// What [`change_line`] does to the one matching line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    /// Flip the checkbox.
    Toggle,
    /// Replace the text, keeping indent, marker and checkbox.
    Retext(String),
    /// Delete the line.
    Remove,
}

/// `file` with `item`'s one top-level line changed. Matching is the toggle's: the line's
/// text and state (checkbox or plain bullet) must match what the panel showed, exactly once.
pub fn change_line(file: &str, item: &TaskLine, change: &Change) -> Result<String, String> {
    let done = item.check;
    // (indent, state, text) of a list line; state `None` for a plain bullet.
    let parse = |line: &str| -> Option<(usize, Option<bool>, String)> {
        let body = line.trim_start_matches(' ');
        let rest = body.strip_prefix("- ").or_else(|| body.strip_prefix("* ")).or_else(|| body.strip_prefix("+ "))?;
        let indent = line.len() - body.len();
        let Some(boxed) = rest.strip_prefix('[') else { return Some((indent, None, rest.trim().to_owned())) };
        let mut chars = boxed.chars();
        let state = match chars.next() {
            Some(' ') => false,
            Some('x' | 'X') => true,
            _ => return Some((indent, None, rest.trim().to_owned())),
        };
        match chars.as_str().strip_prefix("] ") {
            Some(text) => Some((indent, Some(state), text.trim().to_owned())),
            None => Some((indent, None, rest.trim().to_owned())),
        }
    };
    let lines: Vec<&str> = file.split('\n').collect();
    let hits: Vec<(usize, usize)> = lines
        .iter()
        .enumerate()
        .filter_map(|(i, line)| parse(line).map(|p| (i, p)))
        .filter(|(_, (_, state, text))| *state == done && text == item.text.trim())
        .map(|(i, (indent, _, _))| (i, indent))
        .collect();
    let (at, indent) = match hits.as_slice() {
        [(at, indent)] if *indent <= 3 => (*at, *indent),
        [] | [_] => return Err(format!("\"{}\" is no longer a top-level task in TASKS.md; reloaded", item.text)),
        many => return Err(format!("\"{}\" is in TASKS.md {} times; not guessing which", item.text, many.len())),
    };
    let line = lines[at];
    let mut out: Vec<String> = lines.iter().map(|l| (*l).to_owned()).collect();
    // Keep the indent and list marker (and the checkbox, after it).
    let prefix = indent + 2 + if done.is_some() { 4 } else { 0 };
    match change {
        Change::Toggle => {
            let Some(done) = done else { return Err(format!("\"{}\" is not a checkbox", item.text)) };
            let mark = indent + 3;
            out[at] = format!("{}{}{}", &line[..mark], if done { ' ' } else { 'x' }, &line[mark + 1..]);
        }
        Change::Retext(text) => {
            let text = one_line(text).ok_or("a task needs some text")?;
            out[at] = format!("{}{text}", &line[..prefix]);
        }
        Change::Remove => {
            out.remove(at);
        }
    }
    Ok(out.join("\n"))
}

/// `text` as one task line: trimmed, newlines as spaces (`tasks_cli add`'s rule); `None` if empty.
pub fn one_line(text: &str) -> Option<String> {
    let text = text.trim().replace(['\n', '\r'], " ");
    (!text.is_empty()).then_some(text)
}

/// Standard base64 (RFC 4648, padded), what `tasks_cli.py save64` decodes.
pub fn base64(bytes: &[u8]) -> String {
    const ABC: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | u32::from(*b) << (16 - 8 * i));
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(char::from(ABC[(n >> (18 - 6 * i) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

// ── calendar_agenda.py ───────────────────────────────────────────────────────────────────────

/// `calendar_agenda.Row`, the fields the panel shows.
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Meeting {
    pub title: String,
    pub time: String,
    pub start: f64,
    pub end: f64,
    pub all_day: bool,
    pub status: String,
    pub meeting: bool,
    pub location: String,
    pub url: String,
    pub link_kind: String,
    pub join_url: String,
    pub details: String,
    pub emails: Vec<String>,
    pub phones: Vec<String>,
    pub tomorrow: bool,
}

impl Meeting {
    fn zoom(&self) -> bool {
        self.link_kind == "Zoom"
    }
}

/// `calendar_agenda.Feed`.
#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Agenda {
    pub date: String,
    pub events: Vec<Meeting>,
    pub done: Vec<Meeting>,
    pub tomorrow: Vec<Meeting>,
    pub next: Option<Meeting>,
    pub live: bool,
    pub following: Option<Meeting>,
    pub next_label: String,
    pub countdown: String,
    pub error: String,
}

pub fn parse_agenda(raw: &str) -> Result<Agenda, String> {
    let raw = if raw.trim().is_empty() { "{}" } else { raw };
    serde_json::from_str(raw).map_err(|err| format!("calendar_agenda output: {err}"))
}

/// Agenda.qml's `joinLeadSeconds`: Join waits until a meeting is this close or in progress.
pub const JOIN_LEAD_SECONDS: f64 = 5.0 * 60.0;

/// A meeting row as Agenda.qml's delegate words it: "NOW · " for the one in progress. A click
/// pins it (`pinnedRow`), so the selected row is highlighted too.
fn meeting_row(row: &Meeting, selected: Option<&Meeting>) -> Line {
    let current = row.status == "current";
    let now = if current { "NOW · " } else { "" };
    let shown = selected == Some(row);
    Line::Row {
        text: format!("{now}{}  {}", row.time, row.title),
        current: current || shown,
        act: Some(Act::Select(Box::new(row.clone()))),
    }
}

/// Agenda.qml's `joinable`: the next meeting is a Zoom one, and live or within the lead time.
pub fn joinable(agenda: &Agenda, now: f64) -> bool {
    agenda.next.as_ref().is_some_and(|next| next.zoom() && (agenda.live || next.start - now <= JOIN_LEAD_SECONDS))
}

/// `tel:` href as the QML builds it: the number's digits and `+` only.
pub fn tel_href(number: &str) -> String {
    format!("tel:{}", number.chars().filter(|c| c.is_ascii_digit() || *c == '+').collect::<String>())
}

/// Agenda.qml's `fullDetails(row)`, the details card: title, time, location (unless Zoom), the
/// Zoom join link, contacts, then the description.
pub fn details_lines(row: &Meeting) -> Vec<Line> {
    let mut lines = vec![Line::Separator, Line::Heading(row.title.clone()), text(row.time.clone())];
    if !row.location.is_empty() && !row.zoom() {
        lines.push(text(row.location.clone()));
    }
    if row.zoom() {
        lines.push(Line::Buttons(vec![("Join Zoom meeting".to_owned(), Act::Join(Box::new(row.clone())))]));
    }
    for email in &row.emails {
        lines.push(Line::Buttons(vec![(email.clone(), Act::Open(format!("mailto:{email}")))]));
    }
    for phone in &row.phones {
        lines.push(Line::Buttons(vec![(phone.clone(), Act::Open(tel_href(phone)))]));
    }
    // Blank lines in the description stay paragraph breaks, as the card's <br>s.
    lines.extend(row.details.lines().map(|l| text(l.to_owned())));
    lines
}

/// Agenda panel: Agenda.qml's header (New Zoom Meeting, Open calendar), next-up block with its
/// Join button, and sections, at most `max_rows` rows; then the `selected` meeting's details.
/// `now` is the wall clock in epoch seconds (for Join's lead time).
pub fn agenda_lines(agenda: &Agenda, max_rows: usize, now: f64, selected: Option<&Meeting>) -> Vec<Line> {
    let mut lines = vec![
        Line::Heading("Today's meetings".to_owned()),
        text(if agenda.date.is_empty() { "Calendar" } else { &agenda.date }),
        Line::Buttons(vec![
            ("New Zoom Meeting".to_owned(), Act::Open(crate::launch::NEW_ZOOM.to_owned())),
            ("Open calendar".to_owned(), Act::OpenCalendar),
        ]),
    ];
    // Agenda.qml's `title`: the next meeting's, else the countdown sentence.
    let title = agenda.next.as_ref().map_or(agenda.countdown.as_str(), |next| next.title.as_str());
    if let Some(next) = &agenda.next {
        lines.push(Line::Separator);
        lines.push(text(if agenda.live {
            "In progress".to_owned()
        } else {
            format!("{} · {}", agenda.next_label, agenda.countdown)
        }));
        lines.push(text(if agenda.live { title.to_owned() } else { format!("Next up: {title}") }));
        lines.push(text(next.time.clone()));
        if joinable(agenda, now) {
            lines.push(Line::Buttons(vec![("Join Zoom meeting now".to_owned(), Act::Join(Box::new(next.clone())))]));
        }
    }
    if agenda.events.is_empty() && agenda.done.is_empty() {
        lines.push(Line::Separator);
        lines.push(text(if agenda.error.is_empty() { "No meetings today" } else { &agenda.error }));
    }
    let mut budget = max_rows;
    let mut rows = |lines: &mut Vec<Line>, list: &[Meeting]| {
        let shown = list.len().min(budget);
        lines.extend(list[..shown].iter().map(|row| meeting_row(row, selected)));
        budget -= shown;
        if shown < list.len() {
            lines.push(text(format!("… {} more", list.len() - shown)));
        }
    };
    if !agenda.tomorrow.is_empty() {
        lines.push(Line::Separator);
        lines.push(text("Tomorrow"));
        rows(&mut lines, &agenda.tomorrow);
        if !agenda.events.is_empty() || !agenda.done.is_empty() {
            lines.push(text("Today"));
        }
    }
    if !agenda.events.is_empty() {
        if agenda.tomorrow.is_empty() {
            lines.push(Line::Separator);
        }
        rows(&mut lines, &agenda.events);
    }
    if !agenda.done.is_empty() {
        lines.push(text(format!("Done · {}", agenda.done.len())));
        // Agenda.qml opens the done section by default only when nothing is ahead.
        if agenda.events.is_empty() {
            rows(&mut lines, &agenda.done);
        }
    }
    let empty = agenda.next.is_none() && agenda.events.is_empty() && agenda.done.is_empty() && agenda.tomorrow.is_empty();
    if let (false, Some(row)) = (empty, selected) {
        lines.extend(details_lines(row));
    }
    lines
}

// ── shared ───────────────────────────────────────────────────────────────────────────────────

/// What a panel shows for a fetch that has not finished or failed.
/// The world-clock popup (`Clock.qml`): the date, the local time, then one row per zone.
pub fn world_lines(table: &crate::clock::Table) -> Vec<Line> {
    let mut lines = vec![text(table.date.clone()), Line::Heading(format!("{}: {}", table.local.0, table.local.1))];
    lines.extend(table.rows.iter().map(|row| {
        let day = if row.day.is_empty() { String::new() } else { format!(" {}", row.day) };
        Line::Pair(format!("{}:", row.city), format!("{}{day} • {}", row.time, row.difference))
    }));
    lines
}

pub fn status_lines(title: &str, loading: bool, error: Option<&str>) -> Vec<Line> {
    let mut lines = vec![Line::Heading(title.to_owned())];
    match error {
        Some(err) => lines.push(text(format!("could not load: {err}"))),
        None if loading => lines.push(text("loading…")),
        None => lines.push(text("nothing loaded yet")),
    }
    lines
}

/// Height the panel needs for `lines` (logical px): `row` is the body line height and
/// `wraps(text)` how many lines a text takes at the panel's width (headings count one).
pub fn height(lines: &[Line], row: f32, spacing: f32, margin: f32, max: f32, wraps: impl Fn(&str) -> usize) -> f32 {
    #[allow(clippy::cast_precision_loss, reason = "a handful of wrapped lines")]
    let rows = |text: &str| wraps(text).max(1) as f32 * row;
    let body: f32 = lines
        .iter()
        .map(|line| match line {
            Line::Heading(_) => row * 1.5,
            Line::Text(t) => rows(t),
            // A selectable row has button padding above and below.
            Line::Row { text, .. } => rows(text) + 6.0,
            Line::Buttons(_) => row + 6.0,
            // A horizontal row, as tall as a button row.
            Line::Pair(..) => row + 6.0,
            // A single-line text field: a row with frame padding.
            Line::Field { .. } => row + 8.0,
            // A code editor: its rows plus frame padding.
            #[allow(clippy::cast_precision_loss, reason = "a few dozen rows")]
            Line::Area { rows } => *rows as f32 * row + 8.0,
            Line::Separator => 6.0,
        } + spacing)
        .sum();
    // Never below three rows, never above `max`.
    (body + 2.0 * margin).max(row * 3.0).min(max)
}

/// Lines `text` wraps to in `width`, given its single-line width.
pub fn wrapped(text_width: f32, width: f32) -> usize {
    if width <= 0.0 {
        return 1;
    }
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, reason = "small positive")]
    let n = (text_width / width).ceil().max(1.0) as usize;
    n
}

/// Draws `lines`; returns the act of a row or button clicked (or activated with Enter).
/// The right-click screen menu (the Qt bar's `ScreenMenu.qml`): one row per output, the
/// current one ticked and highlighted.
pub fn screen_lines(outputs: &[crate::screen::Output]) -> Vec<Line> {
    let mut lines = vec![Line::Heading("Show bar on".to_owned())];
    if outputs.is_empty() {
        lines.push(text("This desktop does not list its screens to the bar."));
    }
    for output in outputs {
        let name = if output.name.is_empty() { "(unnamed output)" } else { &output.name };
        let mark = if output.current { "✔  " } else { "    " };
        lines.push(Line::Row {
            text: format!("{mark}{name}"),
            current: output.current,
            act: Some(Act::Screen(output.name.clone())),
        });
    }
    lines
}

pub fn draw(frame: &mut Frame<'_>, lines: &[Line], size: f32, draft: &mut String) -> Option<Act> {
    let mut acted = None;
    let column = lines
        .iter()
        .filter_map(|line| match line {
            Line::Pair(left, _) => Some(frame.text_width(left, size)),
            _ => None,
        })
        .fold(0.0, f32::max);
    for line in lines {
        match line {
            Line::Heading(t) => frame.heading(t),
            Line::Text(t) => frame.label(t),
            Line::Row { text, current, act } => {
                if frame.selectable_row(text, *current) && act.is_some() {
                    acted.clone_from(act);
                }
            }
            Line::Buttons(buttons) => frame.horizontal(|row| {
                for (label, act) in buttons {
                    if row.button(label) {
                        acted = Some(act.clone());
                    }
                }
            }),
            Line::Pair(left, right) => {
                let indent = column - frame.text_width(left, size);
                frame.horizontal(|row| {
                    // Always a space, even of zero, so every row has the same item spacing.
                    row.space(indent.max(0.0));
                    row.label(left);
                    row.label(right);
                });
            }
            Line::Field { act } => {
                frame.text_field(draft);
                let enter = frame
                    .events()
                    .iter()
                    .any(|e| matches!(e, Event::Key { key: Key::Enter, pressed: true, .. }));
                if enter {
                    acted = Some(act.clone());
                }
            }
            Line::Area { rows } => {
                frame.text_area(draft, *rows);
            }
            Line::Separator => frame.separator(),
        }
    }
    acted
}

#[cfg(test)]
mod tests {
    use super::*;

    // Shapes copied from real `tasks_cli.py list` / `calendar_agenda.py` output (trimmed).
    const TASKS: &str = r#"{"tasks": [{"index": 0, "done": false, "text": "branded email"}, {"index": 1, "done": true, "text": "follow-up"}],
        "open": 1, "synced": false,
        "top": [{"text": "twp -> loop", "check": null}, {"text": "branded email", "check": false}, {"text": "follow-up", "check": true}]}"#;

    const AGENDA: &str = r#"{"date": "Monday, November 2",
        "events": [
          {"title": "Standup", "time": "9:00 AM – 9:15 AM", "start": 1.0, "end": 2.0, "all_day": false, "status": "current", "meeting": true,
           "location": "", "url": "", "link_kind": "Zoom", "join_url": "", "details": "", "emails": [], "phones": [], "tomorrow": false},
          {"title": "Meeting with David", "time": "10:00 AM – 11:00 AM", "start": 3.0, "end": 4.0, "all_day": false, "status": "upcoming",
           "meeting": true, "location": "", "url": "", "link_kind": "", "join_url": "", "details": "", "emails": [], "phones": [], "tomorrow": false}],
        "done": [{"title": "Gym", "time": "7:00 AM – 8:00 AM", "status": "past"}],
        "tomorrow": [], "next": {"title": "Standup", "time": "9:00 AM – 9:15 AM", "status": "current", "link_kind": "Zoom"},
        "live": true, "following": null, "next_label": "Now", "countdown": "In progress", "error": ""}"#;

    #[test]
    fn tasks_parse_the_scripts_field_names() {
        let list = parse_tasks(TASKS).expect("parse");
        assert_eq!(list.open, 1);
        assert!(!list.synced);
        assert_eq!(list.tasks[1], Task { index: 1, done: true, text: "follow-up".into() });
        assert_eq!(list.top[0].check, None);
        assert_eq!(parse_tasks("").expect("empty"), TaskList::default());
        assert!(parse_tasks("not json").is_err());
    }

    #[test]
    fn tasks_panel_uses_the_glue_marks_and_sync_note() {
        let lines = tasks_lines(&parse_tasks(TASKS).expect("parse"));
        assert_eq!(lines[0], Line::Heading("TASKS.md".into()));
        assert_eq!(lines[1], text("saved, syncs at 0/6/12/18h"));
        let rows: Vec<_> = lines
            .iter()
            .filter_map(|l| match l {
                Line::Row { text, .. } => Some(text.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(rows, ["• twp -> loop", "[ ] branded email", "[x] follow-up"]);
        let toggles: Vec<_> = lines.iter().filter_map(|l| match l {
            Line::Row { act, .. } => Some(act.clone()),
            _ => None,
        }).collect();
        assert_eq!(toggles, [None, Some(Act::Toggle(1)), Some(Act::Toggle(2))], "bullets are inert, checkboxes toggle their top index");
        let empty = tasks_lines(&TaskList::default());
        assert_eq!(empty[1], text("synced"));
        assert_eq!(empty.last(), Some(&text("Nothing yet")));
    }

    #[test]
    fn tasks_cell_counts_open_tasks() {
        let list = parse_tasks(TASKS);
        assert_eq!(tasks_cell(Some(&list)), "Tasks 1 •");
        assert_eq!(tasks_cell(Some(&Ok(TaskList { open: 4, ..TaskList::default() }))), "Tasks 4");
        assert_eq!(tasks_cell(Some(&Err("x".into()))), "Tasks !");
        assert_eq!(tasks_cell(None), "Tasks");
    }

    #[test]
    fn agenda_live_meeting_reads_like_the_qml() {
        let agenda = parse_agenda(AGENDA).expect("parse");
        let lines = agenda_lines(&agenda, 10, 0.0, None);
        assert_eq!(lines[0], Line::Heading("Today's meetings".into()));
        assert_eq!(lines[1], text("Monday, November 2"));
        assert!(lines.contains(&text("In progress")));
        assert!(lines.contains(&text("Standup")), "live: the title without 'Next up'");
        let row = |want: &str| lines.iter().find_map(|l| match l {
            Line::Row { text, current, act } if text == want => Some((*current, act.clone())),
            _ => None,
        });
        assert_eq!(row("NOW · 9:00 AM – 9:15 AM  Standup").map(|r| r.0), Some(true));
        let (current, act) = row("10:00 AM – 11:00 AM  Meeting with David").expect("row");
        assert!(!current);
        assert_eq!(act, Some(Act::Select(Box::new(agenda.events[1].clone()))), "a click pins the row");
        assert_eq!(lines[2], Line::Buttons(vec![
            ("New Zoom Meeting".into(), Act::Open("https://zoom.us/start/videomeeting".into())),
            ("Open calendar".into(), Act::OpenCalendar),
        ]));
        // Live Zoom meeting: the headliner's Join button, after the next-up time.
        let join = lines.iter().position(|l| matches!(l, Line::Buttons(b) if b[0].0 == "Join Zoom meeting now")).expect("join");
        assert_eq!(lines[join - 1], text("9:00 AM – 9:15 AM"));
        // Something is still ahead, so the done section is collapsed to its header.
        assert!(lines.contains(&text("Done · 1")));
        assert!(!lines.iter().any(|l| matches!(l, Line::Row { text, .. } if text.contains("Gym"))));
    }

    #[test]
    fn agenda_upcoming_says_next_up_with_label_and_countdown() {
        let mut agenda = parse_agenda(AGENDA).expect("parse");
        agenda.live = false;
        agenda.next_label = "10:00 AM".into();
        agenda.countdown = "in 2h 05m".into();
        agenda.next = Some(agenda.events[1].clone());
        let lines = agenda_lines(&agenda, 10, 0.0, None);
        assert!(lines.contains(&text("10:00 AM · in 2h 05m")));
        assert!(lines.contains(&text("Next up: Meeting with David")));
        assert!(lines.contains(&text("10:00 AM – 11:00 AM")));
    }

    #[test]
    fn join_waits_for_the_lead_time_unless_live() {
        let zoom = Meeting { link_kind: "Zoom".into(), start: 1000.0, ..Meeting::default() };
        let agenda = Agenda { next: Some(zoom.clone()), ..Agenda::default() };
        assert!(!joinable(&agenda, 1000.0 - 301.0), "an hour-off meeting is not ready");
        assert!(joinable(&agenda, 1000.0 - 300.0));
        assert!(joinable(&Agenda { live: true, ..agenda.clone() }, 0.0));
        let plain = Agenda { next: Some(Meeting { link_kind: "Calendar".into(), ..zoom }), live: true, ..Agenda::default() };
        assert!(!joinable(&plain, 1000.0), "only Zoom meetings get the button");
    }

    #[test]
    fn details_card_is_the_qml_full_details() {
        let row = Meeting {
            title: "Board sync".into(),
            time: "1:00 PM – 2:00 PM".into(),
            location: "Room 4".into(),
            emails: vec!["a@b.co".into()],
            phones: vec!["+1 (555) 010-2000".into()],
            details: "Agenda:\n\n1. numbers".into(),
            link_kind: "Calendar".into(),
            ..Meeting::default()
        };
        let lines = details_lines(&row);
        assert_eq!(lines[..4], [Line::Separator, Line::Heading("Board sync".into()), text("1:00 PM – 2:00 PM"), text("Room 4")]);
        assert!(lines.contains(&Line::Buttons(vec![("a@b.co".into(), Act::Open("mailto:a@b.co".into()))])));
        assert!(lines.contains(&Line::Buttons(vec![("+1 (555) 010-2000".into(), Act::Open("tel:+15550102000".into()))])));
        assert!(lines.ends_with(&[text("Agenda:"), text(""), text("1. numbers")]));
        assert!(!lines.iter().any(|l| matches!(l, Line::Buttons(b) if b[0].0 == "Join Zoom meeting")));
        // A Zoom meeting: no location line (it is the link), and a Join button.
        let zoom = Meeting { link_kind: "Zoom".into(), location: "https://zoom.us/j/1".into(), ..row };
        let lines = details_lines(&zoom);
        assert!(!lines.contains(&text("https://zoom.us/j/1")));
        assert!(lines.contains(&Line::Buttons(vec![("Join Zoom meeting".into(), Act::Join(Box::new(zoom.clone())))])));
    }

    #[test]
    fn the_selected_row_is_highlighted_and_its_card_follows_the_list() {
        let agenda = parse_agenda(AGENDA).expect("parse");
        let pick = agenda.events[1].clone();
        let lines = agenda_lines(&agenda, 10, 0.0, Some(&pick));
        assert!(lines.iter().any(|l| matches!(l, Line::Row { text, current: true, .. } if text.ends_with("Meeting with David"))));
        let card = lines.iter().position(|l| *l == Line::Heading("Meeting with David".into())).expect("card");
        let last_row = lines.iter().rposition(|l| matches!(l, Line::Row { .. })).expect("rows");
        assert!(card > last_row, "the card is below the rows, so they never move under the pointer");
        assert!(!agenda_lines(&Agenda::default(), 10, 0.0, Some(&pick)).iter().any(|l| *l == Line::Heading("Meeting with David".into())), "no card on an empty day");
    }

    #[test]
    fn agenda_empty_day_shows_the_error_or_the_default() {
        let failed = Agenda { error: "Waiting for the first calendar sync.".into(), ..Agenda::default() };
        let lines = agenda_lines(&failed, 10, 0.0, None);
        assert_eq!(lines[1], text("Calendar"));
        assert!(lines.contains(&text("Waiting for the first calendar sync.")));
        let lines = agenda_lines(&Agenda { date: "Sunday".into(), ..Agenda::default() }, 10, 0.0, None);
        assert!(lines.contains(&text("No meetings today")));
    }

    #[test]
    fn agenda_tomorrow_leads_and_done_opens_when_nothing_is_ahead() {
        let row = |title: &str, status: &str| Meeting { title: title.into(), time: "1:00 PM – 2:00 PM".into(), status: status.into(), ..Meeting::default() };
        let agenda = Agenda {
            date: "Monday".into(),
            done: vec![row("Gym", "past")],
            tomorrow: vec![row("A", "upcoming"), row("B", "upcoming"), row("C", "upcoming")],
            countdown: "All of today's meetings are done".into(),
            ..Agenda::default()
        };
        let lines = agenda_lines(&agenda, 2, 0.0, None);
        let tomorrow = lines.iter().position(|l| *l == text("Tomorrow")).expect("tomorrow");
        let today = lines.iter().position(|l| *l == text("Today")).expect("today");
        assert!(tomorrow < today);
        assert!(lines.contains(&text("… 1 more")), "row budget of 2 cuts tomorrow's third");
        assert!(lines.contains(&text("Done · 1")));
        // The budget is spent, so the done rows themselves are cut too.
        assert!(lines.contains(&text("… 1 more")));
    }

    #[test]
    fn status_and_height() {
        assert_eq!(status_lines("Tasks", true, None)[1], text("loading…"));
        assert_eq!(status_lines("Tasks", false, Some("boom"))[1], text("could not load: boom"));
        let lines = [Line::Heading("h".into()), text("a"), Line::Separator, Line::Row { text: "r".into(), current: false, act: None }];
        let buttons = [Line::Buttons(vec![("b".into(), Act::OpenCalendar)])];
        assert!((height(&buttons, 20.0, 0.0, 0.0, 600.0, |_| 1) - 60.0).abs() < 1e-3, "a button row is a row (floored at three)");
        let one = |_: &str| 1;
        let h = height(&lines, 20.0, 8.0, 8.0, 600.0, one);
        assert!((h - (30.0 + 20.0 + 6.0 + 26.0 + 32.0 + 16.0)).abs() < 1e-3, "{h}");
        let two = |t: &str| if t == "a" { 2 } else { 1 };
        assert!((height(&lines, 20.0, 8.0, 8.0, 600.0, two) - h - 20.0).abs() < 1e-3, "a wrapped line adds a row");
        assert!((height(&lines, 20.0, 8.0, 8.0, 50.0, one) - 50.0).abs() < 1e-3, "capped");
        assert!((height(&[], 20.0, 0.0, 0.0, 500.0, one) - 60.0).abs() < 1e-3, "never below three rows");
        assert_eq!(wrapped(250.0, 100.0), 3);
        assert_eq!(wrapped(0.0, 100.0), 1);
        assert_eq!(wrapped(10.0, 0.0), 1);
    }

    fn item(text: &str, check: Option<bool>) -> TaskLine {
        TaskLine { text: text.into(), check }
    }

    const FILE: &str = "# Tasks\n\n- twp -> loop\n  - [ ] branded email\n      - [ ] nested sub\n* [X] follow-up\n";

    #[test]
    fn toggle_flips_only_the_matching_top_level_box() {
        let open = toggle_line(FILE, &item("branded email", Some(false))).expect("toggle");
        assert_eq!(open, FILE.replace("  - [ ] branded email", "  - [x] branded email"));
        let reopened = toggle_line(FILE, &item("follow-up", Some(true))).expect("toggle");
        assert_eq!(reopened, FILE.replace("* [X] follow-up", "* [ ] follow-up"), "marker kept");
        // Round trip is the identity.
        assert_eq!(toggle_line(&open, &item("branded email", Some(true))).expect("back"), FILE);
    }

    #[test]
    fn toggle_refuses_stale_ambiguous_nested_and_bullets() {
        let stale = toggle_line(FILE, &item("branded email", Some(true))).expect_err("state changed");
        assert!(stale.contains("no longer"), "{stale}");
        assert!(toggle_line(FILE, &item("nested sub", Some(false))).is_err(), "nested items are not top-level");
        let nested_twin = format!("{FILE}    - [ ] branded email\n");
        assert!(toggle_line(&nested_twin, &item("branded email", Some(false))).expect_err("dup").contains("2 times"), "a nested twin is still a duplicate");
        assert!(toggle_line(FILE, &item("twp -> loop", None)).expect_err("bullet").contains("not a checkbox"));
        let twice = format!("{FILE}- [ ] branded email\n");
        assert!(toggle_line(&twice, &item("branded email", Some(false))).expect_err("dup").contains("2 times"));
    }

    #[test]
    fn base64_matches_rfc4648() {
        for (raw, enc) in [("", ""), ("f", "Zg=="), ("fo", "Zm8="), ("foo", "Zm9v"), ("foob", "Zm9vYg=="), ("- [x] é\n", "LSBbeF0gw6kK")] {
            assert_eq!(base64(raw.as_bytes()), enc, "{raw:?}");
        }
    }

    #[test]
    fn the_agenda_cell_says_the_next_meeting() {
        let parse = |raw: &str| Some(parse_agenda(raw));
        let next = parse(r#"{"next_label": "10:00 AM", "next": {"title": "Sync", "status": "upcoming"}}"#);
        assert_eq!(agenda_cell(next.as_ref()).as_deref(), Some("10:00 AM · Sync"));
        let live = parse(r#"{"live": true, "next": {"title": "Sync", "status": "current"}}"#);
        assert_eq!(agenda_cell(live.as_ref()).as_deref(), Some("Now · Sync"));
        let unlabelled = parse(r#"{"next": {"title": "Sync", "time": "9:30 AM – 10:00 AM"}}"#);
        assert_eq!(agenda_cell(unlabelled.as_ref()).as_deref(), Some("9:30 AM · Sync"));
        assert_eq!(agenda_cell(parse("{}").as_ref()), None);
        assert_eq!(agenda_cell(Some(&Err("boom".to_owned()))), None);
        assert_eq!(agenda_cell(None), None);
    }

    #[test]
    fn the_editor_adds_in_browse_mode_and_picks_in_edit_mode() {
        let list = parse_tasks(TASKS).expect("parse");
        let browse = tasks_editor_lines(&list, &Editor::Off);
        assert!(browse.contains(&Line::Row { text: "[ ] branded email".into(), current: false, act: Some(Act::Toggle(1)) }));
        assert_eq!(browse[3], Line::Field { act: Act::Add }, "above the list");
        assert!(matches!(&browse[4], Line::Buttons(b) if b[0].1 == Act::Add && b[1].1 == Act::EditMode(true)));
        let editing = tasks_editor_lines(&list, &Editor::On(None));
        let picks: Vec<_> = editing.iter().filter_map(|l| match l { Line::Row { act, .. } => act.clone(), _ => None }).collect();
        assert_eq!(picks, [Act::Pick(0), Act::Pick(1), Act::Pick(2)], "plain bullets are pickable too");
        assert!(!editing.iter().any(|l| matches!(l, Line::Field { .. })), "no field until a pick");
        let picked = tasks_editor_lines(&list, &Editor::On(Some(1)));
        assert!(picked.contains(&Line::Row { text: "[ ] branded email".into(), current: true, act: Some(Act::Pick(1)) }));
        assert!(picked.contains(&Line::Field { act: Act::Save }));
        assert!(matches!(&picked[4], Line::Buttons(b) if b.iter().map(|x| &x.1).eq([&Act::Save, &Act::Remove, &Act::Cancel])));
    }

    #[test]
    fn change_line_rewrites_and_removes_only_the_matching_line() {
        let file = "# T\n- [ ] a\n    - [ ] nested\n* note\n- [x] b";
        let a = TaskLine { text: "a".into(), check: Some(false) };
        assert_eq!(change_line(file, &a, &Change::Retext("a2".into())).as_deref(), Ok("# T\n- [ ] a2\n    - [ ] nested\n* note\n- [x] b"));
        assert_eq!(change_line(file, &a, &Change::Remove).as_deref(), Ok("# T\n    - [ ] nested\n* note\n- [x] b"));
        let note = TaskLine { text: "note".into(), check: None };
        assert_eq!(change_line(file, &note, &Change::Retext("n".into())).as_deref(), Ok("# T\n- [ ] a\n    - [ ] nested\n* n\n- [x] b"));
        assert!(change_line(file, &note, &Change::Toggle).is_err(), "a bullet has no box");
        let nested = TaskLine { text: "nested".into(), check: Some(false) };
        assert!(change_line(file, &nested, &Change::Remove).is_err(), "not top level");
        let wrong_state = TaskLine { text: "b".into(), check: Some(false) };
        assert!(change_line(file, &wrong_state, &Change::Remove).is_err());
        assert_eq!(one_line(" x\ny "), Some("x y".into()));
        assert_eq!(one_line(" \n"), None);
    }
}

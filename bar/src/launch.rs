//! Opening links and apps from the agenda panel, the way the Qt bar does.
//!
//! What `src/talos_bar/glue/builtin/Agenda.qml` and `agenda.py` do:
//!
//! - **Join** (`openMeeting`): `row.join_url || row.url` through `Qt.openUrlExternally`. On Linux
//!   that is Qt's desktop-services `openUrl`, which hands the URL to `xdg-open` (on Plasma,
//!   `xdg-open` itself defers to KIO). The glue blanks `join_url` (the `zoommtg://` app link)
//!   when `xdg-mime query default x-scheme-handler/zoommtg` names no handler, so the web link is
//!   used instead; [`meeting_url`] applies the same rule.
//! - **Open calendar** (`open_calendar` action): `kioclient exec org.kde.korganizer.desktop` when
//!   `kioclient` is on `PATH`, else `xdg-open https://calendar.google.com`, else nothing.
//! - **New Zoom Meeting**, contact links (`mailto:`, `tel:`): `Qt.openUrlExternally(href)`.
//!
//! Every one closes the popup afterwards. Building the argv is pure and tested; running it goes
//! through [`Spawn`], which tests replace so no browser or KOrganizer is ever started by them.

use crate::panel::Meeting;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, OnceLock};

/// Agenda.qml's "New Zoom Meeting" button.
pub const NEW_ZOOM: &str = "https://zoom.us/start/videomeeting";
/// `open_calendar`'s fallback without `kioclient`.
pub const WEB_CALENDAR: &str = "https://calendar.google.com";

/// Starts `argv` without waiting for it; tests record instead.
pub type Spawn = Arc<dyn Fn(&[String]) -> Result<(), String> + Send + Sync>;
/// Whether a program is on `PATH` (`shutil.which`).
pub type Which = Arc<dyn Fn(&str) -> bool + Send + Sync>;

/// `Qt.openUrlExternally(url)` on Linux.
pub fn open_argv(url: &str) -> Vec<String> {
    vec!["xdg-open".to_owned(), url.to_owned()]
}

/// `agenda.open_calendar`: KOrganizer through KIO, else the web calendar, else nothing.
pub fn calendar_argv(which: &dyn Fn(&str) -> bool) -> Option<Vec<String>> {
    if which("kioclient") {
        Some(["kioclient", "exec", "org.kde.korganizer.desktop"].map(str::to_owned).to_vec())
    } else if which("xdg-open") {
        Some(open_argv(WEB_CALENDAR))
    } else {
        None
    }
}

/// `openMeeting`'s URL: the app link when the Zoom app handles it, else the web link.
pub fn meeting_url(row: &Meeting, zoom_app: bool) -> Option<String> {
    let join = if zoom_app { row.join_url.as_str() } else { "" };
    let url = if join.is_empty() { row.url.as_str() } else { join };
    (!url.is_empty()).then(|| url.to_owned())
}

/// `_zoom_app_installed`: no `xdg-mime` means "can't tell" (keep the app links); otherwise its
/// answer must name a handler.
pub fn zoom_app_from(xdg_mime_on_path: bool, query: impl FnOnce() -> String) -> bool {
    !xdg_mime_on_path || !query().trim().is_empty()
}

/// `shutil.which`: an executable file named `program` in a `PATH` directory.
pub fn on_path(program: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    let Some(path) = std::env::var_os("PATH") else { return false };
    std::env::split_paths(&path).any(|dir| {
        let file = dir.join(program);
        Path::new(&file).metadata().is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    })
}

/// Spawns detached (its own process group, no stdio), reaped on a thread, as `startDetached`.
pub fn real_spawn() -> Spawn {
    Arc::new(|argv: &[String]| {
        use std::os::unix::process::CommandExt;
        let (program, args) = argv.split_first().ok_or("empty command")?;
        let mut child = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .map_err(|err| format!("{program}: {err}"))?;
        std::thread::spawn(move || child.wait());
        Ok(())
    })
}

/// The agenda panel's launcher: the seams plus the Zoom-app answer, asked once off the UI thread.
#[derive(Clone)]
pub struct Launcher {
    spawn: Spawn,
    which: Which,
    zoom_app: Arc<OnceLock<bool>>,
}

/// What a launch did, for the log.
#[derive(Debug, PartialEq, Eq)]
pub enum Launched {
    Ran(Vec<String>),
    Nothing(&'static str),
}

impl Launcher {
    /// `zoom_app` answers whether `zoommtg://` has a handler; it runs once, on a thread.
    pub fn new(spawn: Spawn, which: Which, zoom_app: impl FnOnce() -> bool + Send + 'static) -> Self {
        let cell = Arc::new(OnceLock::new());
        let fill = Arc::clone(&cell);
        std::thread::spawn(move || {
            let _ = fill.set(zoom_app());
        });
        Self { spawn, which, zoom_app: cell }
    }

    /// The real desktop: `PATH` lookups, `xdg-mime`, detached children.
    pub fn desktop() -> Self {
        let query = || {
            zoom_app_from(on_path("xdg-mime"), || {
                let argv = ["xdg-mime", "query", "default", "x-scheme-handler/zoommtg"].map(str::to_owned);
                crate::feeds::run(&argv, std::time::Duration::from_secs(5)).unwrap_or_default()
            })
        };
        Self::new(real_spawn(), Arc::new(on_path), query)
    }

    /// Until the query answers, assume the app is there, as the glue does when it can't tell.
    fn zoom_app(&self) -> bool {
        self.zoom_app.get().copied().unwrap_or(true)
    }

    fn run(&self, argv: Vec<String>) -> Result<Launched, String> {
        (self.spawn)(&argv)?;
        Ok(Launched::Ran(argv))
    }

    pub fn join(&self, row: &Meeting) -> Result<Launched, String> {
        match meeting_url(row, self.zoom_app()) {
            Some(url) => self.run(open_argv(&url)),
            None => Ok(Launched::Nothing("the meeting has no link")),
        }
    }

    pub fn open(&self, url: &str) -> Result<Launched, String> {
        self.run(open_argv(url))
    }

    pub fn calendar(&self) -> Result<Launched, String> {
        match calendar_argv(&*self.which) {
            Some(argv) => self.run(argv),
            None => Ok(Launched::Nothing("neither kioclient nor xdg-open is on PATH")),
        }
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use std::sync::Mutex;

    /// A launcher that records argv; `zoom_app` is answered before it returns.
    pub fn recording(which: &'static [&'static str], zoom_app: bool) -> (Launcher, Arc<Mutex<Vec<Vec<String>>>>) {
        let log = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&log);
        let spawn: Spawn = Arc::new(move |argv: &[String]| {
            sink.lock().expect("log").push(argv.to_vec());
            Ok(())
        });
        let launcher = Launcher::new(spawn, Arc::new(move |p: &str| which.contains(&p)), move || zoom_app);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while launcher.zoom_app.get().is_none() {
            assert!(std::time::Instant::now() < deadline, "zoom query never answered");
            std::thread::yield_now();
        }
        (launcher, log)
    }

    fn zoom_row() -> Meeting {
        Meeting {
            url: "https://us02web.zoom.us/j/123456789?pwd=abc".into(),
            join_url: "zoommtg://zoom.us/join?action=join&confno=123456789&pwd=abc".into(),
            link_kind: "Zoom".into(),
            ..Meeting::default()
        }
    }

    #[test]
    fn join_prefers_the_app_link_only_when_the_app_handles_it() {
        let row = zoom_row();
        assert_eq!(meeting_url(&row, true).as_deref(), Some(row.join_url.as_str()));
        assert_eq!(meeting_url(&row, false).as_deref(), Some(row.url.as_str()), "the glue blanks join_url");
        let web_only = Meeting { join_url: String::new(), ..row };
        assert_eq!(meeting_url(&web_only, true).as_deref(), Some(web_only.url.as_str()));
        assert_eq!(meeting_url(&Meeting::default(), true), None);
    }

    #[test]
    fn calendar_is_korganizer_then_the_web_then_nothing() {
        assert_eq!(calendar_argv(&|p| p == "kioclient" || p == "xdg-open").expect("kio"), ["kioclient", "exec", "org.kde.korganizer.desktop"]);
        assert_eq!(calendar_argv(&|p| p == "xdg-open").expect("web"), ["xdg-open", "https://calendar.google.com"]);
        assert_eq!(calendar_argv(&|_| false), None);
    }

    #[test]
    fn zoom_app_check_matches_the_glue() {
        assert!(zoom_app_from(false, || unreachable!("no xdg-mime, no query")));
        assert!(zoom_app_from(true, || "Zoom.desktop\n".into()));
        assert!(!zoom_app_from(true, || "\n".into()));
    }

    #[test]
    fn launcher_runs_through_the_seam() {
        let (launcher, log) = recording(&["kioclient"], false);
        assert_eq!(launcher.join(&zoom_row()), Ok(Launched::Ran(open_argv("https://us02web.zoom.us/j/123456789?pwd=abc"))));
        assert!(matches!(launcher.join(&Meeting::default()), Ok(Launched::Nothing(_))));
        launcher.calendar().expect("calendar");
        launcher.open("mailto:a@b.c").expect("open");
        let log = log.lock().expect("log");
        assert_eq!(log.len(), 3, "no spawn for a meeting without a link");
        assert_eq!(log[1], ["kioclient", "exec", "org.kde.korganizer.desktop"]);
        assert_eq!(log[2], ["xdg-open", "mailto:a@b.c"]);
    }

    #[test]
    fn on_path_finds_executables_only() {
        assert!(on_path("sh"));
        assert!(!on_path("definitely-not-a-program-talos"));
    }
}

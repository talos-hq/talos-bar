//! talos-bar-rs: talos-bar's top dock drawn with `kernal_api::gfx` instead of Qt.
//!
//! A `Placement::Dock` at the top edge (a layer-shell surface with an exclusive zone on Wayland),
//! showing a search button, the multi-zone clock and talos-searchd's status. The Qt bar is
//! untouched; this is a separate binary. See README.md.

mod clock;
mod config;
mod disk;
mod diskview;
mod feeds;
mod launch;
mod layout;
mod panel;
mod screen;
mod searchd;
mod shot;
mod theme;
mod tray;
mod view;

use kernal_api::gfx::{self, DockAlign, DockLength, Edge, Monitor, Placement, Support, Theme, WindowOptions};
use kernal_api::platform::ipc::Endpoint;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub const THICKNESS: f32 = 36.0;

struct Args {
    endpoint: String,
    config: Option<PathBuf>,
    screenshot: Option<PathBuf>,
    /// With `--screenshot`: render this panel (fed by the real scripts) instead of the bar.
    panel: Option<view::Kind>,
    /// `--no-tray`: no status-area item.
    tray: bool,
}

fn usage() -> ! {
    eprintln!("usage: talos-bar-rs [--endpoint E] [--config PATH] [--no-tray] [--screenshot PNG [--panel tasks|agenda|world|disk]]");
    std::process::exit(2);
}

fn parse_args(mut args: impl Iterator<Item = String>) -> Option<Args> {
    let mut parsed = Args { endpoint: String::new(), config: config::default_path(), screenshot: None, panel: None, tray: true };
    let mut endpoint = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--endpoint" => endpoint = Some(args.next()?),
            "--config" => parsed.config = Some(PathBuf::from(args.next()?)),
            "--no-tray" => parsed.tray = false,
            "--screenshot" => parsed.screenshot = Some(PathBuf::from(args.next()?)),
            "--panel" => {
                parsed.panel = Some(match args.next()?.as_str() {
                    "tasks" => view::Kind::Tasks,
                    "agenda" => view::Kind::Agenda,
                    "world" => view::Kind::World,
                    "disk" => view::Kind::Disk,
                    _ => return None,
                });
            }
            _ => return None,
        }
    }
    parsed.endpoint = endpoint.unwrap_or_else(searchd::default_endpoint);
    Some(parsed)
}

fn options(width: f32, theme: Theme, translucent: bool, monitor: Monitor, align: config::Align) -> WindowOptions {
    // A "fit" bar starts full and shrinks to its content after the first frame (`view`).
    let align = match align {
        config::Align::Left => DockAlign::Start,
        config::Align::Center => DockAlign::Center,
        config::Align::Right => DockAlign::End,
    };
    WindowOptions::new("talos-bar")
        .dock_length(DockLength::Full)
        .dock_align(align)
        .size(width, THICKNESS)
        .theme(theme)
        .transparent(translucent)
        .monitor(monitor)
        .placement(Placement::Dock { edge: Edge::Top, thickness: THICKNESS })
}

/// The real scripts, run as the Qt glue runs them (see `feeds`).
fn script_feeds() -> view::Feeds {
    view::Feeds {
        tasks: feeds::Feed::new(feeds::script(feeds::tasks_command()), panel::parse_tasks),
        agenda: feeds::Feed::new(feeds::script(feeds::agenda_command()), panel::parse_agenda),
        tasks_cli: feeds::tasks_cli(),
        launch: launch::Launcher::desktop(),
    }
}

/// Offscreen: the panel `kind` as the dock would show it, after its script has answered.
struct PanelShot {
    bar: view::Bar,
    kind: view::Kind,
}

impl gfx::App for PanelShot {
    fn frame(&mut self, frame: &mut gfx::Frame<'_>) {
        let (lines, _) = self.bar.panel_lines(self.kind);
        let _ = panel::draw(frame, &lines, self.bar.text_size, &mut String::new());
    }
}

fn panel_shot(bar: view::Bar, kind: view::Kind, theme: Theme) -> Result<(PanelShot, WindowOptions), String> {
    match kind {
        view::Kind::Tasks => bar.feeds.tasks.refresh(),
        view::Kind::Agenda => bar.feeds.agenda.refresh(),
        view::Kind::World | view::Kind::Screens | view::Kind::Disk | view::Kind::More => {}
    }
    let deadline = std::time::Instant::now() + feeds::TIMEOUT;
    while bar.panel_lines(kind).1 {
        if std::time::Instant::now() > deadline {
            return Err("script timed out".to_owned());
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    let (lines, _) = bar.panel_lines(kind);
    let width = kind.width();
    // No frame to measure with before the snapshot: body text is ~1.4x the font size per line
    // and about half the font size per character.
    let (line, inner) = (bar.text_size * 1.4, width - 28.0);
    #[allow(clippy::cast_precision_loss, reason = "short strings")]
    let wraps = |text: &str| panel::wrapped(text.chars().count() as f32 * bar.text_size * 0.5, inner);
    let height = panel::height(&lines, line, bar.spacing, 8.0, bar.config.panels.max_height, wraps);
    // A panel surface draws with the panel's tokens (`[theme] panel`).
    let options = WindowOptions::new("talos-bar panel").size(width, height).theme(theme.for_panel());
    Ok((PanelShot { bar, kind }, options))
}

fn fail(message: impl std::fmt::Display) -> ! {
    eprintln!("talos-bar-rs: {message}");
    std::process::exit(1);
}

fn main() {
    let args = parse_args(std::env::args().skip(1)).unwrap_or_else(|| usage());
    let config = config::load(args.config.as_deref()).unwrap_or_else(|err| fail(format!("config: {err}")));
    let endpoint = Endpoint::new(args.endpoint.clone()).unwrap_or_else(|err| {
        eprintln!("talos-bar-rs: bad endpoint: {err}");
        std::process::exit(2);
    });
    let status = Arc::new(Mutex::new(searchd::Status::Unknown));
    let width = config.bar.width;
    let theme = theme::build(&config.theme, theme::system_mode).unwrap_or_else(|err| fail(format!("config: {err}")));
    let (theme, translucent) =
        theme::with_opacity(theme, config.bar.opacity).unwrap_or_else(|err| fail(format!("config: {err}")));
    let wanted = screen::configured(config.bar.screen.as_deref());
    eprintln!("talos-bar-rs: screen {}", wanted.as_deref().unwrap_or("(primary)"));
    let monitor = screen::monitor(wanted.clone());
    let align = config.bar.align;
    let options = |width, theme| options(width, theme, translucent, monitor.clone(), align);

    if let Some(path) = &args.screenshot {
        // Offscreen: no compositor needed. One status probe so the cell is real.
        let now = searchd::request(&endpoint, "status")
            .map_or(searchd::Status::Down, |subscribers| searchd::Status::Up { subscribers });
        *status.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = now;
        let bar = view::Bar::new(config, clock::local_zone(), status, theme, Box::new(|| {}), script_feeds());
        let written = match args.panel {
            Some(kind) => panel_shot(bar, kind, theme)
                .and_then(|(mut shot, options)| gfx::snapshot(&options, &mut shot).map_err(|err| err.to_string())),
            None => {
                let mut bar = bar;
                gfx::snapshot(&options(width, theme), &mut bar).map_err(|err| err.to_string())
            }
        };
        let written = written.and_then(|shot| {
                shot::write_png(path, shot.width(), shot.height(), shot.rgba())
                    .map_err(|err| format!("writing {}: {err}", path.display()))
            });
        match written {
            Ok(()) => println!("{}", path.display()),
            Err(err) => fail(format!("screenshot failed: {err}")),
        }
        return;
    }

    let caps = gfx::capabilities();
    if caps.dock == Support::Unsupported {
        // run() names the reason (no Wayland, no layer-shell, macOS/Windows not wired).
        let reason = gfx::run(options(width, theme), view::Bar::new(config, clock::local_zone(), status, theme, Box::new(|| {}), script_feeds()))
            .err()
            .map_or_else(|| "dock unsupported".to_owned(), |err| err.to_string());
        fail(format!("cannot dock here: {reason}"));
    }

    {
        let endpoint = endpoint.clone();
        let status = Arc::clone(&status);
        std::thread::spawn(move || searchd::poll_forever(&endpoint, &status));
    }
    let on_search = Box::new(move || {
        let endpoint = endpoint.clone();
        std::thread::spawn(move || match searchd::request(&endpoint, "show") {
            Ok(0) => eprintln!("talos-bar-rs: show sent, but no popover is subscribed"),
            Ok(n) => eprintln!("talos-bar-rs: show sent to {n} subscriber(s)"),
            Err(err) => eprintln!("talos-bar-rs: show failed: {err}"),
        });
    });
    let mut bar = view::Bar::new(config, clock::local_zone(), status, theme, on_search, script_feeds());
    bar.wanted = wanted;
    if args.tray {
        bar.tray = Some(tray::spawn(theme.accent));
    }
    if let Err(err) = gfx::run(options(width, theme), bar) {
        fail(err);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Option<Args> {
        parse_args(list.iter().map(|s| (*s).to_owned()))
    }

    #[test]
    fn flags_parse() {
        let parsed = args(&["--endpoint", "e", "--config", "/c.toml", "--screenshot", "/s.png"]).expect("args");
        assert_eq!(parsed.endpoint, "e");
        assert_eq!(parsed.config, Some(PathBuf::from("/c.toml")));
        assert_eq!(parsed.screenshot, Some(PathBuf::from("/s.png")));
        assert!(args(&["--bogus"]).is_none());
        assert_eq!(args(&["--panel", "agenda"]).and_then(|a| a.panel), Some(view::Kind::Agenda));
        assert!(args(&["--panel", "clock"]).is_none());
        assert!(args(&["--config"]).is_none());
        assert!(args(&[]).expect("none").tray);
        assert!(!args(&["--no-tray"]).expect("no tray").tray);
    }

    #[test]
    fn dock_options_are_a_top_dock() {
        assert_eq!(THICKNESS, 36.0);
        let _ = options(1280.0, Theme::default(), true, Monitor::Named("DP-1".into()), config::Align::Right);
    }
}

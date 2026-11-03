//! talos-popover: the Super+Space search popover drawn with `kernal_api::gfx`.
//!
//! It is a client of `talos-searchd`: every query and launch is a request on the daemon's
//! kernal-api IPC endpoint (see `searchd/src/main.rs` for the protocol). The Qt bar is untouched.
//!
//! By default it is resident: no window until searchd's `subscribe` stream says `show` (the
//! daemon owns the hotkey), then one popover per show; Esc, focus loss or a launch closes the
//! window and the process waits for the next show. `--oneshot` opens one popover and exits.
//! `--screenshot PATH` renders the popover without a window to a PNG and exits.

mod shot;
mod source;
mod view;

use kernal_api::gfx::{self, Placement, Support, WindowOptions};
use std::path::PathBuf;

const SIZE: (f32, f32) = (640.0, 400.0);

fn usage() -> ! {
    eprintln!("usage: talos-popover [--endpoint E] [--at X,Y] [--oneshot] [--query TEXT] [--screenshot PNG]");
    std::process::exit(2);
}

fn parse_at(value: &str) -> Option<(f32, f32)> {
    let (x, y) = value.split_once(',')?;
    Some((x.trim().parse().ok()?, y.trim().parse().ok()?))
}

/// Centred unless `--at` asks for a position the backend can at least try to honour.
fn placement(at: Option<(f32, f32)>, support: Support) -> Placement {
    match (at, support) {
        (Some((x, y)), Support::Supported | Support::BestEffort) => {
            if support == Support::BestEffort {
                eprintln!("talos-popover: absolute placement is best effort; the compositor may ignore it");
            }
            Placement::At { x, y }
        }
        (Some(_), _) => {
            eprintln!("talos-popover: absolute placement unsupported here; centring");
            Placement::Centered
        }
        (None, _) => Placement::Centered,
    }
}

struct Args {
    endpoint: String,
    at: Option<(f32, f32)>,
    oneshot: bool,
    query: String,
    screenshot: Option<PathBuf>,
}

fn parse_args(mut args: impl Iterator<Item = String>) -> Option<Args> {
    let mut parsed = Args { endpoint: String::new(), at: None, oneshot: false, query: String::new(), screenshot: None };
    let mut endpoint = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--endpoint" => endpoint = Some(args.next()?),
            "--at" => parsed.at = Some(parse_at(&args.next()?)?),
            "--oneshot" => parsed.oneshot = true,
            "--query" => parsed.query = args.next()?,
            "--screenshot" => parsed.screenshot = Some(PathBuf::from(args.next()?)),
            _ => return None,
        }
    }
    parsed.endpoint = endpoint.unwrap_or_else(source::default_endpoint);
    Some(parsed)
}

fn window(at: Option<(f32, f32)>) -> WindowOptions {
    let caps = gfx::capabilities();
    WindowOptions::new("talos search")
        .size(SIZE.0, SIZE.1)
        .resizable(false)
        .decorations(caps.undecorated == Support::Unsupported)
        .always_on_top(caps.always_on_top != Support::Unsupported)
        .placement(placement(at, caps.absolute_placement))
}

fn client(endpoint: &str) -> source::Searchd {
    source::Searchd::new(endpoint.to_owned()).unwrap_or_else(|err| {
        eprintln!("talos-popover: bad endpoint: {err}");
        std::process::exit(2);
    })
}

fn open(args: &Args, query: String) {
    if let Err(err) = gfx::run(window(args.at), view::Popover::new(client(&args.endpoint), query)) {
        eprintln!("talos-popover: {err}");
        std::process::exit(1);
    }
}

fn main() {
    let args = parse_args(std::env::args().skip(1)).unwrap_or_else(|| usage());
    if let Some(path) = &args.screenshot {
        // Rendered without a window, so it works where a window gets no frames (a locked
        // Wayland session, a display-less CI host).
        let mut popover = view::Popover::new(client(&args.endpoint), args.query.clone());
        let written = gfx::snapshot(&window(args.at), &mut popover)
            .map_err(|err| err.to_string())
            .and_then(|shot| {
                shot::write_png(path, shot.width(), shot.height(), shot.rgba())
                    .map_err(|err| format!("writing {}: {err}", path.display()))
            });
        match written {
            Ok(()) => println!("{}", path.display()),
            Err(err) => {
                eprintln!("talos-popover: screenshot failed: {err}");
                std::process::exit(1);
            }
        }
        return;
    }
    if args.oneshot {
        return open(&args, args.query.clone());
    }
    resident(&args);
}

/// Wait for searchd's `show`, open a popover with an empty query, repeat. searchd owns the
/// hotkey; this process never registers one.
fn resident(args: &Args) -> ! {
    let endpoint = kernal_api::platform::ipc::Endpoint::new(args.endpoint.clone()).unwrap_or_else(|err| {
        eprintln!("talos-popover: bad endpoint: {err}");
        std::process::exit(2);
    });
    let (shows, show) = std::sync::mpsc::channel();
    std::thread::spawn(move || source::subscribe_forever(&endpoint, &shows));
    loop {
        if show.recv().is_err() {
            eprintln!("talos-popover: subscription thread ended");
            std::process::exit(1);
        }
        open(args, String::new());
        // Presses that arrived while the popover was open are already answered.
        while show.try_recv().is_ok() {}
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
        let parsed = args(&["--oneshot", "--query", "fire", "--screenshot", "/x.png", "--endpoint", "e"]).expect("args");
        assert!(parsed.oneshot);
        assert_eq!(parsed.query, "fire");
        assert_eq!(parsed.screenshot, Some(PathBuf::from("/x.png")));
        assert_eq!(parsed.endpoint, "e");
        let default = args(&[]).expect("args");
        assert!(!default.oneshot && default.query.is_empty() && default.screenshot.is_none());
        assert!(args(&["--query"]).is_none());
        assert!(args(&["--bogus"]).is_none());
    }

    #[test]
    fn at_parses_and_respects_capability() {
        assert_eq!(parse_at("10, 20"), Some((10.0, 20.0)));
        assert_eq!(parse_at("10"), None);
        assert_eq!(placement(Some((1.0, 2.0)), Support::BestEffort), Placement::At { x: 1.0, y: 2.0 });
        assert_eq!(placement(Some((1.0, 2.0)), Support::Unsupported), Placement::Centered);
        assert_eq!(placement(None, Support::Supported), Placement::Centered);
    }
}

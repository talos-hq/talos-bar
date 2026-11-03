//! The bar's status-area item, through `kernal_api::system_tray` (StatusNotifierItem on KDE).
//!
//! The facade gives an item three fixed events; the bar names them after what it does with each:
//!
//! - left click and the first menu entry (`Activate`) open the search popover through searchd's
//!   `show`, the same request as the bar's Search button (no second launcher);
//! - middle click and the second entry (`OpenDashboard`) open or close the Tasks panel;
//! - the third entry (`Quit`) closes the bar.
//!
//! Hiding the dock is not offered: the gfx facade cannot unmap a dock and keep calling its frame,
//! so a hidden bar could not be told to come back. Registration happens on its own thread with the
//! facade's async runtime; events reach the bar over a channel and are read on its next frame
//! (the clock ticks every second, so at most a second later).

use kernal_api::gfx::Color;
use kernal_api::system_tray::{TrayEvent, TrayHandle, TrayOptions};
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

/// Icon edge in pixels.
pub const SIZE: u16 = 32;
const POLL: Duration = Duration::from_millis(100);

/// What a tray event asks the bar to do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Act {
    Search,
    Tasks,
    Quit,
}

pub fn act(event: TrayEvent) -> Act {
    match event {
        TrayEvent::Activate => Act::Search,
        TrayEvent::OpenDashboard => Act::Tasks,
        TrayEvent::Quit => Act::Quit,
    }
}

/// A `SIZE`² ARGB32 icon (network byte order, as StatusNotifierItem wants): a disc in `accent`
/// with a white bar across its top third, the dock it stands for.
pub fn icon(accent: Color) -> Vec<u8> {
    let n = usize::from(SIZE);
    let mut argb = Vec::with_capacity(n * n * 4);
    #[allow(clippy::cast_precision_loss, reason = "32 px")]
    let (centre, radius) = (n as f32 / 2.0, n as f32 / 2.0 - 0.5);
    for y in 0..n {
        for x in 0..n {
            #[allow(clippy::cast_precision_loss, reason = "32 px")]
            let (dx, dy) = (x as f32 + 0.5 - centre, y as f32 + 0.5 - centre);
            let inside = dx.hypot(dy) <= radius;
            let stripe = (9..14).contains(&y) && (7..n - 7).contains(&x);
            let pixel = match (inside, stripe) {
                (false, _) => [0, 0, 0, 0],
                (true, true) => [255, 255, 255, 255],
                (true, false) => [255, accent.r, accent.g, accent.b],
            };
            argb.extend_from_slice(&pixel);
        }
    }
    argb
}

pub fn options(accent: Color) -> TrayOptions {
    TrayOptions::new("talos-bar", "talos bar", SIZE, icon(accent))
        .and_then(|options| {
            options.with_labels("Search…", "Tasks panel", "Quit bar", "Click to search, middle-click for tasks")
        })
        .expect("constant tray options are valid")
}

/// Register the item on a thread of its own; its actions arrive on the returned channel. When
/// the desktop has no status-area host the thread logs why and ends, and the bar runs without.
pub fn spawn(accent: Color) -> Receiver<Act> {
    let (sender, receiver) = mpsc::channel();
    let options = options(accent);
    let spawned = std::thread::Builder::new().name("tray".into()).spawn(move || serve(options, &sender));
    if let Err(err) = spawned {
        eprintln!("talos-bar-rs: tray: no thread: {err}");
    }
    receiver
}

fn serve(options: TrayOptions, sender: &Sender<Act>) {
    let runtime = match kernal_api::async_engine::RuntimeBuilder::current_thread().enable_all().build() {
        Ok(runtime) => runtime,
        Err(err) => return eprintln!("talos-bar-rs: tray: no runtime: {err}"),
    };
    // A current-thread runtime only runs the item's D-Bus service while it is driven, so the
    // poll loop drives it: every wait is a bounded `run_within`.
    let registered = runtime.run_within(TrayHandle::register(options), Duration::from_secs(5));
    let handle = match registered {
        Ok(Ok(handle)) => handle,
        Ok(Err(err)) => return eprintln!("talos-bar-rs: tray: not registered ({err:?}); the bar runs without it"),
        Err(_) => return eprintln!("talos-bar-rs: tray: registration timed out; the bar runs without it"),
    };
    let mut online = handle.is_online();
    eprintln!("talos-bar-rs: tray: registered (host online: {online})");
    loop {
        while let Some(event) = handle.try_event() {
            if sender.send(act(event)).is_err() {
                return; // The bar is gone.
            }
        }
        if handle.is_online() != online {
            online = !online;
            eprintln!("talos-bar-rs: tray: host {}", if online { "back" } else { "gone" });
        }
        let _ = runtime.run_within(kernal_api::async_engine::sleep(POLL), 2 * POLL);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_map_to_search_tasks_and_quit() {
        assert_eq!(act(TrayEvent::Activate), Act::Search);
        assert_eq!(act(TrayEvent::OpenDashboard), Act::Tasks);
        assert_eq!(act(TrayEvent::Quit), Act::Quit);
    }

    #[test]
    fn the_icon_is_an_accent_disc_with_a_white_bar() {
        let accent = Color::rgb(61, 174, 233);
        let argb = icon(accent);
        let n = usize::from(SIZE);
        assert_eq!(argb.len(), n * n * 4);
        let at = |x: usize, y: usize| &argb[(y * n + x) * 4..(y * n + x) * 4 + 4];
        assert_eq!(at(0, 0), [0, 0, 0, 0], "corners are transparent");
        assert_eq!(at(16, 24), [255, 61, 174, 233]);
        assert_eq!(at(16, 11), [255, 255, 255, 255]);
        let _ = options(accent); // valid, so `expect` cannot fire
    }
}

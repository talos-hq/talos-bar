//! The bar's two requests to talos-searchd (protocol: `searchd/src/main.rs`):
//! `{"op":"status"}` for the status cell and `{"op":"show"}` for the search button. `show`
//! makes searchd broadcast the same `{"event":"show"}` its hotkey does, so the resident
//! popover opens exactly as for Meta+Space; the bar never starts a popover itself.

use kernal_api::platform::ipc::{Endpoint, Stream};
use std::io::{self, BufRead as _, BufReader, Write as _};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const TIMEOUT: Duration = Duration::from_secs(2);
const POLL: Duration = Duration::from_secs(2);

/// What the status cell shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    /// No answer yet.
    Unknown,
    /// searchd is not answering.
    Down,
    /// searchd answers; `subscribers` resident clients (the popover) follow its `show` events.
    Up { subscribers: u64 },
}

impl Status {
    pub fn text(self) -> String {
        match self {
            Self::Unknown => "searchd …".to_owned(),
            Self::Down => "searchd down".to_owned(),
            Self::Up { subscribers: 0 } => "no popover".to_owned(),
            Self::Up { subscribers: 1 } => "popover subscribed".to_owned(),
            Self::Up { subscribers } => format!("popover subscribed ×{subscribers}"),
        }
    }
}

/// One request on a fresh connection; the reply's `subscribers`.
pub fn request(endpoint: &Endpoint, op: &str) -> io::Result<u64> {
    let mut stream = Stream::connect_within(endpoint, TIMEOUT)?;
    stream.set_recv_timeout(Some(TIMEOUT))?;
    stream.set_send_timeout(Some(TIMEOUT))?;
    stream.write_all(format!("{{\"op\":\"{op}\"}}\n").as_bytes())?;
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line)?;
    parse_reply(&line)
}

/// `{"ok":true,"subscribers":N}` → N. A searchd without the op answers `{"error":...}`.
pub fn parse_reply(line: &str) -> io::Result<u64> {
    let reply: serde_json::Value = serde_json::from_str(line).map_err(io::Error::other)?;
    if let Some(error) = reply.get("error") {
        return Err(io::Error::other(format!("talos-searchd: {error}")));
    }
    reply
        .get("subscribers")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| io::Error::other(format!("talos-searchd: unexpected reply {reply}")))
}

/// Poll `status` forever into `cell`; logs only on changes.
pub fn poll_forever(endpoint: &Endpoint, cell: &Arc<Mutex<Status>>) {
    let mut last = Status::Unknown;
    loop {
        let now = match request(endpoint, "status") {
            Ok(subscribers) => Status::Up { subscribers },
            Err(err) => {
                if last != Status::Down {
                    eprintln!("talos-bar-rs: talos-searchd at {}: {err}", endpoint.display());
                }
                Status::Down
            }
        };
        if now != last {
            eprintln!("talos-bar-rs: status: {}", now.text());
            last = now;
        }
        if let Ok(mut slot) = cell.lock() {
            *slot = now;
        }
        std::thread::sleep(POLL);
    }
}

pub fn default_endpoint() -> String {
    let dir = std::env::var_os("XDG_RUNTIME_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    kernal_api::platform::ipc::EndpointAddressCandidates::new(
        Some("talos-searchd".into()),
        Some(dir.join("talos-searchd.sock")),
    )
    .select()
    .unwrap_or_else(|| "talos-searchd".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replies_parse() {
        assert_eq!(parse_reply(r#"{"ok":true,"subscribers":2}"#).expect("ok"), 2);
        assert!(parse_reply(r#"{"error":"unknown variant `show`"}"#).is_err());
        assert!(parse_reply(r#"{"ok":true}"#).is_err());
        assert!(parse_reply("").is_err());
    }

    #[test]
    fn status_text() {
        assert_eq!(Status::Down.text(), "searchd down");
        assert_eq!(Status::Up { subscribers: 0 }.text(), "no popover");
        assert_eq!(Status::Up { subscribers: 1 }.text(), "popover subscribed");
    }
}

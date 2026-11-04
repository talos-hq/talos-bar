//! Where results come from: talos-searchd's line-JSON protocol over kernal-api IPC.

use kernal_api::platform::ipc::{Endpoint, Stream};
use serde::Deserialize;
use std::io::{self, BufRead as _, BufReader, Write as _};
use std::sync::mpsc::Sender;
use std::time::Duration;

const TIMEOUT: Duration = Duration::from_secs(2);

/// One result row, as `talos-searchd` serialises its `Hit`.
#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct Hit {
    /// `app:<id>` or `url:<uri>`; handed back verbatim to `activate`.
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub subtitle: String,
}

/// The popover's view of the search backend.
pub trait Source {
    fn query(&mut self, q: &str) -> io::Result<Vec<Hit>>;
    /// Launch or open the hit; `Ok(false)` when the daemon refused it.
    fn activate(&mut self, id: &str) -> io::Result<bool>;
}

/// A client of `talos-searchd`, connected on first use and reconnected after an error.
pub struct Searchd {
    endpoint: Endpoint,
    conn: Option<(Stream, BufReader<Stream>)>,
}

impl Searchd {
    pub fn new(address: String) -> io::Result<Self> {
        Ok(Self { endpoint: Endpoint::new(address)?, conn: None })
    }

    fn request(&mut self, request: &serde_json::Value) -> io::Result<serde_json::Value> {
        let result = self.request_once(request);
        if result.is_err() {
            self.conn = None;
        }
        result
    }

    fn request_once(&mut self, request: &serde_json::Value) -> io::Result<serde_json::Value> {
        if self.conn.is_none() {
            let stream = Stream::connect_within(&self.endpoint, TIMEOUT)?;
            stream.set_recv_timeout(Some(TIMEOUT))?;
            stream.set_send_timeout(Some(TIMEOUT))?;
            let reader = BufReader::new(stream.try_clone()?);
            self.conn = Some((stream, reader));
        }
        let Some((stream, reader)) = self.conn.as_mut() else {
            unreachable!("connected above");
        };
        let mut line = serde_json::to_vec(request).map_err(io::Error::other)?;
        line.push(b'\n');
        stream.write_all(&line)?;
        let mut reply = String::new();
        if reader.read_line(&mut reply)? == 0 {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "talos-searchd closed the connection"));
        }
        let reply: serde_json::Value = serde_json::from_str(&reply).map_err(io::Error::other)?;
        match reply.get("error").and_then(serde_json::Value::as_str) {
            Some(error) => Err(io::Error::other(format!("talos-searchd: {error}"))),
            None => Ok(reply),
        }
    }
}

impl Source for Searchd {
    fn query(&mut self, q: &str) -> io::Result<Vec<Hit>> {
        let reply = self.request(&serde_json::json!({ "op": "query", "q": q }))?;
        let hits = reply.get("hits").cloned().unwrap_or_default();
        serde_json::from_value(hits).map_err(io::Error::other)
    }

    fn activate(&mut self, id: &str) -> io::Result<bool> {
        let reply = self.request(&serde_json::json!({ "op": "activate", "id": id }))?;
        Ok(reply.get("ok").and_then(serde_json::Value::as_bool).unwrap_or(false))
    }
}

/// Retry delays for the subscription: 250 ms doubling to a 5 s ceiling.
pub fn backoff(failures: u32) -> Duration {
    Duration::from_millis(250u64.saturating_mul(1 << failures.min(5))).min(Duration::from_secs(5))
}

/// How an acknowledged subscription ended.
#[derive(Debug, PartialEq, Eq)]
pub enum Ended {
    /// The daemon closed the stream (restart, or it dropped a lagging subscriber).
    Daemon,
    /// Nobody is listening for `show` any more.
    Receiver,
}

/// One subscription: send `subscribe`, wait for its ack, then forward every `show` event to
/// `shows` until the daemon goes away. `Err` when connecting or the handshake failed, or the
/// stream broke. `on_ack` runs once the daemon has accepted the subscription.
pub fn follow(endpoint: &Endpoint, shows: &Sender<()>, on_ack: impl FnOnce()) -> io::Result<Ended> {
    let mut stream = Stream::connect_within(endpoint, TIMEOUT)?;
    stream.set_send_timeout(Some(TIMEOUT))?;
    stream.set_recv_timeout(Some(TIMEOUT))?;
    stream.write_all(b"{\"op\":\"subscribe\"}\n")?;
    let mut lines = BufReader::new(stream.try_clone()?).lines();
    let ack: serde_json::Value = match lines.next() {
        Some(line) => serde_json::from_str(&line?).map_err(io::Error::other)?,
        None => return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "talos-searchd closed before the subscribe ack")),
    };
    if ack.get("ok").and_then(serde_json::Value::as_bool) != Some(true) {
        return Err(io::Error::other(format!("talos-searchd refused subscribe: {ack}")));
    }
    on_ack();
    // Events arrive only on hotkey presses: wait for them indefinitely.
    stream.set_recv_timeout(None)?;
    for line in lines {
        let event: serde_json::Value = match serde_json::from_str(&line?) {
            Ok(event) => event,
            Err(_) => continue,
        };
        if event.get("event").and_then(serde_json::Value::as_str) == Some("show") && shows.send(()).is_err() {
            return Ok(Ended::Receiver);
        }
    }
    Ok(Ended::Daemon)
}

/// Follow `talos-searchd`'s `show` events forever, reconnecting with [`backoff`] whenever it is
/// absent or restarts. Returns only when `shows` has no receiver.
pub fn subscribe_forever(endpoint: &Endpoint, shows: &Sender<()>) {
    let mut failures = 0u32;
    let mut was_up = None;
    loop {
        let result = follow(endpoint, shows, || {
            failures = 0;
            if was_up != Some(true) {
                eprintln!("talos-popover: subscribed to talos-searchd");
            }
            was_up = Some(true);
        });
        match &result {
            Ok(Ended::Receiver) => return,
            Ok(Ended::Daemon) => eprintln!("talos-popover: talos-searchd went away; reconnecting"),
            Err(err) if was_up != Some(false) => eprintln!("talos-popover: waiting for talos-searchd ({err}); retrying"),
            Err(_) => {}
        }
        if result.is_err() {
            was_up = Some(false);
        }
        std::thread::sleep(backoff(failures));
        failures = failures.saturating_add(1);
    }
}

/// The same per-user endpoint `talos-searchd` picks by default.
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
    use kernal_api::platform::ipc::Listener;

    /// A one-connection peer on a real kernal-api endpoint that answers like talos-searchd.
    fn serve_once(test: &str) -> (String, std::thread::JoinHandle<Vec<serde_json::Value>>) {
        let address = if cfg!(windows) {
            format!("talos-popover-{test}-{}", std::process::id())
        } else {
            let dir = std::env::temp_dir().join(format!("talos-popover-{test}-{}", std::process::id()));
            dir.join("searchd.sock").display().to_string()
        };
        let endpoint = Endpoint::new(address.clone()).expect("endpoint");
        endpoint.ensure_owner_private_parent().expect("private parent");
        endpoint.retire().expect("retire");
        let listener = Listener::bind_owner_only(&endpoint).expect("bind");
        let server = std::thread::spawn(move || {
            let mut stream = listener.accept().expect("accept");
            let mut lines = BufReader::new(stream.try_clone().expect("clone")).lines();
            let mut seen = Vec::new();
            while let Some(Ok(line)) = lines.next() {
                let request: serde_json::Value = serde_json::from_str(&line).expect("json");
                let reply = match request["op"].as_str() {
                    Some("query") => serde_json::json!({ "hits": [
                        { "id": "app:firefox.desktop", "title": "Firefox", "subtitle": "Web Browser", "icon": null, "score": 9 }
                    ]}),
                    Some("activate") => serde_json::json!({ "ok": request["id"] == "app:firefox.desktop" }),
                    _ => serde_json::json!({ "error": "unknown op" }),
                };
                seen.push(request);
                stream.write_all(format!("{reply}\n").as_bytes()).expect("write");
            }
            seen
        });
        (address, server)
    }

    #[test]
    fn speaks_the_searchd_protocol_over_ipc() {
        let (address, server) = serve_once("protocol");
        let mut client = Searchd::new(address).expect("client");
        let hits = client.query("fire").expect("query");
        assert_eq!(hits, vec![Hit { id: "app:firefox.desktop".into(), title: "Firefox".into(), subtitle: "Web Browser".into() }]);
        assert!(client.activate("app:firefox.desktop").expect("activate"));
        assert!(!client.activate("app:nope").expect("activate"));
        drop(client);
        let seen = server.join().expect("server");
        assert_eq!(seen[0], serde_json::json!({ "op": "query", "q": "fire" }));
        assert_eq!(seen.len(), 3);
    }

    /// A peer that acks `subscribe`, then sends `events` lines and hangs up.
    fn serve_subscription(test: &str, events: &'static [&'static str]) -> (Endpoint, std::thread::JoinHandle<String>) {
        let dir = std::env::temp_dir().join(format!("talos-popover-{test}-{}", std::process::id()));
        let endpoint = Endpoint::new(dir.join("searchd.sock").display().to_string()).expect("endpoint");
        endpoint.ensure_owner_private_parent().expect("private parent");
        endpoint.retire().expect("retire");
        let listener = Listener::bind_owner_only(&endpoint).expect("bind");
        let server = std::thread::spawn(move || {
            let mut stream = listener.accept().expect("accept");
            let mut request = String::new();
            BufReader::new(stream.try_clone().expect("clone")).read_line(&mut request).expect("read");
            stream.write_all(b"{\"ok\":true}\n").expect("ack");
            for event in events {
                stream.write_all(format!("{event}\n").as_bytes()).expect("event");
            }
            request
        });
        (endpoint, server)
    }

    #[test]
    fn subscription_forwards_only_show_events_until_the_daemon_hangs_up() {
        let (endpoint, server) = serve_subscription(
            "subscribe",
            &[r#"{"event":"show"}"#, r#"{"event":"dictation","text":"hi"}"#, "not json", r#"{"event":"show"}"#],
        );
        let (tx, rx) = std::sync::mpsc::channel();
        let mut acked = false;
        assert_eq!(follow(&endpoint, &tx, || acked = true).expect("follow"), Ended::Daemon);
        assert!(acked);
        assert_eq!(rx.try_iter().count(), 2);
        assert_eq!(server.join().expect("server").trim(), r#"{"op":"subscribe"}"#);
    }

    #[test]
    fn subscription_stops_when_nobody_listens() {
        let (endpoint, server) = serve_subscription("noreceiver", &[r#"{"event":"show"}"#]);
        let (tx, rx) = std::sync::mpsc::channel();
        drop(rx);
        assert_eq!(follow(&endpoint, &tx, || {}).expect("follow"), Ended::Receiver);
        server.join().expect("server");
    }

    #[test]
    fn backoff_doubles_to_a_ceiling() {
        assert_eq!(backoff(0), Duration::from_millis(250));
        assert_eq!(backoff(1), Duration::from_millis(500));
        assert_eq!(backoff(4), Duration::from_secs(4));
        assert_eq!(backoff(5), Duration::from_secs(5));
        assert_eq!(backoff(u32::MAX), Duration::from_secs(5));
    }

    #[test]
    fn a_missing_daemon_is_an_error_not_empty_results() {
        let dir = std::env::temp_dir().join(format!("talos-popover-absent-{}", std::process::id()));
        let mut client = Searchd::new(dir.join("none.sock").display().to_string()).expect("client");
        assert!(client.query("x").is_err());
    }
}

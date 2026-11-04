//! The real daemon binary answers the popup's protocol on this host's transport: a Unix socket
//! on Linux and macOS, a named pipe on Windows. This is the per-OS proof that talos-searchd runs.

use kernal_api::platform::ipc::{Endpoint, Stream};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command};
use std::time::{Duration, Instant};

struct Daemon(Child);

impl Drop for Daemon {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn unique_endpoint(test: &str) -> String {
    let name = format!("talos-searchd-{test}-{}", std::process::id());
    if cfg!(windows) {
        name
    } else {
        std::env::temp_dir().join(&name).join("searchd.sock").display().to_string()
    }
}

fn start(test: &str) -> (Daemon, Endpoint) {
    let address = unique_endpoint(test);
    let endpoint = Endpoint::new(address.clone()).expect("endpoint");
    // The owner-only listener refuses a parent other users can enter.
    endpoint.ensure_owner_private_parent().expect("private parent");
    let child = Command::new(env!("CARGO_BIN_EXE_talos-searchd"))
        .args(["--no-hotkey", "--endpoint", &address])
        .spawn()
        .expect("spawn talos-searchd");
    (Daemon(child), endpoint)
}

/// Connects once the daemon is listening, or panics after 30 s.
fn connect(endpoint: &Endpoint) -> Stream {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        match Stream::connect_within(endpoint, Duration::from_secs(2)) {
            Ok(stream) => return stream,
            Err(err) if Instant::now() > deadline => panic!("daemon never listened: {err}"),
            Err(_) => std::thread::sleep(Duration::from_millis(100)),
        }
    }
}

fn request(stream: &mut Stream, line: &str) -> serde_json::Value {
    stream.write_all(format!("{line}\n").as_bytes()).expect("write");
    let mut reply = String::new();
    BufReader::new(stream.try_clone().expect("clone"))
        .read_line(&mut reply)
        .expect("read");
    serde_json::from_str(&reply).expect("reply is JSON")
}

#[test]
fn answers_ping_query_and_bad_requests() {
    let (_daemon, endpoint) = start("protocol");
    let mut stream = connect(&endpoint);

    assert_eq!(request(&mut stream, r#"{"op":"ping"}"#), serde_json::json!({ "ok": true }));

    let reply = request(&mut stream, r#"{"op":"query","q":"go/docs"}"#);
    let hits = reply["hits"].as_array().expect("hits");
    assert!(
        hits.iter().any(|hit| hit["id"] == "url:http://go/docs"),
        "a go/ link is always offered, whatever apps this host has: {reply}"
    );

    let reply = request(&mut stream, r#"{"op":"nope"}"#);
    assert!(reply.get("error").is_some(), "unknown ops are reported: {reply}");
}

#[test]
fn lists_this_hosts_installed_apps() {
    let (_daemon, endpoint) = start("catalog");
    let mut stream = connect(&endpoint);
    // Every desktop host ships at least one app whose name contains an "e" (Edge, Finder's
    // "System Settings", a terminal); the catalog must find it through kernal-api.
    let reply = request(&mut stream, r#"{"op":"query","q":"e"}"#);
    let apps = reply["hits"]
        .as_array()
        .expect("hits")
        .iter()
        .filter(|hit| hit["id"].as_str().is_some_and(|id| id.starts_with("app:")))
        .count();
    assert!(apps > 0, "no installed apps found: {reply}");
}

#[test]
fn show_reaches_subscribers_and_status_counts_them() {
    let (_daemon, endpoint) = start("show");
    let mut control = connect(&endpoint);
    assert_eq!(request(&mut control, r#"{"op":"status"}"#)["subscribers"], 0);

    let mut sub = connect(&endpoint);
    assert_eq!(request(&mut sub, r#"{"op":"subscribe"}"#), serde_json::json!({ "ok": true }));
    assert_eq!(request(&mut control, r#"{"op":"status"}"#)["subscribers"], 1);

    let reply = request(&mut control, r#"{"op":"show"}"#);
    assert_eq!(reply, serde_json::json!({ "ok": true, "subscribers": 1 }));
    // A hang guard where the transport has one: Windows named pipes reject I/O timeouts.
    match sub.set_recv_timeout(Some(Duration::from_secs(5))) {
        Err(e) if e.kind() == std::io::ErrorKind::Unsupported => {}
        other => other.expect("timeout"),
    }
    let mut event = String::new();
    BufReader::new(sub.try_clone().expect("clone")).read_line(&mut event).expect("event");
    assert_eq!(serde_json::from_str::<serde_json::Value>(&event).expect("json"), serde_json::json!({ "event": "show" }));
}

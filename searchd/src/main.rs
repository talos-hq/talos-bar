//! talos-searchd: the search daemon behind talos-bar's Super+Space popup.
//!
//! Every OS touchpoint goes through kernal-api: the global hotkey, the app catalog, launching,
//! and the IPC endpoint the Qt popup connects to. The wire protocol is one JSON object per line:
//!
//! - `{"op":"query","q":"fire"}` → `{"hits":[...]}`
//! - `{"op":"activate","id":"app:firefox.desktop"}` → `{"ok":true}`
//! - `{"op":"subscribe"}` → `{"ok":true}`, then `{"event":"show"}` per hotkey press
//! - `{"op":"ping"}` → `{"ok":true}`

mod search;

use kernal_api::app_catalog::{self, AppEntry};
use kernal_api::global_hotkey::{Chord, HotkeyEvent, HotkeyHandle, HotkeyOptions};
use kernal_api::{IpcAsyncListener, IpcEndpoint};
use serde::Deserialize;
use serde_json::json;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt as _, AsyncReadExt as _, AsyncWriteExt as _, BufReader};
use tokio::sync::{Mutex, broadcast};

const CATALOG_TTL: Duration = Duration::from_secs(30);
const MAX_LINE: usize = 4096;

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "lowercase")]
enum Request {
    Query { q: String },
    Activate { id: String },
    Subscribe,
    Ping,
}

struct State {
    apps: Mutex<(Instant, Vec<AppEntry>)>,
    events: broadcast::Sender<&'static str>,
}

impl State {
    async fn apps(&self) -> Vec<AppEntry> {
        let mut cached = self.apps.lock().await;
        if cached.1.is_empty() || cached.0.elapsed() > CATALOG_TTL {
            *cached = (Instant::now(), app_catalog::list_apps().await);
        }
        cached.1.clone()
    }

    async fn activate(&self, id: &str) -> bool {
        if let Some(uri) = id.strip_prefix("url:") {
            return app_catalog::open_uri(uri).await.is_ok();
        }
        let Some(app_id) = id.strip_prefix("app:") else {
            return false;
        };
        let apps = self.apps().await;
        match apps.iter().find(|a| a.id == app_id) {
            Some(app) => app_catalog::launch(app).await.is_ok(),
            None => false,
        }
    }
}

async fn serve<S>(stream: S, state: Arc<State>)
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let (read, mut write) = tokio::io::split(stream);
    let mut lines = BufReader::new(read).take(1 << 20).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if line.len() > MAX_LINE {
            break;
        }
        let reply = match serde_json::from_str::<Request>(&line) {
            Ok(Request::Query { q }) => {
                json!({ "hits": search::rank(&q, &state.apps().await, 12) })
            }
            Ok(Request::Activate { id }) => json!({ "ok": state.activate(&id).await }),
            Ok(Request::Ping) => json!({ "ok": true }),
            Ok(Request::Subscribe) => {
                if write.write_all(b"{\"ok\":true}\n").await.is_err() {
                    return;
                }
                let mut events = state.events.subscribe();
                while let Ok(event) = events.recv().await {
                    let line = format!("{}\n", json!({ "event": event }));
                    if write.write_all(line.as_bytes()).await.is_err() {
                        return;
                    }
                }
                return;
            }
            Err(err) => json!({ "error": err.to_string() }),
        };
        let mut out = serde_json::to_vec(&reply).unwrap_or_default();
        out.push(b'\n');
        if write.write_all(&out).await.is_err() {
            return;
        }
    }
}

/// The per-user endpoint: a socket in the runtime dir, or a named pipe where that is the transport.
fn default_endpoint() -> String {
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

async fn hotkey_loop(chord: String, events: broadcast::Sender<&'static str>) {
    let options = match Chord::parse(&chord)
        .and_then(|c| HotkeyOptions::new("talos-search", "Open talos search", c))
    {
        Ok(options) => options,
        Err(err) => return eprintln!("talos-searchd: bad hotkey {chord}: {err:?}"),
    };
    let mut handle = match HotkeyHandle::register(options).await {
        Ok(handle) => handle,
        Err(err) => return eprintln!("talos-searchd: hotkey {chord} unavailable: {err:?}"),
    };
    eprintln!("talos-searchd: hotkey {chord} registered");
    while handle.is_active() {
        if let Some(HotkeyEvent::Pressed) = handle.next_event_within(Duration::from_secs(3600)).await {
            let _ = events.send("show");
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> std::io::Result<()> {
    let mut endpoint = default_endpoint();
    let mut hotkey = Some("Meta+Space".to_owned());
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--endpoint" => endpoint = args.next().expect("--endpoint takes a value"),
            "--hotkey" => hotkey = Some(args.next().expect("--hotkey takes a chord")),
            "--no-hotkey" => hotkey = None,
            "--print-endpoint" => {
                println!("{endpoint}");
                return Ok(());
            }
            _ => {
                eprintln!("usage: talos-searchd [--endpoint E] [--hotkey CHORD | --no-hotkey]");
                std::process::exit(2);
            }
        }
    }
    let (events, _) = broadcast::channel(16);
    let state = Arc::new(State {
        apps: Mutex::new((Instant::now(), Vec::new())),
        events: events.clone(),
    });
    let endpoint = IpcEndpoint::new(endpoint)?;
    endpoint.retire()?;
    let listener = IpcAsyncListener::bind_owner_only(&endpoint)?;
    eprintln!("talos-searchd: listening on {}", endpoint.display());
    if let Some(chord) = hotkey {
        tokio::spawn(hotkey_loop(chord, events));
    }
    loop {
        let stream = listener.accept().await?;
        tokio::spawn(serve(stream, Arc::clone(&state)));
    }
}

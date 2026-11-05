//! The disk tree as a web page.
//!
//! The bar's panel is a list; the full answer to "where did it go" is a tree
//! with indentation and collapsible parents, which egui widgets in the dock
//! cannot do without growing the bar's own layout vocabulary. So the click
//! opens one self-contained HTML page in the session's browser: the same rows
//! the panel would show, nested, with a size bar per row.
//!
//! Cleanup lives here too, and it is trash-first: "trash" moves the entry into
//! the desktop's XDG trash (recoverable from the file manager), after a
//! confirmation, and only for paths inside the scan root. A single click in a
//! dock must never be irreversible.

use crate::disk::{self, Pressure, Row};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const PREFIX: &str = "TALOS_BAR_DISK_PREFIX";

/// The page for these rows and this reading.
pub fn render(rows: &[Row], pressure: Option<Pressure>) -> String {
    let mut body = String::new();
    if rows.is_empty() {
        body.push_str(&format!(
            "<p class=\"empty\">No directory above {}.</p>",
            disk::human(disk::MIN_BYTES)
        ));
    }
    // A flat list drawn as a tree: `depth` comes from the scan, so a parent's
    // children are the rows that follow it until the indent drops back.
    for row in rows {
        let indent = row.depth.saturating_sub(1) * 18;
        let path = row.path.to_string_lossy();
        body.push_str(&format!(
            "<div class=\"row\" style=\"padding-left:{indent}px\" title=\"{}\" data-path=\"{}\">\
               <span class=\"name\">{}</span>\
               <span class=\"bar\"><i style=\"width:{:.1}%\\\"></i></span>\
               <span class=\"size\">{}</span>\
               <button class=\"trash\" title=\"Move to the trash\">trash</button>\
             </div>",
            escape(&row.name),
            escape(&path),
            escape(&row.name),
            share(row.bytes, rows),
            disk::human(row.bytes),
        ));
    }
    let totals = pressure.map_or_else(
        || "usage unavailable".to_owned(),
        |p| {
            format!(
                "{} used of {} · {} free",
                disk::human(p.used()),
                disk::human(p.total),
                disk::human(p.free),
            )
        },
    );
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">\
         <title>Disk — where the space went</title>\
         <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\
         <style>{style}</style></head><body>\
         <header><h1>Disk</h1><p>{totals} · mounted filesystems excluded · \
         cleanup moves entries to the trash</p></header>\
         {body}<script>{script}</script></body></html>",
        style = STYLE,
        script = SCRIPT,
        totals = escape(&totals),
    )
}

/// A row's size as a share of the biggest row, for the bar.
fn share(bytes: u64, rows: &[Row]) -> f64 {
    let largest = rows.iter().map(|row| row.bytes).max().unwrap_or(0);
    if largest == 0 {
        return 0.0;
    }
    #[allow(clippy::cast_precision_loss, reason = "a bar width keeps three digits")]
    let percent = bytes as f64 / largest as f64 * 100.0;
    percent.min(100.0)
}

fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            other => out.push(other),
        }
    }
    out
}

const STYLE: &str = "\
:root{color-scheme:dark}\
body{margin:0;padding:24px;font:14px/1.5 system-ui,sans-serif;background:#111318;color:#e6e8ee}\
header{margin-bottom:16px}h1{margin:0;font-size:18px;font-weight:600}\
p{margin:4px 0 0;color:#9aa3b2}\
.row{display:grid;grid-template-columns:minmax(160px,1fr) 180px 90px 64px;gap:12px;\
align-items:center;padding:4px 8px;border-radius:6px}\
.row:hover{background:#1b1f27}\
.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
.bar{height:6px;border-radius:3px;background:#232833;overflow:hidden}\
.bar i{display:block;height:100%;background:linear-gradient(90deg,#3daee9,#8b5cf6)}\
.size{text-align:right;color:#9aa3b2;font-variant-numeric:tabular-nums}\
.empty{padding:16px 8px}\
.trash{justify-self:end;background:#2a1f22;color:#ff8f84;border:1px solid #5a2a26;\
border-radius:6px;padding:2px 8px;font:inherit;cursor:pointer}\
.trash:hover{background:#3a2528}\
.trash[disabled]{opacity:.4;cursor:default}";

/// Ask before moving anything, then do it and reload.
const SCRIPT: &str = r#"
document.querySelectorAll('.trash').forEach(function (button) {
  button.addEventListener('click', async function () {
    const row = button.closest('.row');
    const path = row.dataset.path;
    if (!confirm('Move to the trash?\n\n' + path)) { return; }
    button.disabled = true;
    try {
      const response = await fetch('/trash', { method: 'POST', body: new URLSearchParams({ path: path }) });
      const text = await response.text();
      if (!response.ok) { throw new Error(text); }
      row.remove();
    } catch (error) {
      button.disabled = false;
      alert('Not moved: ' + error.message);
    }
  });
});
"#;

/// Serve the page and open it; returns the URL.
pub fn open(rows: &[Row], pressure: Option<Pressure>, root: &Path) -> io::Result<String> {
    let url = serve(render(rows, pressure), root)?;
    let browser = std::env::var_os(PREFIX)
        .map(|prefix| prefix.to_string_lossy().into_owned())
        .unwrap_or_else(|| "xdg-open".to_owned());
    Command::new(&browser)
        .arg(&url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    Ok(url)
}

/// Serve `page` on a free loopback port for the life of the process.
///
/// `root` is the scan's root: the trash endpoint refuses any path outside it,
/// so a page cannot be talked into moving something it was not asked about.
pub fn serve(page: String, root: &Path) -> io::Result<String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let url = format!("http://127.0.0.1:{}/", listener.local_addr()?.port());
    let root = root.to_path_buf();
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = respond(stream, &page, &root);
        }
    });
    Ok(url)
}

fn respond(mut stream: TcpStream, page: &str, root: &Path) -> io::Result<()> {
    let mut raw = Vec::new();
    let mut chunk = [0u8; 4096];
    // Read the head, then exactly Content-Length bytes: a form POST is not
    // guaranteed to arrive in the first segment.
    let (head, mut body) = loop {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            break (String::from_utf8_lossy(&raw).into_owned(), Vec::new());
        }
        raw.extend_from_slice(&chunk[..read]);
        if let Some(at) = raw.windows(4).position(|window| window == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&raw[..at]).into_owned();
            let body = raw[at + 4..].to_vec();
            break (head, body);
        }
    };
    let mut parts = head.split_whitespace();
    let method = parts.next().unwrap_or("").to_owned();
    let path = parts.next().map(str::to_owned);
    let wanted: usize = head
        .lines()
        .find_map(|line| line.strip_prefix("Content-Length:").map(str::trim))
        .and_then(|value| value.parse().ok())
        .unwrap_or(0);
    while body.len() < wanted {
        let read = stream.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..read]);
    }

    let (status, text) = match (method.as_str(), path.as_deref()) {
        ("POST", Some("/trash")) => {
            let form = String::from_utf8_lossy(&body).into_owned();
            match trash(&form, root) {
                Ok(moved) => ("200 OK", format!("moved to {}", moved.display())),
                Err(error) => ("409 Conflict", error),
            }
        }
        (_, Some("/" | "/index.html")) => ("200 OK", page.to_owned()),
        (_, Some(_)) => ("404 Not Found", "not found".to_owned()),
        // A request line with no path is not a page request at all.
        (_, None) => ("400 Bad Request", "bad request".to_owned()),
    };
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{text}",
        text.len()
    )?;
    stream.flush()
}

/// Move the `path=` form field into the XDG trash. Returns the trashed location.
fn trash(form: &str, root: &Path) -> Result<PathBuf, String> {
    if std::env::var("TALOS_BAR_DISK_TRASH").is_ok_and(|value| value.eq_ignore_ascii_case("off")) {
        return Err("trash is disabled (TALOS_BAR_DISK_TRASH=off)".to_owned());
    }
    let Some(encoded) = form.split('&').find_map(|pair| pair.strip_prefix("path=")) else {
        return Err("no path".to_owned());
    };
    let requested = percent_decode(encoded);
    // Canonicalise both sides: the page sends what the scan reported, but the
    // check must not be fooled by `..` or a symlinked parent.
    let root = root.canonicalize().map_err(|error| format!("root: {error}"))?;
    let target = PathBuf::from(&requested)
        .canonicalize()
        .map_err(|error| format!("{requested}: {error}"))?;
    if target == root {
        return Err("refusing to trash the scan root".to_owned());
    }
    if !target.starts_with(&root) {
        return Err(format!("{} is outside {}", target.display(), root.display()));
    }
    move_to_trash(&target)
}

/// XDG trash: `files/<name>` plus `info/<name>.trashinfo`, collision-safe.
fn move_to_trash(target: &Path) -> Result<PathBuf, String> {
    let data = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share")))
        .ok_or("no XDG_DATA_HOME and no HOME")?
        .join("Trash");
    let files = data.join("files");
    let info = data.join("info");
    std::fs::create_dir_all(&files).map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&info).map_err(|error| error.to_string())?;

    let name = target
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .ok_or("no file name")?;
    let mut candidate = name.clone();
    let mut counter = 2;
    while files.join(&candidate).exists() || info.join(format!("{candidate}.trashinfo")).exists() {
        candidate = format!("{name}.{counter}");
        counter += 1;
    }
    let info_path = info.join(format!("{candidate}.trashinfo"));
    std::fs::write(
        &info_path,
        format!(
            "[Trash Info]\nPath={}\nDeletionDate={}\n",
            percent_encode(&target.to_string_lossy()),
            timestamp(),
        ),
    )
    .map_err(|error| error.to_string())?;
    let moved = files.join(&candidate);
    if let Err(error) = std::fs::rename(target, &moved) {
        let _ = std::fs::remove_file(&info_path);
        return Err(format!("move: {error}"));
    }
    Ok(moved)
}

/// `YYYY-MM-DDTHH:MM:SS`, the trashinfo date format, from the system clock.
fn timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = (now / 86_400) as i64;
    let secs = now % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        secs / 3600,
        secs / 60 % 60,
        secs % 60
    )
}

/// Howard Hinnant's `civil_from_days`: days since the epoch to a calendar date.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    #[allow(clippy::cast_sign_loss, reason = "the era offset keeps doe positive")]
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    #[allow(clippy::cast_possible_wrap, reason = "yoe is a year within an era")]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    #[allow(clippy::cast_possible_truncation, reason = "day and month are small")]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    #[allow(clippy::cast_possible_truncation, reason = "day and month are small")]
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn percent_encode(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'/' | b'-' | b'.' | b'_' | b'~' => {
                out.push(char::from(byte));
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(if bytes[index] == b'+' { b' ' } else { bytes[index] });
        index += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The tests share one process environment, so every test that sets a
    /// variable holds this lock; otherwise one test's `remove_var` lands
    /// between another's `set_var` and its use.
    static ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        ENV.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn rows() -> Vec<Row> {
        vec![
            Row { depth: 1, name: "/home".to_owned(), bytes: 800, path: "/home".into() },
            Row { depth: 2, name: "/home/<script>".to_owned(), bytes: 400, path: "/home/<script>".into() },
            Row { depth: 1, name: "/var".to_owned(), bytes: 200, path: "/var".into() },
        ]
    }

    #[test]
    fn names_are_escaped_and_indented_by_depth() {
        let page = render(&rows(), Some(Pressure { total: 1_000, free: 50 }));
        assert!(!page.contains("/home/<script>"), "a name must never become markup");
        assert!(page.contains("&lt;script&gt;"));
        assert!(page.contains("padding-left:18px"), "depth 2 indents once");
        assert!(page.contains("padding-left:0px"), "depth 1 does not indent");
        assert!(page.contains("950 B used of 1000 B · 50 B free"));
        assert!(page.contains("class=\"trash\""), "every row can be moved to the trash");
    }

    #[test]
    fn the_biggest_row_fills_the_bar() {
        let page = render(&rows(), None);
        assert!(page.contains("width:100.0%"), "800 B is the largest row");
        assert!(page.contains("width:50.0%"), "400 B is half of it");
        assert!(page.contains("usage unavailable"));
    }

    #[test]
    fn an_empty_scan_says_so() {
        let page = render(&[], None);
        assert!(page.contains("No directory above 64.0 MiB"));
        assert!(!page.contains("class=\"row\""));
    }

    fn serve_once(page: &str, root: &Path) -> (u16, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let page = page.to_owned();
        let root = root.to_path_buf();
        let handle = std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let _ = respond(stream, &page, &root);
            }
        });
        (port, handle)
    }

    fn request(port: u16, text: &str) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
        stream.write_all(text.as_bytes()).expect("write");
        let mut response = String::new();
        std::io::BufReader::new(&stream).read_to_string(&mut response).expect("read");
        response
    }

    #[test]
    fn the_server_answers_the_root_and_rejects_anything_else() {
        let page = render(&rows(), None);
        let (port, _handle) = serve_once(&page, Path::new("/"));
        let response = request(port, "GET / HTTP/1.1\r\nHost: x\r\n\r\n");
        assert!(response.starts_with("HTTP/1.1 200 OK"), "{response:.60}");
        assert!(response.contains("<!doctype html>"), "{response:.60}");
        let response = request(port, "GET /elsewhere HTTP/1.1\r\nHost: x\r\n\r\n");
        assert!(response.starts_with("HTTP/1.1 404 Not Found"), "{response:.60}");
    }

    #[test]
    fn a_request_with_no_path_does_not_panic() {
        let (port, _handle) = serve_once("x", Path::new("/"));
        let response = request(port, "garbage\r\n\r\n");
        assert!(response.contains("400 Bad Request"), "{response:.60}");
    }

    #[test]
    fn opening_hands_the_url_to_the_configured_browser() {
        let _env = env_lock();
        let dir = std::env::temp_dir().join(format!("talos-diskview-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let record = dir.join("url");
        let opener = dir.join("opener.sh");
        std::fs::write(&opener, format!("#!/bin/sh\nprintf '%s' \"$1\" > {}\n", record.display()))
            .expect("script");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&opener, std::fs::Permissions::from_mode(0o755)).expect("chmod");
        }
        // SAFETY: this test is the only reader of the variable in this binary.
        unsafe { std::env::set_var(PREFIX, &opener) };
        let url = open(&rows(), None, Path::new("/")).expect("open");
        assert!(url.starts_with("http://127.0.0.1:"), "{url}");
        let mut waited = 0;
        while !record.exists() && waited < 200 {
            std::thread::sleep(std::time::Duration::from_millis(25));
            waited += 1;
        }
        assert_eq!(std::fs::read_to_string(&record).expect("record"), url);
        unsafe { std::env::remove_var(PREFIX) };
    }

    /// A temp root, a trash under it, and a file to move.
    fn sandbox(name: &str) -> (PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("talos-trash-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("root");
        let data = base.join("data");
        std::fs::create_dir_all(root.join("junk")).expect("root");
        std::fs::create_dir_all(&data).expect("data");
        (root, data)
    }

    #[test]
    fn trashing_moves_the_file_and_records_where_it_came_from() {
        let _env = env_lock();
        let (root, data) = sandbox("moves");
        let junk = root.join("junk/big.bin");
        std::fs::write(&junk, b"x").expect("file");
        // SAFETY: this test owns these variables for its own duration.
        unsafe { std::env::set_var("XDG_DATA_HOME", &data) };
        let moved = trash(&format!("path={}", percent_encode(&junk.to_string_lossy())), &root)
            .expect("trashed");
        assert!(!junk.exists(), "the original is gone from its directory");
        assert!(moved.exists(), "and it is in the trash");
        assert!(moved.starts_with(data.join("Trash/files")));
        let info = data.join("Trash/info/big.bin.trashinfo");
        let info = std::fs::read_to_string(info).expect("trashinfo");
        assert!(info.starts_with("[Trash Info]\n"), "{info}");
        assert!(info.contains(&format!("Path={}", percent_encode(&junk.to_string_lossy()))), "{info}");
        unsafe { std::env::remove_var("XDG_DATA_HOME") };
    }

    #[test]
    fn trashing_refuses_paths_outside_the_scan_root() {
        let _env = env_lock();
        let (root, data) = sandbox("outside");
        let outside = data.join("elsewhere.bin");
        std::fs::write(&outside, b"x").expect("file");
        unsafe { std::env::set_var("XDG_DATA_HOME", &data) };
        let error = trash(&format!("path={}", percent_encode(&outside.to_string_lossy())), &root)
            .expect_err("refused");
        assert!(error.contains("outside"), "{error}");
        assert!(outside.exists(), "a refused path is untouched");
        unsafe { std::env::remove_var("XDG_DATA_HOME") };
    }

    #[test]
    fn trashing_refuses_the_root_itself() {
        let _env = env_lock();
        let (root, data) = sandbox("root");
        unsafe { std::env::set_var("XDG_DATA_HOME", &data) };
        let error = trash(&format!("path={}", percent_encode(&root.to_string_lossy())), &root)
            .expect_err("refused");
        assert!(error.contains("scan root"), "{error}");
        assert!(root.exists());
        unsafe { std::env::remove_var("XDG_DATA_HOME") };
    }

    #[test]
    fn the_trash_kill_switch_is_honoured() {
        let _env = env_lock();
        let (root, data) = sandbox("off");
        let junk = root.join("junk/x.bin");
        std::fs::write(&junk, b"x").expect("file");
        unsafe {
            std::env::set_var("XDG_DATA_HOME", &data);
            std::env::set_var("TALOS_BAR_DISK_TRASH", "off");
        }
        let error = trash(&format!("path={}", percent_encode(&junk.to_string_lossy())), &root)
            .expect_err("disabled");
        assert!(error.contains("disabled"), "{error}");
        assert!(junk.exists());
        unsafe {
            std::env::remove_var("TALOS_BAR_DISK_TRASH");
            std::env::remove_var("XDG_DATA_HOME");
        }
    }

    #[test]
    fn the_trash_endpoint_is_wired_to_the_post_route() {
        let _env = env_lock();
        let (root, data) = sandbox("post");
        let junk = root.join("junk/posted.bin");
        std::fs::write(&junk, b"x").expect("file");
        unsafe { std::env::set_var("XDG_DATA_HOME", &data) };
        let body = format!("path={}", percent_encode(&junk.to_string_lossy()));
        let (port, _handle) = serve_once("page", &root);
        let response = request(
            port,
            &format!(
                "POST /trash HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            ),
        );
        assert!(response.starts_with("HTTP/1.1 200 OK"), "{response:.80}");
        assert!(!junk.exists(), "the POST moved it");
        unsafe { std::env::remove_var("XDG_DATA_HOME") };
    }

    #[test]
    fn percent_round_trips_a_path_with_spaces_and_quotes() {
        let path = "/tmp/a b\"c'd%";
        assert_eq!(percent_decode(&percent_encode(path)), path);
        assert!(percent_encode(path).contains("%20"));
    }
}

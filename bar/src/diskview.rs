//! The disk tree as a web page.
//!
//! The bar's panel is a list; the full answer to "where did it go" is a tree
//! with indentation and collapsible parents, which egui widgets in the dock
//! cannot do without growing the bar's own layout vocabulary. So the click
//! opens one self-contained HTML page in the session's browser: the same rows
//! the panel would show, nested, with a size bar per row.
//!
//! It is served on loopback for as long as the bar runs, and it is read-only:
//! turning a size list into a deletion needs its own confirmation path, and a
//! single click in a dock is not that path.

use crate::disk::{self, Pressure, Row};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
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
        body.push_str(&format!(
            "<div class=\"row\" style=\"padding-left:{indent}px\" title=\"{}\">\
               <span class=\"name\">{}</span>\
               <span class=\"bar\"><i style=\"width:{:.1}%\"></i></span>\
               <span class=\"size\">{}</span>\
             </div>",
            escape(&row.name),
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
         <header><h1>Disk</h1><p>{totals} · mounted filesystems excluded · read-only</p></header>\
         {body}</body></html>",
        style = STYLE,
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
.row{display:grid;grid-template-columns:minmax(160px,1fr) 180px 90px;gap:12px;\
align-items:center;padding:4px 8px;border-radius:6px}\
.row:hover{background:#1b1f27}\
.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\
.bar{height:6px;border-radius:3px;background:#232833;overflow:hidden}\
.bar i{display:block;height:100%;background:linear-gradient(90deg,#3daee9,#8b5cf6)}\
.size{text-align:right;color:#9aa3b2;font-variant-numeric:tabular-nums}\
.empty{padding:16px 8px}";

/// Serve the page and open it; returns the URL.
pub fn open(rows: &[Row], pressure: Option<Pressure>) -> io::Result<String> {
    let url = serve(render(rows, pressure))?;
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
pub fn serve(page: String) -> io::Result<String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let url = format!("http://127.0.0.1:{}/", listener.local_addr()?.port());
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = respond(stream, &page);
        }
    });
    Ok(url)
}

fn respond(mut stream: TcpStream, page: &str) -> io::Result<()> {
    let mut request = [0u8; 1024];
    let read = stream.read(&mut request)?;
    let request = String::from_utf8_lossy(&request[..read]);
    let (status, body) = match request.split_whitespace().nth(1) {
        Some("/" | "/index.html") => ("200 OK", page),
        Some(_) => ("404 Not Found", "not found"),
        // A request line with no path is not a page request at all.
        None => ("400 Bad Request", "bad request"),
    };
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )?;
    stream.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows() -> Vec<Row> {
        vec![
            Row { depth: 1, name: "/home".to_owned(), bytes: 800 },
            Row { depth: 2, name: "/home/<script>".to_owned(), bytes: 400 },
            Row { depth: 1, name: "/var".to_owned(), bytes: 200 },
        ]
    }

    #[test]
    fn names_are_escaped_and_indented_by_depth() {
        let page = render(&rows(), Some(Pressure { total: 1_000, free: 50 }));
        assert!(!page.contains("<script>"), "a name must never become markup");
        assert!(page.contains("&lt;script&gt;"));
        assert!(page.contains("padding-left:18px"), "depth 2 indents once");
        assert!(page.contains("padding-left:0px"), "depth 1 does not indent");
        assert!(page.contains("950 B used of 1000 B · 50 B free"));
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

    #[test]
    fn the_server_answers_the_root_and_rejects_anything_else() {
        let page = render(&rows(), None);
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let port = listener.local_addr().expect("addr").port();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let _ = respond(stream, &page);
            }
        });

        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
        stream.write_all(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n").expect("write");
        let mut response = String::new();
        std::io::BufReader::new(&stream).read_to_string(&mut response).expect("read");
        assert!(response.starts_with("HTTP/1.1 200 OK"), "{response:.60}");
        assert!(response.contains("<!doctype html>"));

        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
        stream.write_all(b"GET /elsewhere HTTP/1.1\r\nHost: x\r\n\r\n").expect("write");
        let mut response = String::new();
        std::io::BufReader::new(&stream).read_to_string(&mut response).expect("read");
        assert!(response.starts_with("HTTP/1.1 404 Not Found"), "{response:.60}");
    }

    #[test]
    fn opening_hands_the_url_to_the_configured_browser() {
        // Point the opener at a command that records its argument.
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
        // SAFETY: tests in this binary do not read this variable concurrently.
        unsafe { std::env::set_var(PREFIX, &opener) };
        let url = open(&rows(), None).expect("open");
        assert!(url.starts_with("http://127.0.0.1:"), "{url}");
        let mut waited = 0;
        while !record.exists() && waited < 200 {
            std::thread::sleep(std::time::Duration::from_millis(25));
            waited += 1;
        }
        assert_eq!(std::fs::read_to_string(&record).expect("record"), url);
        unsafe { std::env::remove_var(PREFIX) };
    }

    #[test]
    fn a_request_with_no_path_does_not_panic() {
        let page = "x".to_owned();
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let port = listener.local_addr().expect("addr").port();
        std::thread::spawn(move || {
            if let Some(stream) = listener.incoming().flatten().next() {
                let _ = respond(stream, &page);
            }
        });
        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
        stream.write_all(b"garbage\r\n\r\n").expect("write");
        let mut response = String::new();
        std::io::BufReader::new(&stream).read_to_string(&mut response).expect("read");
        assert!(response.contains("400 Bad Request"), "{response:.60}");
    }
}

"""Disk tree view: where the space went, as a page in the browser.

The scanner is `jettison` (a Rust binary that walks the filesystem with
`getdents64`+`statx` on Linux, `getattrlistbulk` on macOS and the raw NTFS MFT
on Windows), so a whole root is a few seconds rather than minutes. This module
turns its JSON tree into one self-contained HTML page and serves it on loopback.

Why a page instead of an embedded view: the bar already runs a Qt Quick
scene, and a WebEngine view would add a second browser runtime to the Nix
package just to draw a list. A loopback page opens in whatever browser the
session already has, survives the bar restarting, and can be reloaded while a
scan is still settling.

The page is read-only. Nothing here deletes anything: a size list is evidence,
and turning evidence into an action needs its own confirmation path.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Depth and size cut keep the page to the directories a human would act on;
# deeper than six levels is a file manager, not a cleanup decision.
DEFAULT_DEPTH = 6
DEFAULT_MIN_SIZE = 8 * 1024 * 1024
SCAN_TIMEOUT = 600.0


class ScannerMissing(RuntimeError):
    """`jettison-cli` is not on PATH, so there is nothing to render."""


@dataclass(frozen=True)
class Tree:
    """One scan: totals plus the nested children the page draws."""

    root: str
    allocated: int
    apparent: int
    entries: int
    errors: int
    seconds: float
    node: dict[str, Any]
    min_size: int = 0

    @property
    def gigabytes(self) -> float:
        return self.allocated / 1_000_000_000


def scanner() -> str:
    """Path to the scanner binary.

    `TALOS_BAR_JETTISON` wins so a checkout can be tested without installing
    the wheel; otherwise the bundled binary from the `jettison` wheel.
    """
    override = os.environ.get("TALOS_BAR_JETTISON")
    if override:
        return override
    found = shutil.which("jettison-cli")
    if found is None:
        raise ScannerMissing(
            "jettison-cli is not on PATH; install the `jettison` wheel or set "
            "TALOS_BAR_JETTISON=/path/to/jettison-cli"
        )
    return found


def scan(
    root: str = "/",
    depth: int = DEFAULT_DEPTH,
    min_size: int = DEFAULT_MIN_SIZE,
    one_filesystem: bool = True,
    timeout: float = SCAN_TIMEOUT,
) -> Tree:
    """Scan `root` and return its tree.

    `--one-filesystem` is the "ignore mounted drives" rule: the walk stays on
    the root's own filesystem, so `/home` on a separate disk is not counted
    against `/` and a network or FUSE mount is never traversed.
    """
    command = [scanner(), root, "--json", "--depth", str(depth)]
    if min_size:
        command += ["--min-size", str(min_size)]
    if one_filesystem:
        command.append("--one-filesystem")
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"scanner produced no output (exit {result.returncode})")
    payload = json.loads(result.stdout)
    return Tree(
        root=payload["root"]["path"],
        allocated=int(payload["allocated"]),
        apparent=int(payload["apparent"]),
        entries=int(payload["entries"]),
        errors=int(payload.get("errors", 0)),
        seconds=float(payload.get("seconds", 0.0)),
        node=payload["root"],
        min_size=min_size,
    )


def human(size: int) -> str:
    units = ((1 << 40, "TiB"), (1 << 30, "GiB"), (1 << 20, "MiB"), (1 << 10, "KiB"))
    for factor, suffix in units:
        if size >= factor:
            return f"{size / factor:.1f} {suffix}"
    return f"{size} B"


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = [c for c in node.get("children", []) if isinstance(c, dict)]
    children.sort(key=lambda c: int(c.get("allocated", 0)), reverse=True)
    return children


def render(tree: Tree) -> str:
    """One HTML document: a collapsed tree with a size bar per row."""
    # The root is the widest row, so it is the denominator; a child can never
    # exceed it (a child's size is part of its parent's total).
    largest = max(int(tree.node.get("allocated", 0)), 1)

    def row(node: dict[str, Any], depth: int) -> str:
        allocated = int(node.get("allocated", 0))
        entitlement = min(allocated / largest * 100, 100.0)
        name = html.escape(str(node.get("path", "")).rstrip("/").rsplit("/", 1)[-1] or "/")
        children = _children(node)
        label = (
            f'<span class="n">{name}</span>'
            f'<span class="bar"><i style="width:{entitlement:.1f}%"></i></span>'
            f'<span class="s">{human(allocated)}</span>'
        )
        if not children:
            return f'<li class="file" style="--d:{depth}">{label}</li>'
        body = "".join(row(child, depth + 1) for child in children)
        return (
            f'<li style="--d:{depth}"><details>'
            f"<summary>{label}</summary><ul>{body}</ul></details></li>"
        )

    body = row(tree.node, 0)
    if not _children(tree.node):
        body = (
            f'<li class="empty">No directory above {human(tree.min_size)} '
            f"({tree.entries:,} files scanned).</li>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(tree.root)} — {human(tree.allocated)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0; padding:24px; font:14px/1.5 ui-sans-serif,system-ui,sans-serif;
        background:#111318; color:#e6e8ee; }}
header {{ position:sticky; top:0; padding-bottom:16px; background:#111318; }}
h1 {{ margin:0 0 4px; font-size:18px; font-weight:600; }}
p {{ margin:0; color:#9aa3b2; }}
ul {{ list-style:none; margin:0; padding:0; }}
li {{ margin:0; }}
details > summary {{ cursor:pointer; list-style:none; }}
details > summary::-webkit-details-marker {{ display:none; }}
summary, .file > span:first-child, .file {{ padding-left:calc(var(--d, 0) * 18px); }}
li > details > summary, .file {{ display:grid; grid-template-columns:minmax(160px,1fr) 140px 90px;
        gap:12px; align-items:center; padding:4px 8px; border-radius:6px; }}
li > details > summary:hover, .file:hover {{ background:#1b1f27; }}
.n {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar {{ height:6px; border-radius:3px; background:#232833; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:linear-gradient(90deg,#4c8bf5,#8b5cf6); }}
.empty {{ padding:16px 8px; color:#9aa3b2; }}
.s {{ text-align:right; color:#9aa3b2; font-variant-numeric:tabular-nums; }}
.file {{ display:grid; grid-template-columns:minmax(160px,1fr) 140px 90px; gap:12px; }}
.file .n {{ padding-left:0; }}
</style></head>
<body>
<header>
  <h1>{html.escape(tree.root)} — {human(tree.allocated)} used</h1>
  <p>{tree.entries:,} entries in {tree.seconds:.1f}s{f", {tree.errors} unreadable" if tree.errors else ""}.
     Mounted filesystems are excluded. Read-only: nothing is deleted here.</p>
</header>
<ul>{body}</ul>
</body></html>
"""


def serve(tree: Tree, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, str]:
    """Serve `tree` on a free loopback port; returns the server and its URL."""
    page = render(tree).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, format: str, *args: object) -> None:  # pylint: disable=redefined-builtin
            """Silence the access log: the bar's stderr is for bar problems."""
            del format, args

    server = ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}/"


def open_view(root: str = "/") -> str:
    """Scan, serve and open the page; returns the URL that was opened."""
    server, url = serve(scan(root))
    opened = webbrowser.open(url)
    if not opened:
        raise RuntimeError(f"no browser accepted {url}")
    # The server is a daemon thread; keeping the object alive is enough for the
    # page to keep loading after this call returns.
    _SERVERS.append(server)
    return url


_SERVERS: list[ThreadingHTTPServer] = []


def write_page(tree: Tree, path: Path) -> Path:
    """Write the same page to a file (tests, and `--out` from a shell)."""
    path.write_text(render(tree), encoding="utf-8")
    return path


if __name__ == "__main__":  # pragma: no cover - manual entry point
    print(open_view(os.environ.get("TALOS_BAR_DISK_ROOT", "/")))

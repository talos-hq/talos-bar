//! Disk pressure: a tab that exists only when the root filesystem is nearly
//! full, and the tree that says where the space went.
//!
//! Two halves, deliberately separate:
//!
//! * [`Pressure`] is one `statvfs` reading through `kernal_api`, cheap enough to
//!   take on every bar refresh. The tab is drawn only when usage is over the
//!   threshold, so the bar carries a warning only when there is one.
//! * [`Tree`] is a whole-root scan through `kernal_api::fs_scan` — seconds of
//!   work, so it never runs on the UI thread. The panel shows what the last
//!   scan found and asks for a new one when it opens.
//!
//! Mounted filesystems are excluded (`one_filesystem`): the numbers describe
//! one disk, and a network or FUSE mount is neither counted nor walked.

use kernal_api::fs_scan::{self, EntryKind, ScanEntry, ScanOptions};
use kernal_api::platform::resources;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Usage percentage at which the tab appears (env `TALOS_BAR_DISK_THRESHOLD`).
/// 95 is the owner's number: high enough that the tab is rare, low enough that
/// there is still room to act before a write fails.
pub const DEFAULT_THRESHOLD: u8 = 95;
/// Rows the panel shows; deeper than this is a file manager, not a decision.
pub const MAX_DEPTH: usize = 4;
/// Directories smaller than this are not worth a row.
pub const MIN_BYTES: u64 = 64 * 1024 * 1024;
/// Rows in the panel.
pub const MAX_ROWS: usize = 40;
/// A finished scan is used for this long before opening the panel starts another.
const FRESH_FOR: Duration = Duration::from_secs(120);

/// One filesystem reading.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pressure {
    pub total: u64,
    pub free: u64,
}

impl Pressure {
    pub fn used(self) -> u64 {
        self.total.saturating_sub(self.free)
    }

    /// Used share in percent, rounded to nearest.
    pub fn percent(self) -> u8 {
        if self.total == 0 {
            return 0;
        }
        let used = u128::from(self.used()) * 100;
        let total = u128::from(self.total);
        // Round half up without floating point: (2*used + total) / (2*total).
        u8::try_from((2 * used + total) / (2 * total)).unwrap_or(100)
    }

    pub fn read(root: &Path) -> std::io::Result<Self> {
        Ok(Self {
            total: resources::total_space(root)?,
            free: resources::available_space(root)?,
        })
    }
}

/// The configured threshold, clamped to 1..=100; junk falls back to the default.
pub fn threshold() -> u8 {
    threshold_from(std::env::var("TALOS_BAR_DISK_THRESHOLD").ok().as_deref())
}

fn threshold_from(raw: Option<&str>) -> u8 {
    match raw
        .map(str::trim)
        .and_then(|value| value.parse::<i32>().ok())
    {
        Some(value) => u8::try_from(value.clamp(1, 100)).unwrap_or(DEFAULT_THRESHOLD),
        None => DEFAULT_THRESHOLD,
    }
}

/// The filesystem the tab watches: `/`, or `TALOS_BAR_DISK_ROOT` for a data
/// disk or a test fixture.
pub fn root() -> std::path::PathBuf {
    std::env::var_os("TALOS_BAR_DISK_ROOT").map_or_else(|| Path::new("/").to_path_buf(), std::path::PathBuf::from)
}

/// What the tab shows, or `None` when the disk is not under pressure.
pub fn cell(pressure: Pressure, threshold: u8) -> Option<String> {
    let percent = pressure.percent();
    (percent >= threshold).then(|| format!("{percent}%"))
}

/// `1.2 TiB` — the panel's size column.
pub fn human(bytes: u64) -> String {
    const UNITS: [(u64, &str); 4] = [
        (1 << 40, "TiB"),
        (1 << 30, "GiB"),
        (1 << 20, "MiB"),
        (1 << 10, "KiB"),
    ];
    for (factor, suffix) in UNITS {
        if bytes >= factor {
            #[allow(
                clippy::cast_precision_loss,
                reason = "a display size keeps three digits"
            )]
            return format!("{:.1} {suffix}", bytes as f64 / factor as f64);
        }
    }
    format!("{bytes} B")
}

/// One row: how far to indent, what it is called, how big, and where it is.
///
/// The absolute path is carried so the web page can act on a row (move it to
/// the trash) without guessing: the scan already knows it, and a page that
/// re-derived paths from names would act on the wrong file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub depth: usize,
    pub name: String,
    pub bytes: u64,
    pub path: std::path::PathBuf,
}

/// A whole-root scan, aggregated into a tree.
#[derive(Clone, Debug, Default)]
pub struct Tree {
    nodes: Vec<Node>,
    root: Option<usize>,
}

#[derive(Clone, Debug)]
struct Node {
    name: String,
    /// Absolute path, as the scan reported it.
    path: std::path::PathBuf,
    own: u64,
    children: Vec<usize>,
}

impl Tree {
    /// Fold the facade's unordered stream into a tree.
    ///
    /// `(name, own allocation, parent key, own key)`. Delivery order is
    /// unspecified and a parent may arrive after its children, so parentage is
    /// resolved as keys appear rather than by walking in order; a child whose
    /// parent never arrives (the directory vanished mid-scan) is dropped rather
    /// than invented. The first entry is the root the scan started from.
    #[cfg(test)]
    pub fn from_entries(entries: impl IntoIterator<Item = (String, u64, u64, u64)>) -> Self {
        Self::from_entries_with_paths(entries.into_iter().map(|(name, own, parent, key)| (name, own, parent, key, std::path::PathBuf::new())))
    }

    /// As [`Tree::from_entries`], with the absolute path for each entry.
    pub fn from_entries_with_paths(
        entries: impl IntoIterator<Item = (String, u64, u64, u64, std::path::PathBuf)>,
    ) -> Self {
        let mut tree = Tree::default();
        let mut seen: HashMap<u64, usize> = HashMap::new();
        let mut waiting: HashMap<u64, Vec<usize>> = HashMap::new();
        for (name, own, parent, key, path) in entries {
            let index = tree.nodes.len();
            tree.nodes.push(Node { name, path, own, children: Vec::new() });
            if tree.root.is_none() {
                tree.root = Some(index);
            }
            if let Some(taken) = waiting.remove(&key) {
                tree.nodes[index].children.extend(taken);
            }
            if let Some(parent_index) = seen.get(&parent).copied() {
                tree.nodes[parent_index].children.push(index);
            } else {
                waiting.entry(parent).or_default().push(index);
            }
            seen.insert(key, index);
        }
        tree
    }

    /// Subtree total: the directory's own allocation plus everything under it.
    pub fn total(&self, index: usize) -> u64 {
        let node = &self.nodes[index];
        node.children.iter().fold(node.own, |sum, child| {
            sum.saturating_add(self.total(*child))
        })
    }

    /// Largest first, at most `max_depth` deep and `min_bytes` big, `limit` rows.
    pub fn rows(&self, max_depth: usize, min_bytes: u64, limit: usize) -> Vec<Row> {
        let Some(root) = self.root else {
            return Vec::new();
        };
        let mut rows = Vec::new();
        self.push_rows(root, 0, max_depth, min_bytes, limit, &mut rows);
        rows
    }

    fn push_rows(
        &self,
        index: usize,
        depth: usize,
        max_depth: usize,
        min_bytes: u64,
        limit: usize,
        rows: &mut Vec<Row>,
    ) {
        if rows.len() >= limit {
            return;
        }
        let node = &self.nodes[index];
        let mut children: Vec<(usize, u64)> = node
            .children
            .iter()
            .map(|child| (*child, self.total(*child)))
            .filter(|(_, bytes)| *bytes >= min_bytes)
            .collect();
        children.sort_by(|a, b| {
            b.1.cmp(&a.1)
                .then_with(|| self.nodes[a.0].name.cmp(&self.nodes[b.0].name))
        });
        for (child, bytes) in children {
            if rows.len() >= limit {
                return;
            }
            rows.push(Row {
                depth: depth + 1,
                name: self.nodes[child].name.clone(),
                bytes,
                path: self.nodes[child].path.clone(),
            });
            if depth + 1 < max_depth {
                self.push_rows(child, depth + 1, max_depth, min_bytes, limit, rows);
            }
        }
    }
}

/// The scan the panel draws: last result, and whether a new one is running.
#[derive(Clone, Debug, Default)]
pub struct Scanner {
    shared: Arc<Mutex<State>>,
}

#[derive(Clone, Debug, Default)]
struct State {
    rows: Option<(Instant, Vec<Row>)>,
    running: bool,
    error: Option<String>,
}

impl Scanner {
    /// Start a scan unless one is running or the last one is fresh.
    pub fn refresh(&self, root: &Path) {
        let Ok(mut state) = self.shared.lock() else {
            return;
        };
        if state.running {
            return;
        }
        let fresh = state
            .rows
            .as_ref()
            .is_some_and(|(at, _)| at.elapsed() < FRESH_FOR);
        if fresh {
            return;
        }
        state.running = true;
        state.error = None;
        drop(state);

        let shared = Arc::clone(&self.shared);
        let root = root.to_path_buf();
        std::thread::spawn(move || {
            let outcome = scan(&root);
            if let Ok(mut state) = shared.lock() {
                state.running = false;
                match outcome {
                    Ok(rows) => state.rows = Some((Instant::now(), rows)),
                    Err(error) => state.error = Some(error),
                }
            }
        });
    }

    /// `(rows, running, error)` for the panel.
    pub fn peek(&self) -> (Vec<Row>, bool, Option<String>) {
        match self.shared.lock() {
            Ok(state) => (
                state
                    .rows
                    .as_ref()
                    .map(|(_, rows)| rows.clone())
                    .unwrap_or_default(),
                state.running,
                state.error.clone(),
            ),
            Err(_) => (Vec::new(), false, Some("scan state unavailable".to_owned())),
        }
    }
}

/// Scan `root` and produce the panel's rows.
fn scan(root: &Path) -> Result<Vec<Row>, String> {
    let options = ScanOptions {
        one_filesystem: true,
        ..ScanOptions::default()
    };
    // The facade hands out opaque identities; the tree only needs to match a
    // child to its parent, so each entry gets a local key as it arrives.
    let mut entries: Vec<(String, u64, u64, u64, std::path::PathBuf)> = Vec::new();
    let mut keys: HashMap<kernal_api::fs_scan::EntryId, u64> = HashMap::new();
    let mut next = 0u64;
    fs_scan::scan_stream(root, &options, |entry: ScanEntry<'_>| {
        if entry.kind == EntryKind::Directory || entry.kind == EntryKind::File {
            let key = next;
            next += 1;
            keys.insert(entry.identity.entry_id(), key);
            let parent = keys.get(&entry.parent).copied().unwrap_or(u64::MAX);
            let relative = entry.relative_name.to_string_lossy();
            let path = root.join(relative.as_ref());
            let name = relative.trim_end_matches('/').rsplit('/').next().unwrap_or("/").to_owned();
            entries.push((name, entry.allocated, parent, key, path));
        }
        true
    })
    .map_err(|error| error.to_string())?;
    Ok(Tree::from_entries_with_paths(entries).rows(MAX_DEPTH, MIN_BYTES, MAX_ROWS))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pressure(total: u64, free: u64) -> Pressure {
        Pressure { total, free }
    }

    #[test]
    fn percent_rounds_half_up_and_handles_an_empty_volume() {
        assert_eq!(pressure(1_000, 100).percent(), 90);
        assert_eq!(pressure(1_000, 0).percent(), 100);
        assert_eq!(pressure(1_000, 1_000).percent(), 0);
        assert_eq!(pressure(0, 0).percent(), 0);
        // 96.5% rounds to 97, not 96.
        assert_eq!(pressure(200, 7).percent(), 97);
    }

    #[test]
    fn the_tab_appears_only_at_the_threshold() {
        // The default trips at 95%, not before.
        assert_eq!(cell(pressure(1_000, 60), DEFAULT_THRESHOLD), None);
        assert_eq!(
            cell(pressure(1_000, 50), DEFAULT_THRESHOLD),
            Some("95%".to_owned())
        );
        assert_eq!(
            cell(pressure(1_000, 20), DEFAULT_THRESHOLD),
            Some("98%".to_owned())
        );
        // An explicit threshold still wins.
        assert_eq!(cell(pressure(1_000, 100), 90), Some("90%".to_owned()));
    }

    #[test]
    fn the_threshold_is_clamped_and_junk_falls_back() {
        assert_eq!(threshold_from(None), 95);
        assert_eq!(threshold_from(Some("85")), 85);
        assert_eq!(threshold_from(Some("0")), 1);
        assert_eq!(threshold_from(Some("500")), 100);
        assert_eq!(threshold_from(Some("lots")), 95);
        assert_eq!(threshold_from(Some(" 91 ")), 91);
    }

    #[test]
    fn sizes_are_human_readable() {
        assert_eq!(human(512), "512 B");
        assert_eq!(human(1 << 20), "1.0 MiB");
        assert_eq!(human((3 << 30) + (1 << 29)), "3.5 GiB");
    }

    /// Build entries the way the facade delivers them: root first, then an
    /// unordered mix, including a child before its parent.
    fn tree() -> Tree {
        let root = 0;
        Tree::from_entries(vec![
            (String::new(), 4, u64::MAX, root),
            ("/home".to_owned(), 8, root, 1),
            // A grandchild delivered before its parent.
            ("/home/big.iso".to_owned(), 900, 1, 2),
            ("/var".to_owned(), 200, root, 3),
            ("/tmp".to_owned(), 5, root, 4),
        ])
    }

    #[test]
    fn rows_are_largest_first_with_children_under_their_parent() {
        let rows = tree().rows(3, 0, 10);
        let names: Vec<&str> = rows.iter().map(|row| row.name.as_str()).collect();
        assert_eq!(names, ["/home", "/home/big.iso", "/var", "/tmp"]);
        assert_eq!(rows[0].bytes, 908);
        assert_eq!(rows[0].depth, 1);
        assert_eq!(rows[1].depth, 2);
    }

    #[test]
    fn small_rows_and_depth_are_cut() {
        let rows = tree().rows(1, 100, 10);
        let names: Vec<&str> = rows.iter().map(|row| row.name.as_str()).collect();
        assert_eq!(names, ["/home", "/var"], "depth 1 keeps parents only");
        let deep = tree().rows(3, 500, 10);
        let names: Vec<&str> = deep.iter().map(|row| row.name.as_str()).collect();
        assert_eq!(
            names,
            ["/home", "/home/big.iso"],
            "908 B and 900 B clear 500 B; 200 B and 5 B do not"
        );
    }

    #[test]
    fn the_row_budget_is_respected() {
        assert_eq!(tree().rows(3, 0, 2).len(), 2);
    }

    #[test]
    fn an_empty_tree_has_no_rows() {
        assert!(Tree::default().rows(3, 0, 10).is_empty());
    }
}

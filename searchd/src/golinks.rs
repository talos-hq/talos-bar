//! Which go/ links exist, so `go/ma` can complete to `go/mail`.
//!
//! Nothing on the machine answers "what go/ links are there": the links are nginx `location`
//! blocks plus a catch-all, so every name resolves and none can be listed. Completions therefore
//! come from two places:
//!
//! - a **links file** (`--go-links`, default `$XDG_CONFIG_HOME/talos-bar/go-links`): one link
//!   per line, `name [where it goes]`, `#` comments. It is the registry, written by whoever
//!   writes the links (on NixOS, generated from the same attrset as the nginx locations). Read
//!   again whenever it changes.
//! - **history** (`$XDG_STATE_HOME/talos-bar/go-history.json`, `{"name": uses}`): every go/ link
//!   opened from the popup, so the links actually used surface and rank first.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

/// One completion candidate.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct GoLink {
    /// The part after `go/`, e.g. `mail` or `soldr/pulls`.
    pub name: String,
    /// Where it goes, when the links file says.
    pub target: Option<String>,
    /// Times opened from the popup.
    pub uses: u32,
}

/// The name a `go/` link opened through `url` was recorded under, if it is one.
pub fn name_of_url(url: &str) -> Option<&str> {
    let rest = url.strip_prefix("http://go/").or_else(|| url.strip_prefix("https://go/"))?;
    let name = rest.trim_end_matches('/');
    (!name.is_empty() && !name.contains(char::is_whitespace)).then_some(name)
}

/// Parses a links file: `name [target]` per line, blank lines and `#` comments skipped. A
/// leading `go/` on the name is optional.
pub fn parse_links(text: &str) -> Vec<(String, Option<String>)> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .filter_map(|line| {
            let mut parts = line.splitn(2, char::is_whitespace);
            let name = parts.next()?.trim_start_matches("go/").trim_end_matches('/');
            let target = parts.next().map(str::trim).filter(|t| !t.is_empty());
            (!name.is_empty()).then(|| (name.to_owned(), target.map(str::to_owned)))
        })
        .collect()
}

/// Matches for the text typed after `go/`: names starting with it first, then names containing
/// it; within each, most used first, then alphabetical. An empty prefix lists everything.
pub fn complete(typed: &str, links: &[GoLink], limit: usize) -> Vec<GoLink> {
    let typed = typed.to_lowercase();
    let mut matches: Vec<(u8, &GoLink)> = links
        .iter()
        .filter_map(|link| {
            let name = link.name.to_lowercase();
            if name.starts_with(&typed) {
                Some((0, link))
            } else if name.contains(&typed) {
                Some((1, link))
            } else {
                None
            }
        })
        .collect();
    matches.sort_by(|(ta, a), (tb, b)| ta.cmp(tb).then(b.uses.cmp(&a.uses)).then_with(|| a.name.cmp(&b.name)));
    matches.into_iter().take(limit).map(|(_, link)| link.clone()).collect()
}

/// The links file and the history, kept in memory; the history is written back on each use.
pub struct GoLinks {
    links_path: Option<PathBuf>,
    history_path: Option<PathBuf>,
    file: Vec<(String, Option<String>)>,
    file_stamp: Option<SystemTime>,
    history: BTreeMap<String, u32>,
}

impl GoLinks {
    pub fn new(links_path: Option<PathBuf>, history_path: Option<PathBuf>) -> Self {
        let history = history_path
            .as_deref()
            .and_then(|path| std::fs::read(path).ok())
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        Self { links_path, history_path, file: Vec::new(), file_stamp: None, history }
    }

    /// Re-reads the links file when its mtime changed (or it appeared or vanished).
    fn refresh_file(&mut self) {
        let Some(path) = &self.links_path else { return };
        let stamp = std::fs::metadata(path).and_then(|m| m.modified()).ok();
        if stamp == self.file_stamp {
            return;
        }
        self.file_stamp = stamp;
        self.file = std::fs::read_to_string(path).map(|text| parse_links(&text)).unwrap_or_default();
    }

    /// Every known link: the file's, plus any only in the history.
    pub fn all(&mut self) -> Vec<GoLink> {
        self.refresh_file();
        let mut out: BTreeMap<&str, GoLink> = BTreeMap::new();
        for (name, target) in &self.file {
            out.entry(name).or_insert_with(|| GoLink { name: name.clone(), target: target.clone(), uses: 0 });
        }
        for (name, uses) in &self.history {
            out.entry(name).or_insert_with(|| GoLink { name: name.clone(), ..GoLink::default() }).uses = *uses;
        }
        out.into_values().collect()
    }

    /// Counts one opening of `name` and saves the history.
    pub fn record(&mut self, name: &str) {
        let uses = self.history.entry(name.to_owned()).or_default();
        *uses = uses.saturating_add(1);
        if let Some(path) = &self.history_path
            && let Err(err) = save(path, &self.history)
        {
            eprintln!("talos-searchd: saving {}: {err}", path.display());
        }
    }
}

/// Write-then-rename, so a crash never leaves half a history.
fn save(path: &Path, history: &BTreeMap<String, u32>) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(history).map_err(std::io::Error::other)?)?;
    std::fs::rename(tmp, path)
}

fn xdg_dir(var: &str, fallback: &str) -> Option<PathBuf> {
    std::env::var_os(var)
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(fallback)))
}

pub fn default_links_path() -> Option<PathBuf> {
    xdg_dir("XDG_CONFIG_HOME", ".config").map(|d| d.join("talos-bar").join("go-links"))
}

pub fn default_history_path() -> Option<PathBuf> {
    xdg_dir("XDG_STATE_HOME", ".local/state").map(|d| d.join("talos-bar").join("go-history.json"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn link(name: &str, uses: u32) -> GoLink {
        GoLink { name: name.into(), target: None, uses }
    }

    fn names(links: &[GoLink]) -> Vec<&str> {
        links.iter().map(|l| l.name.as_str()).collect()
    }

    #[test]
    fn prefix_matches_come_before_substrings_and_used_before_unused() {
        let links = [link("gmail", 9), link("maps", 0), link("mail", 3), link("roadmap", 0), link("cal", 5)];
        assert_eq!(names(&complete("ma", &links, 10)), ["mail", "maps", "gmail", "roadmap"]);
        assert_eq!(names(&complete("MA", &links, 2)), ["mail", "maps"]);
        assert_eq!(names(&complete("", &links, 10)), ["gmail", "cal", "mail", "maps", "roadmap"]);
        assert!(complete("zzz", &links, 10).is_empty());
    }

    #[test]
    fn links_files_take_names_with_optional_targets() {
        let parsed = parse_links("# comment\n\nmail https://mail.example\ngo/cal\n  soldr/  \n");
        assert_eq!(
            parsed,
            [
                ("mail".to_owned(), Some("https://mail.example".to_owned())),
                ("cal".to_owned(), None),
                ("soldr".to_owned(), None),
            ]
        );
    }

    #[test]
    fn only_go_urls_have_names() {
        assert_eq!(name_of_url("http://go/mail"), Some("mail"));
        assert_eq!(name_of_url("https://go/soldr/pulls/"), Some("soldr/pulls"));
        for miss in ["http://go/", "http://example.com/go/x", "file:///go/x"] {
            assert_eq!(name_of_url(miss), None, "{miss}");
        }
    }

    #[test]
    fn history_and_file_merge_and_history_persists() {
        let dir = std::env::temp_dir().join(format!("talos-searchd-golinks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("dir");
        let file = dir.join("go-links");
        let history = dir.join("state").join("go-history.json");
        std::fs::write(&file, "mail https://mail.example\ncal\n").expect("links");

        let mut go = GoLinks::new(Some(file.clone()), Some(history.clone()));
        go.record("mail");
        go.record("mail");
        go.record("hermes");
        let all = go.all();
        assert_eq!(names(&all), ["cal", "hermes", "mail"]);
        assert_eq!(all[2], GoLink { name: "mail".into(), target: Some("https://mail.example".into()), uses: 2 });

        let mut again = GoLinks::new(Some(file.clone()), Some(history));
        assert_eq!(again.all().iter().map(|l| l.uses).collect::<Vec<_>>(), [0, 1, 2]);

        std::fs::remove_file(&file).expect("rm");
        assert_eq!(names(&again.all()), ["hermes", "mail"], "a removed file drops its links");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

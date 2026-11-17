//! Ranking: pure functions over the app catalog and the query.

use crate::golinks::{self, GoLink};
use kernal_api::app_catalog::AppEntry;
use serde::Serialize;

/// At most this many go/ completions per query.
const GO_COMPLETIONS: usize = 8;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Hit {
    /// `app:<catalog id>` or `url:<uri>`; handed back verbatim to `activate`.
    pub id: String,
    pub title: String,
    pub subtitle: String,
    pub icon: Option<String>,
    pub score: u32,
    /// Text the client's Tab puts in the query box (`go/mail`); only on completions.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub complete: Option<String>,
}

/// `go/<name>` (optionally with a scheme) as an http URL, the go-runner contract.
pub fn go_url(query: &str) -> Option<String> {
    let q = query.trim();
    let q = q
        .strip_prefix("http://")
        .or_else(|| q.strip_prefix("https://"))
        .unwrap_or(q);
    (q.starts_with("go/") && q.len() > 3 && !q.contains(char::is_whitespace))
        .then(|| format!("http://{q}"))
}

fn score(query: &str, app: &AppEntry) -> u32 {
    let name = app.name.to_lowercase();
    if name == query {
        return 100;
    }
    if name.starts_with(query) {
        return 80;
    }
    if name.split(|c: char| !c.is_alphanumeric()).any(|w| w.starts_with(query)) {
        return 60;
    }
    if name.contains(query) {
        return 40;
    }
    if app.keywords.iter().any(|k| k.to_lowercase().starts_with(query)) {
        return 30;
    }
    0
}

/// What follows `go/` in a go-link query (possibly empty), scheme optional.
fn go_typed(query: &str) -> Option<&str> {
    let q = query.trim_start();
    let q = q.strip_prefix("http://").or_else(|| q.strip_prefix("https://")).unwrap_or(q);
    q.strip_prefix("go/").filter(|rest| !rest.trim_end().contains(char::is_whitespace))
}

fn subtitle(link: &GoLink) -> String {
    let uses = match link.uses {
        0 => String::new(),
        1 => "opened once".into(),
        n => format!("opened {n} times"),
    };
    match (&link.target, uses.is_empty()) {
        (Some(target), true) => format!("go/ link → {target}"),
        (Some(target), false) => format!("go/ link → {target} · {uses}"),
        (None, true) => "go/ link".into(),
        (None, false) => format!("go/ link · {uses}"),
    }
}

/// The literal `go/<typed>` hit (score 200, so Enter still opens exactly what was typed), then
/// completions from the known links (199 down, best first).
fn go_hits(query: &str, links: &[GoLink]) -> Vec<Hit> {
    let mut hits = Vec::new();
    let Some(typed) = go_typed(query) else {
        return hits;
    };
    let typed = typed.trim_end();
    let exact = links.iter().find(|l| l.name.eq_ignore_ascii_case(typed.trim_end_matches('/')));
    if let Some(url) = go_url(query) {
        hits.push(Hit {
            title: format!("Open {}", &url["http://".len()..]),
            subtitle: exact.map_or_else(|| "go/ link".into(), subtitle),
            id: format!("url:{url}"),
            icon: Some("internet-web-browser".into()),
            score: 200,
            complete: None,
        });
    }
    let completions = golinks::complete(typed, links, GO_COMPLETIONS + 1);
    let completions = completions.iter().filter(|l| Some(*l) != exact).take(GO_COMPLETIONS);
    for (i, link) in completions.enumerate() {
        hits.push(Hit {
            id: format!("url:http://go/{}", link.name),
            title: format!("go/{}", link.name),
            subtitle: subtitle(link),
            icon: Some("internet-web-browser".into()),
            score: 199 - i as u32,
            complete: Some(format!("go/{}", link.name)),
        });
    }
    hits
}

/// Best matches first, at most `limit`. `links` are the known go/ links, for completion.
pub fn rank(query: &str, apps: &[AppEntry], links: &[GoLink], limit: usize) -> Vec<Hit> {
    let mut hits = go_hits(query, links);
    let q = query.trim().to_lowercase();
    if !q.is_empty() {
        hits.extend(apps.iter().filter_map(|app| {
            let score = score(&q, app);
            (score > 0).then(|| Hit {
                id: format!("app:{}", app.id),
                title: app.name.clone(),
                subtitle: "Application".into(),
                icon: app.icon.clone(),
                score,
                complete: None,
            })
        }));
    }
    hits.sort_by(|a, b| b.score.cmp(&a.score).then_with(|| a.title.cmp(&b.title)));
    hits.truncate(limit);
    hits
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn go_links_follow_the_go_runner_contract() {
        assert_eq!(go_url("go/docs").as_deref(), Some("http://go/docs"));
        assert_eq!(go_url(" https://go/x ").as_deref(), Some("http://go/x"));
        for miss in ["go/", "goo/x", "go/a b", "docs"] {
            assert_eq!(go_url(miss), None, "{miss}");
        }
    }

    #[test]
    fn go_links_rank_above_apps_and_empty_queries_return_nothing() {
        assert!(rank("", &[], &[], 10).is_empty());
        let hits = rank("go/mail", &[], &[], 10);
        assert_eq!(hits[0].id, "url:http://go/mail");
    }

    fn link(name: &str, uses: u32) -> GoLink {
        GoLink { name: name.into(), target: None, uses }
    }

    fn ids(hits: &[Hit]) -> Vec<&str> {
        hits.iter().map(|h| h.id.as_str()).collect()
    }

    #[test]
    fn go_prefixes_complete_from_known_links_after_the_literal() {
        let links = [link("mail", 2), link("maps", 7), link("gmail", 20), link("cal", 1)];
        let hits = rank("go/ma", &[], &links, 10);
        assert_eq!(ids(&hits), ["url:http://go/ma", "url:http://go/maps", "url:http://go/mail", "url:http://go/gmail"]);
        assert_eq!(hits[0].complete, None, "the literal is not a completion");
        assert_eq!(hits[1].complete.as_deref(), Some("go/maps"));
        assert_eq!(hits[1].title, "go/maps");
        assert_eq!(hits[1].subtitle, "go/ link · opened 7 times");
    }

    #[test]
    fn a_bare_go_slash_lists_the_most_used_links() {
        let links = [link("mail", 2), link("maps", 7), link("cal", 0)];
        assert_eq!(ids(&rank("go/", &[], &links, 2)), ["url:http://go/maps", "url:http://go/mail"]);
        assert!(rank("go", &[], &links, 10).is_empty(), "no go/ yet, no completions");
    }

    #[test]
    fn an_exact_known_link_is_not_listed_twice() {
        let mail = GoLink { name: "mail".into(), target: Some("https://mail.example".into()), uses: 1 };
        let hits = rank("go/mail", &[], &[mail, link("mailbox", 0)], 10);
        assert_eq!(ids(&hits), ["url:http://go/mail", "url:http://go/mailbox"]);
        assert_eq!(hits[0].subtitle, "go/ link → https://mail.example · opened once");
    }

    #[test]
    fn completions_serialize_their_text_and_other_hits_omit_it() {
        let hits = rank("go/ma", &[], &[link("mail", 0)], 10);
        let json = serde_json::to_value(&hits).expect("json");
        assert!(json[0].get("complete").is_none());
        assert_eq!(json[1]["complete"], "go/mail");
    }
}

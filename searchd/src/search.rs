//! Ranking: pure functions over the app catalog and the query.

use kernal_api::app_catalog::AppEntry;
use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Hit {
    /// `app:<catalog id>` or `url:<uri>`; handed back verbatim to `activate`.
    pub id: String,
    pub title: String,
    pub subtitle: String,
    pub icon: Option<String>,
    pub score: u32,
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

/// Best matches first, at most `limit`.
pub fn rank(query: &str, apps: &[AppEntry], limit: usize) -> Vec<Hit> {
    let mut hits = Vec::new();
    if let Some(url) = go_url(query) {
        hits.push(Hit {
            title: format!("Open {}", &url["http://".len()..]),
            subtitle: "go/ link".into(),
            id: format!("url:{url}"),
            icon: Some("internet-web-browser".into()),
            score: 200,
        });
    }
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
        assert!(rank("", &[], 10).is_empty());
        let hits = rank("go/mail", &[], 10);
        assert_eq!(hits[0].id, "url:http://go/mail");
    }
}

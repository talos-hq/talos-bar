//! Multi-zone clock text. Pure: every string comes from one UTC instant passed in, so the local
//! time and the remote zones cannot disagree across a minute or midnight.

use chrono::{DateTime, Offset as _, TimeZone, Utc};
use chrono_tz::Tz;

/// One clock cell in the bar.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cell {
    pub city: String,
    pub time: String,
    /// Weekday abbreviation when the zone's date differs from local, else empty.
    pub day: String,
}

impl Cell {
    pub fn text(&self) -> String {
        if self.day.is_empty() {
            format!("{} {}", self.city, self.time)
        } else {
            format!("{} {} {}", self.city, self.time, self.day)
        }
    }
}

/// `America/New_York` → `New York`, `Etc/UTC` → `UTC`, like the Qt clock's `_city`.
pub fn city(zone: &str) -> String {
    if matches!(zone, "Etc/UTC" | "Etc/GMT" | "UTC") {
        return "UTC".to_owned();
    }
    zone.rsplit('/').next().unwrap_or(zone).replace('_', " ")
}

/// The short label for a zone the bar ships with (the Qt bar's eight), by IANA name.
pub fn short(zone: &str) -> Option<&'static str> {
    Some(match zone {
        "America/New_York" => "NYC",
        "America/Chicago" => "CHI",
        "America/Denver" => "DEN",
        "America/Los_Angeles" => "LAX",
        "Australia/Sydney" => "SYD",
        "Europe/Luxembourg" => "LUX",
        "Europe/London" => "LON",
        "Europe/Moscow" => "MSK",
        "Asia/Tokyo" => "TYO",
        "Etc/UTC" | "Etc/GMT" | "UTC" => "UTC",
        _ => return None,
    })
}

/// How the bar names zones: `[clock] labels`, then (with `short`) the built-in short label,
/// then the city name. `short` also writes remote times compactly (`2:30a`); the local clock
/// keeps `2:30 AM`.
#[derive(Clone, Copy)]
pub struct Labels<'a> {
    pub short: bool,
    pub explicit: &'a std::collections::BTreeMap<String, String>,
}

impl Labels<'_> {
    pub fn of(&self, zone: &str) -> String {
        if let Some(label) = self.explicit.get(zone) {
            return label.clone();
        }
        match short(zone) {
            Some(label) if self.short => label.to_owned(),
            _ => city(zone),
        }
    }
}

fn hm<Z: TimeZone>(t: &DateTime<Z>, seconds: bool) -> String
where
    Z::Offset: std::fmt::Display,
{
    let text = if seconds { t.format("%I:%M:%S %p") } else { t.format("%I:%M %p") }.to_string();
    text.strip_prefix('0').map(str::to_owned).unwrap_or(text)
}

/// A remote zone's time in the compact form short labels use: `2:30a`, `12:05p`.
fn hm_compact<Z: TimeZone>(t: &DateTime<Z>, seconds: bool) -> String
where
    Z::Offset: std::fmt::Display,
{
    let full = hm(t, seconds);
    match full.rsplit_once(' ') {
        Some((time, ampm)) => format!("{time}{}", ampm[..1].to_lowercase()),
        None => full,
    }
}

/// The bar's clock: local first (time over date, as the Qt bar shows it), then each configured
/// zone, skipping one that is the local zone. Unknown zone names are returned in `bad`.
pub struct World {
    pub local: Cell,
    pub date: String,
    pub zones: Vec<Cell>,
    /// The IANA name of each cell in `zones`, for naming a dropped one.
    pub names: Vec<String>,
    pub bad: Vec<String>,
}

pub fn world(now: DateTime<Utc>, local: Tz, zones: &[String], seconds: bool, labels: Labels<'_>) -> World {
    let here = now.with_timezone(&local);
    let local_city = city(local.name());
    let local_offset = here.offset().fix();
    let mut cells = Vec::new();
    let mut names = Vec::new();
    let mut bad = Vec::new();
    for name in zones {
        let Ok(tz) = name.parse::<Tz>() else {
            bad.push(name.clone());
            continue;
        };
        let there = now.with_timezone(&tz);
        if city(tz.name()) == local_city && there.offset().fix() == local_offset {
            continue;
        }
        let day = if there.date_naive() == here.date_naive() { String::new() } else { there.format("%a").to_string() };
        let time = if labels.short { hm_compact(&there, seconds) } else { hm(&there, seconds) };
        cells.push(Cell { city: labels.of(name), time, day });
        names.push(name.clone());
    }
    World {
        local: Cell { city: local_city, time: hm(&here, seconds), day: String::new() },
        date: here.format("%m/%d/%y").to_string(),
        zones: cells,
        names,
        bad,
    }
}

/// One remote row of the world-clock table (`Clock.qml`'s repeater).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub city: String,
    pub time: String,
    /// Full weekday when the zone's date differs from local, else empty.
    pub day: String,
    pub difference: String,
}

/// The Qt bar's world-clock popup (`glue/builtin/clock.py` `world_at`), from one instant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Table {
    /// `Monday, November 2, 2026`.
    pub date: String,
    pub local: (String, String),
    pub rows: Vec<Row>,
}

/// `same time`, `3 hours later`, `5 hours 30 minutes earlier` (clock.py `_difference`).
pub fn difference(seconds: i32) -> String {
    if seconds == 0 {
        return "same time".to_owned();
    }
    let abs = seconds.unsigned_abs();
    let parts: Vec<String> = [(abs / 3600, "hour"), (abs % 3600 / 60, "minute"), (abs % 60, "second")]
        .into_iter()
        .filter(|(value, _)| *value != 0)
        .map(|(value, unit)| format!("{value} {unit}{}", if value == 1 { "" } else { "s" }))
        .collect();
    format!("{} {}", parts.join(" "), if seconds > 0 { "later" } else { "earlier" })
}

/// The table for `zones`: city names (not the bar's short labels), seconds always, rows sorted
/// by offset from local then city, the local zone skipped, unknown zones left out.
pub fn table(now: DateTime<Utc>, local: Tz, zones: &[String]) -> Table {
    let here = now.with_timezone(&local);
    let local_city = city(local.name());
    let local_offset = here.offset().fix().local_minus_utc();
    let mut rows: Vec<(i32, Row)> = zones
        .iter()
        .filter_map(|name| name.parse::<Tz>().ok())
        .filter_map(|tz| {
            let there = now.with_timezone(&tz);
            let diff = there.offset().fix().local_minus_utc() - local_offset;
            let city = city(tz.name());
            if city == local_city && diff == 0 {
                return None;
            }
            let day = if there.date_naive() == here.date_naive() { String::new() } else { there.format("%A").to_string() };
            Some((diff, Row { city, time: hm(&there, true), day, difference: difference(diff) }))
        })
        .collect();
    rows.sort_by(|a, b| (a.0, &a.1.city).cmp(&(b.0, &b.1.city)));
    Table {
        date: format!("{} {}, {}", here.format("%A, %B"), here.format("%-d"), here.format("%Y")),
        local: (local_city, hm(&here, true)),
        rows: rows.into_iter().map(|(_, row)| row).collect(),
    }
}

/// The session's zone: `TZ`, then the OS setting, then UTC.
pub fn local_zone() -> Tz {
    std::env::var("TZ")
        .ok()
        .and_then(|tz| tz.trim_start_matches(':').parse().ok())
        .or_else(|| iana_time_zone::get_timezone().ok().and_then(|tz| tz.parse().ok()))
        .unwrap_or(Tz::UTC)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(rfc3339: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(rfc3339).expect("time").with_timezone(&Utc)
    }

    const CITIES: Labels<'static> = Labels { short: false, explicit: &std::collections::BTreeMap::new() };

    fn zones(list: &[&str]) -> Vec<String> {
        list.iter().map(|z| (*z).to_owned()).collect()
    }

    #[test]
    fn differences_read_like_the_qt_clock() {
        assert_eq!(difference(0), "same time");
        assert_eq!(difference(3600), "1 hour later");
        assert_eq!(difference(-3 * 3600), "3 hours earlier");
        assert_eq!(difference(5 * 3600 + 30 * 60), "5 hours 30 minutes later");
        assert_eq!(difference(-(60 + 1)), "1 minute 1 second earlier");
    }

    #[test]
    fn the_world_table_matches_clock_py() {
        // 2026-11-02 03:30:05 UTC: Sunday evening in Los Angeles (PST, UTC-8).
        let all = zones(&["America/New_York", "Asia/Tokyo", "UTC", "America/Los_Angeles", "Asia/Kolkata", "Mars/X"]);
        let t = table(at("2026-11-02T03:30:05Z"), Tz::America__Los_Angeles, &all);
        assert_eq!(t.date, "Sunday, November 1, 2026");
        assert_eq!(t.local, ("Los Angeles".to_owned(), "7:30:05 PM".to_owned()));
        let rows: Vec<_> = t.rows.iter().map(|r| format!("{}: {} {} • {}", r.city, r.time, r.day, r.difference)).collect();
        assert_eq!(rows, [
            "New York: 10:30:05 PM  • 3 hours later",
            "UTC: 3:30:05 AM Monday • 8 hours later",
            "Kolkata: 9:00:05 AM Monday • 13 hours 30 minutes later",
            "Tokyo: 12:30:05 PM Monday • 17 hours later",
        ], "sorted by offset, local zone skipped, unknown zone left out");
    }

    #[test]
    fn city_names_match_the_qt_clock() {
        assert_eq!(city("America/New_York"), "New York");
        assert_eq!(city("Etc/UTC"), "UTC");
        assert_eq!(city("UTC"), "UTC");
        assert_eq!(city("Asia/Tokyo"), "Tokyo");
    }

    #[test]
    fn one_instant_across_zones_with_day_labels() {
        // 2026-11-02 03:30 UTC: Sunday evening in Los Angeles, Monday in Tokyo.
        let w = world(at("2026-11-02T03:30:00Z"), Tz::America__Los_Angeles, &zones(&["Asia/Tokyo", "UTC", "America/New_York"]), false, CITIES);
        assert_eq!(w.local.text(), "Los Angeles 7:30 PM");
        assert_eq!(w.date, "11/01/26");
        let texts: Vec<_> = w.zones.iter().map(Cell::text).collect();
        assert_eq!(texts, ["Tokyo 12:30 PM Mon", "UTC 3:30 AM Mon", "New York 10:30 PM"]);
        assert!(w.bad.is_empty());
    }

    #[test]
    fn seconds_dst_and_the_local_zone_is_not_repeated() {
        // New York leaves DST on 2026-11-01: 05:59:59Z is still EDT... after 06:00Z it is EST.
        let w = world(at("2026-11-01T05:59:59Z"), Tz::America__New_York, &zones(&["America/New_York", "UTC"]), true, CITIES);
        assert_eq!(w.local.time, "1:59:59 AM");
        assert_eq!(w.zones.len(), 1, "local zone skipped");
        let w = world(at("2026-11-01T06:00:00Z"), Tz::America__New_York, &zones(&[]), true, CITIES);
        assert_eq!(w.local.time, "1:00:00 AM");
    }

    #[test]
    fn unknown_zones_are_reported_not_drawn() {
        let w = world(at("2026-11-02T00:00:00Z"), Tz::UTC, &zones(&["Mars/Olympus", "Asia/Tokyo"]), false, CITIES);
        assert_eq!(w.bad, ["Mars/Olympus"]);
        assert_eq!(w.zones.len(), 1);
    }

    #[test]
    fn short_labels_keep_the_day_marker_and_explicit_labels_win() {
        let mut explicit = std::collections::BTreeMap::new();
        let short = Labels { short: true, explicit: &explicit };
        let w = world(at("2026-11-02T03:30:00Z"), Tz::America__Los_Angeles, &zones(&["Asia/Tokyo", "UTC", "America/New_York", "Europe/Paris"]), false, short);
        let texts: Vec<_> = w.zones.iter().map(Cell::text).collect();
        assert_eq!(texts, ["TYO 12:30p Mon", "UTC 3:30a Mon", "NYC 10:30p", "Paris 4:30a Mon"]);
        assert_eq!(w.names[3], "Europe/Paris");
        explicit.insert("Asia/Tokyo".to_owned(), "Tokyo".to_owned());
        let labels = Labels { short: true, explicit: &explicit };
        assert_eq!(labels.of("Asia/Tokyo"), "Tokyo");
        assert_eq!(labels.of("America/Chicago"), "CHI");
        assert_eq!(Labels { short: false, explicit: &explicit }.of("America/Chicago"), "Chicago");
    }
}

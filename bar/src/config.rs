//! `~/.config/talos-bar/config.toml`, the file `docs/DESIGN.md` names for the bar.
//!
//! Only the keys this bar reads are parsed; any other table in the file is ignored, so the Qt
//! bar and this one can share it.
//!
//! ```toml
//! [clock]
//! zones = ["America/New_York", "UTC"]   # IANA names, shown after the local time
//! seconds = false
//! short = true                           # short labels (NYC, UTC); false: city names (New York)
//! labels = { "Europe/Berlin" = "BER" }   # per-zone label, wins over both
//!
//! [bar]
//! width = "full"                         # "full": span the edge; "fit": only as wide as the content;
//!                                        # a number: full, with this initial / snapshot width (1280)
//! align = "center"                       # a "fit" bar's place on the edge: left | center | right
//! screen = "HDMI-A-1"                    # output (connector name); default: see crate::screen
//! opacity = 0.7                          # fill opacity of the bar and its panels (text stays opaque); 1.0
//!
//! [panels]
//! agenda_rows = 12                       # meeting rows in the agenda panel
//! refresh_secs = 300                     # how often the tasks cell re-runs tasks_cli without a click
//! max_height = 520                       # tallest a panel grows before it scrolls
//!
//! [theme]                                # every key optional; see crate::theme
//! mode = "dark"                          # dark | light | system (KDE's colour scheme)
//! accent = "#3daee9"                     # selection, focus, pressed buttons
//! background = "#202326"                 # the dock's fill
//! foreground = "#fcfcfc"                 # text
//! panel = "#2a2e32"                      # drop-down panels' fill (default: background)
//! ```

use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::PathBuf;

/// The Qt bar's world-clock zones (`src/talos_bar/glue/builtin/clock.py`), in its order.
pub const DEFAULT_ZONES: [&str; 8] = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "Australia/Sydney",
    "Europe/Luxembourg",
    "Europe/Moscow",
    "Asia/Tokyo",
    "UTC",
];

#[derive(Debug, Deserialize, PartialEq)]
#[serde(default)]
pub struct Clock {
    pub zones: Vec<String>,
    pub seconds: bool,
    /// Short built-in labels (`NYC`) instead of city names (`New York`).
    pub short: bool,
    /// Explicit labels by IANA name; they win over `short`.
    pub labels: BTreeMap<String, String>,
}

impl Default for Clock {
    fn default() -> Self {
        Self {
            zones: DEFAULT_ZONES.iter().map(|z| (*z).to_owned()).collect(),
            seconds: false,
            short: true,
            labels: BTreeMap::new(),
        }
    }
}

/// `[bar] width` as written: a number (the old form) or `"full"` / `"fit"`.
#[derive(Debug, Deserialize, PartialEq)]
#[serde(untagged)]
enum WidthKey {
    Px(f32),
    Mode(String),
}

/// `[bar] align`: where a `fit` bar sits on its edge.
#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Align {
    #[serde(alias = "start")]
    Left,
    #[default]
    #[serde(alias = "centre")]
    Center,
    #[serde(alias = "end")]
    Right,
}

#[derive(Debug, PartialEq)]
pub struct Bar {
    /// Initial / snapshot width; a dock lays out against its real width.
    pub width: f32,
    /// Only as wide as the content (`width = "fit"`), placed by `align`.
    pub fit: bool,
    pub align: Align,
    /// The output's connector name (`HDMI-A-1`); `None` defers to the Qt bar's saved choice.
    pub screen: Option<String>,
    /// Fill opacity, 0.0..=1.0 (the Qt bar's `Theme.panelOpacity` is 0.7).
    pub opacity: f32,
}

impl Default for Bar {
    fn default() -> Self {
        Self { width: 1280.0, fit: false, align: Align::Center, screen: None, opacity: 1.0 }
    }
}

#[derive(Deserialize)]
#[serde(default)]
struct BarKeys {
    width: Option<WidthKey>,
    align: Align,
    screen: Option<String>,
    opacity: f32,
}

impl Default for BarKeys {
    fn default() -> Self {
        let bar = Bar::default();
        Self { width: None, align: bar.align, screen: bar.screen, opacity: bar.opacity }
    }
}

impl<'de> Deserialize<'de> for Bar {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let keys = BarKeys::deserialize(deserializer)?;
        let (width, fit) = match keys.width {
            None => (Bar::default().width, false),
            Some(WidthKey::Px(px)) => (px, false),
            Some(WidthKey::Mode(mode)) => match mode.as_str() {
                "full" => (Bar::default().width, false),
                "fit" => (Bar::default().width, true),
                other => {
                    return Err(serde::de::Error::custom(format!(
                        "[bar] width must be \"full\", \"fit\" or a number, got {other:?}"
                    )))
                }
            },
        };
        Ok(Self { width, fit, align: keys.align, screen: keys.screen, opacity: keys.opacity })
    }
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(default)]
pub struct Panels {
    pub agenda_rows: usize,
    pub refresh_secs: u64,
    pub max_height: f32,
}

impl Default for Panels {
    fn default() -> Self {
        Self { agenda_rows: 12, refresh_secs: 300, max_height: 520.0 }
    }
}

/// `[theme]`, as written; [`crate::theme::build`] validates it into a gfx `Theme`.
#[derive(Debug, Default, Deserialize, PartialEq)]
#[serde(default, deny_unknown_fields)]
pub struct Theme {
    pub mode: Option<String>,
    pub accent: Option<String>,
    pub background: Option<String>,
    pub foreground: Option<String>,
    pub panel: Option<String>,
}

#[derive(Debug, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct Config {
    pub clock: Clock,
    pub bar: Bar,
    pub panels: Panels,
    pub theme: Theme,
}

pub fn parse(text: &str) -> Result<Config, String> {
    toml::from_str(text).map_err(|err| err.to_string())
}

pub fn default_path() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("talos-bar").join("config.toml"))
}

/// A missing file means defaults; an unreadable or invalid one is an error the caller reports.
pub fn load(path: Option<&std::path::Path>) -> Result<Config, String> {
    let Some(path) = path else { return Ok(Config::default()) };
    match std::fs::read_to_string(path) {
        Ok(text) => parse(&text).map_err(|err| format!("{}: {err}", path.display())),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
        Err(err) => Err(format!("{}: {err}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_file_is_the_qt_bar_defaults() {
        let config = parse("").expect("parse");
        assert_eq!(config.clock.zones.len(), 8);
        assert_eq!(config.clock.zones[0], "America/New_York");
        assert!(!config.clock.seconds);
        assert!((config.bar.width - 1280.0).abs() < f32::EPSILON);
        assert_eq!(config.panels, Panels::default());
    }

    #[test]
    fn screen_and_opacity_are_read() {
        let config = parse("[bar]\nscreen = \"HDMI-A-1\"\nopacity = 0.7\n").expect("parse");
        assert_eq!(config.bar.screen.as_deref(), Some("HDMI-A-1"));
        assert!((config.bar.opacity - 0.7).abs() < f32::EPSILON);
        let config = parse("").expect("parse");
        assert_eq!(config.bar.screen, None);
        assert!((config.bar.opacity - 1.0).abs() < f32::EPSILON, "opaque by default");
    }

    #[test]
    fn width_is_full_fit_or_a_number_and_align_is_read() {
        let bar = |text: &str| parse(text).map(|c| c.bar);
        let full = bar("").expect("parse");
        assert!(!full.fit && (full.width - 1280.0).abs() < f32::EPSILON && full.align == Align::Center);
        assert!(!bar("[bar]\nwidth = \"full\"\n").expect("parse").fit);
        let fit = bar("[bar]\nwidth = \"fit\"\nalign = \"right\"\n").expect("parse");
        assert!(fit.fit);
        assert_eq!(fit.align, Align::Right);
        assert_eq!(bar("[bar]\nalign = \"start\"\n").expect("parse").align, Align::Left);
        let px = bar("[bar]\nwidth = 900\n").expect("parse");
        assert!(!px.fit && (px.width - 900.0).abs() < f32::EPSILON);
        let err = bar("[bar]\nwidth = \"wide\"\n").expect_err("bad width");
        assert!(err.contains("\"full\", \"fit\""), "{err}");
        assert!(bar("[bar]\nalign = \"middle\"\n").is_err());
    }

    #[test]
    fn panels_table_is_read() {
        let config = parse("[panels]\nagenda_rows = 3\nrefresh_secs = 60\n").expect("parse");
        assert_eq!(config.panels.agenda_rows, 3);
        assert_eq!(config.panels.refresh_secs, 60);
        assert!((config.panels.max_height - 520.0).abs() < f32::EPSILON);
    }

    #[test]
    fn reads_its_keys_and_ignores_other_tables() {
        let config = parse("[clock]\nzones = [\"UTC\"]\nseconds = true\n[bar]\nwidth = 800\n[plugins]\nx = 1\n")
            .expect("parse");
        assert_eq!(config.clock.zones, ["UTC"]);
        assert!(config.clock.seconds);
        assert!((config.bar.width - 800.0).abs() < f32::EPSILON);
        assert!(parse("[clock]\nzones = 3\n").is_err());
    }

    #[test]
    fn labels_and_short_are_read() {
        let config = parse("[clock]\nshort = false\nlabels = { \"Asia/Tokyo\" = \"Tōkyō\" }\n").expect("parse");
        assert!(!config.clock.short);
        assert_eq!(config.clock.labels.get("Asia/Tokyo").map(String::as_str), Some("Tōkyō"));
        assert!(parse("").expect("parse").clock.short, "short labels by default");
    }

    #[test]
    fn theme_table_is_read_and_typos_are_errors() {
        let config = parse("[theme]\nmode = \"light\"\naccent = \"#ff8800\"\n").expect("parse");
        assert_eq!(config.theme.mode.as_deref(), Some("light"));
        assert_eq!(config.theme.accent.as_deref(), Some("#ff8800"));
        assert_eq!(config.theme.panel, None);
        assert_eq!(parse("").expect("parse").theme, Theme::default());
        assert!(parse("[theme]\naccnet = \"#fff\"\n").is_err(), "a misspelt key is not silently ignored");
    }

    #[test]
    fn missing_file_is_defaults() {
        let config = load(Some(std::path::Path::new("/nonexistent/talos-bar.toml"))).expect("defaults");
        assert_eq!(config, Config::default());
    }
}

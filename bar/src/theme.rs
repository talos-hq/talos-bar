//! `[theme]` from the config file, into the gfx facade's `Theme` tokens.
//!
//! Every key is optional; a missing `[theme]` is the facade's default theme, as before this
//! table existed. Colours are `#rgb`, `#rrggbb` or `#rrggbbaa`. `mode = "system"` follows the
//! desktop's colour scheme: KDE writes it to `kdeglobals` (`[General] ColorScheme`, or the
//! `[KDE] LookAndFeelPackage` it came from); a scheme or package named "…dark…"/"…light…" picks
//! that mode, and anything else stays dark, the Qt bar's own palette (Breeze Dark, `Theme.qml`).

use crate::config;
use kernal_api::gfx::{Color, Theme, ThemeMode};
use std::path::PathBuf;

/// `#rgb`, `#rrggbb` or `#rrggbbaa`.
pub fn parse_color(text: &str) -> Result<Color, String> {
    let bad = || format!("{text:?} is not a #rgb, #rrggbb or #rrggbbaa colour");
    let hex = text.trim().strip_prefix('#').ok_or_else(bad)?;
    if !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(bad());
    }
    let byte = |i: usize| u8::from_str_radix(&hex[i..i + 2], 16).map_err(|_| bad());
    match hex.len() {
        3 => {
            let nib = |i: usize| u8::from_str_radix(&hex[i..=i], 16).map(|n| n * 17).map_err(|_| bad());
            Ok(Color::rgb(nib(0)?, nib(1)?, nib(2)?))
        }
        6 => Ok(Color::rgb(byte(0)?, byte(2)?, byte(4)?)),
        8 => Ok(Color::rgba(byte(0)?, byte(2)?, byte(4)?, byte(6)?)),
        _ => Err(bad()),
    }
}

/// The mode a `kdeglobals` file names, if it names one.
pub fn mode_from_kdeglobals(text: &str) -> Option<ThemeMode> {
    let value = |key: &str| {
        text.lines().find_map(|line| line.trim().strip_prefix(key)?.strip_prefix('=').map(|v| v.trim().to_ascii_lowercase()))
    };
    [value("ColorScheme"), value("LookAndFeelPackage")].into_iter().flatten().find_map(|name| {
        if name.contains("dark") {
            Some(ThemeMode::Dark)
        } else if name.contains("light") {
            Some(ThemeMode::Light)
        } else {
            None
        }
    })
}

/// `$XDG_CONFIG_HOME/kdeglobals`, else `~/.config/kdeglobals`.
fn kdeglobals() -> Option<PathBuf> {
    std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
        .map(|dir| dir.join("kdeglobals"))
}

/// The desktop's mode for `mode = "system"`.
pub fn system_mode() -> ThemeMode {
    kdeglobals()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| mode_from_kdeglobals(&text))
        .unwrap_or(ThemeMode::Dark)
}

/// The Qt bar's fill (`Theme.qml` `window`, Breeze Dark) and Breeze Light's, used as the fill
/// to make translucent when `[theme] background` is not set.
const DARK_FILL: Color = Color::rgb(32, 35, 38);
const LIGHT_FILL: Color = Color::rgb(0xef, 0xf0, 0xf1);

fn scaled(color: Color, opacity: f32) -> Color {
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, reason = "clamped to 0..=255")]
    let a = (f32::from(color.a) * opacity).round().clamp(0.0, 255.0) as u8;
    Color::rgba(color.r, color.g, color.b, a)
}

/// `[bar] opacity` applied to the fills: the bar's background and the panels' colour get their
/// alpha scaled by `opacity`, text and widgets stay opaque (as the Qt bar's
/// `Qt.alpha(Theme.window, Theme.panelOpacity)`). Returns the theme and whether the surface must
/// be composited as transparent (any fill below full alpha, including a `#rrggbbaa` colour).
pub fn with_opacity(theme: Theme, opacity: f32) -> Result<(Theme, bool), String> {
    if !(0.0..=1.0).contains(&opacity) {
        return Err(format!("[bar] opacity = {opacity}: expected 0.0 to 1.0"));
    }
    let mut theme = theme;
    if opacity < 1.0 {
        let fill = theme.background.unwrap_or(match theme.mode {
            ThemeMode::Light => LIGHT_FILL,
            _ => DARK_FILL,
        });
        theme.background = Some(scaled(fill, opacity));
        theme.panel = theme.panel.map(|panel| scaled(panel, opacity));
    }
    let translucent = [theme.background, theme.panel].into_iter().flatten().any(|c| c.a < 255);
    Ok((theme, translucent))
}

/// The facade `Theme` for `[theme]`; `system` is asked only when `mode = "system"`.
pub fn build(table: &config::Theme, system: impl FnOnce() -> ThemeMode) -> Result<Theme, String> {
    let mode = match table.mode.as_deref().map(str::trim) {
        None | Some("dark") => ThemeMode::Dark,
        Some("light") => ThemeMode::Light,
        Some("system") => system(),
        Some(other) => return Err(format!("[theme] mode = {other:?}: expected \"dark\", \"light\" or \"system\"")),
    };
    let color = |key: &str, value: &Option<String>| {
        value.as_deref().map(parse_color).transpose().map_err(|err| format!("[theme] {key}: {err}"))
    };
    let mut theme = Theme::with_mode(mode);
    if let Some(c) = color("accent", &table.accent)? {
        theme = theme.with_accent(c);
    }
    if let Some(c) = color("background", &table.background)? {
        theme = theme.with_background(c);
    }
    if let Some(c) = color("foreground", &table.foreground)? {
        theme = theme.with_foreground(c);
    }
    if let Some(c) = color("panel", &table.panel)? {
        theme = theme.with_panel(c);
    }
    Ok(theme)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opacity_scales_the_fills_and_marks_the_surface_translucent() {
        let (theme, translucent) = with_opacity(Theme::default(), 1.0).expect("ok");
        assert!(!translucent);
        assert_eq!(theme.background, None, "opaque keeps the palette");
        let (theme, translucent) = with_opacity(Theme::default(), 0.7).expect("ok");
        assert!(translucent);
        assert_eq!(theme.background, Some(Color::rgba(32, 35, 38, 179)), "the Qt bar's 70% window colour");
        let base = Theme::default().with_background(Color::rgb(0, 0, 0)).with_panel(Color::rgba(10, 10, 10, 200));
        let (theme, translucent) = with_opacity(base, 0.5).expect("ok");
        assert!(translucent);
        assert_eq!(theme.background, Some(Color::rgba(0, 0, 0, 128)));
        assert_eq!(theme.panel, Some(Color::rgba(10, 10, 10, 100)), "alpha multiplies");
        let alpha_only = Theme::default().with_background(parse_color("#00000080").expect("colour"));
        assert!(with_opacity(alpha_only, 1.0).expect("ok").1, "#rrggbbaa alone is honoured");
        assert!(with_opacity(Theme::default(), 1.5).is_err());
        assert!(with_opacity(Theme::default(), f32::NAN).is_err());
    }

    #[test]
    fn colours_parse_in_three_lengths() {
        assert_eq!(parse_color("#3daee9"), Ok(Color::rgb(0x3d, 0xae, 0xe9)));
        assert_eq!(parse_color("#f80"), Ok(Color::rgb(0xff, 0x88, 0x00)));
        assert_eq!(parse_color(" #20232680 "), Ok(Color::rgba(0x20, 0x23, 0x26, 0x80)));
        for bad in ["3daee9", "#12345", "#gggggg", "#", "", "#ééé"] {
            assert!(parse_color(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn no_table_is_the_default_theme() {
        let theme = build(&config::Theme::default(), || unreachable!("not system")).expect("build");
        assert_eq!(theme, Theme::default());
    }

    #[test]
    fn every_key_lands_in_its_token() {
        let table = config::Theme {
            mode: Some("light".into()),
            accent: Some("#ff0000".into()),
            background: Some("#000000".into()),
            foreground: Some("#ffffff".into()),
            panel: Some("#112233".into()),
        };
        let theme = build(&table, || unreachable!("not system")).expect("build");
        assert_eq!(theme.mode, ThemeMode::Light);
        assert_eq!(theme.accent, Color::rgb(255, 0, 0));
        assert_eq!(theme.background, Some(Color::rgb(0, 0, 0)));
        assert_eq!(theme.foreground, Some(Color::rgb(255, 255, 255)));
        assert_eq!(theme.for_panel().background, Some(Color::rgb(0x11, 0x22, 0x33)));
    }

    #[test]
    fn bad_values_name_their_key() {
        let mode = build(&config::Theme { mode: Some("sepia".into()), ..config::Theme::default() }, || ThemeMode::Dark);
        assert!(mode.expect_err("mode").contains("mode = \"sepia\""));
        let accent = build(&config::Theme { accent: Some("blue".into()), ..config::Theme::default() }, || ThemeMode::Dark);
        assert!(accent.expect_err("accent").starts_with("[theme] accent:"));
    }

    #[test]
    fn system_follows_kdeglobals() {
        let table = config::Theme { mode: Some("system".into()), ..config::Theme::default() };
        assert_eq!(build(&table, || ThemeMode::Light).expect("build").mode, ThemeMode::Light);
        assert_eq!(mode_from_kdeglobals("[General]\nColorScheme=BreezeLight\n"), Some(ThemeMode::Light));
        assert_eq!(mode_from_kdeglobals("[KDE]\nLookAndFeelPackage=org.kde.breezedark.desktop\n"), Some(ThemeMode::Dark));
        assert_eq!(
            mode_from_kdeglobals("[General]\nColorScheme=BreezeLight\n[KDE]\nLookAndFeelPackage=org.kde.breezedark.desktop\n"),
            Some(ThemeMode::Light),
            "the scheme itself wins over the package it came from"
        );
        assert_eq!(mode_from_kdeglobals("[General]\nColorSchemeHash=abc\nColorScheme=Oxygen\n"), None);
    }
}

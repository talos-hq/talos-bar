//! Which output the dock lives on, with the Qt bar's naming (`src/talos_bar/platform`): outputs
//! are chosen by connector name (`HDMI-A-1`), and the Qt bar's saved choice
//! (`$XDG_STATE_HOME/talos-bar/screen`) wins over `TALOS_BAR_SCREEN`. `[bar] screen` in the
//! config, written on purpose for this bar, wins over both. Nothing set is the desktop's primary.
//!
//! A named output that is not connected falls back to the primary one (the Qt bar's "last
//! resort"), and the bar logs it; see `gfx::Monitor::Named`. While the wanted output is gone
//! the bar waits on another and goes back as soon as it reappears ([`return_to`]).
//!
//! The right-click screen menu saves its pick to the same state file, in the same format
//! (the output name and a newline), so the Qt bar and this one share the choice ([`save`]).

use kernal_api::gfx::{Monitor, MonitorInfo};

/// An output as the bar lists it: its name and whether the dock is on it now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Output {
    pub name: String,
    pub current: bool,
}

impl From<&MonitorInfo> for Output {
    fn from(info: &MonitorInfo) -> Self {
        Self { name: info.name.clone(), current: info.current }
    }
}
use std::path::PathBuf;

/// The choice from its three sources, first non-empty wins; `None` is the primary output.
pub fn choose(config: Option<&str>, saved: Option<&str>, env: Option<&str>) -> Option<String> {
    [config, saved, env].into_iter().flatten().map(str::trim).find(|name| !name.is_empty()).map(str::to_owned)
}

pub fn monitor(name: Option<String>) -> Monitor {
    name.map_or(Monitor::Primary, Monitor::Named)
}

/// The Qt bar's saved choice file.
fn state_file() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_STATE_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state")))?;
    Some(base.join("talos-bar").join("screen"))
}

/// Save `name` as the Qt bar's `Placement.moveTo` does: `name\n`, creating the directory.
pub fn save(name: &str) -> Result<PathBuf, String> {
    let path = state_file().ok_or("neither XDG_STATE_HOME nor HOME is set")?;
    save_to(&path, name)?;
    Ok(path)
}

fn save_to(path: &std::path::Path, name: &str) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|err| format!("{}: {err}", dir.display()))?;
    }
    std::fs::write(path, format!("{name}\n")).map_err(|err| format!("{}: {err}", path.display()))
}

/// The output to move back to: `wanted`, when it is connected and the dock is shown on
/// another one. `None` while the dock has not been shown yet (no current output), so the
/// start-up fallback is left to the backend.
pub fn return_to<'a>(wanted: Option<&'a str>, monitors: &[Output]) -> Option<&'a str> {
    let wanted = wanted?;
    let current = monitors.iter().find(|info| info.current)?;
    (current.name != wanted && monitors.iter().any(|info| info.name == wanted)).then_some(wanted)
}

/// The output named by config, the Qt bar's saved choice, then `TALOS_BAR_SCREEN`.
pub fn configured(config: Option<&str>) -> Option<String> {
    let saved = state_file().and_then(|path| std::fs::read_to_string(path).ok());
    let env = std::env::var("TALOS_BAR_SCREEN").ok();
    choose(config, saved.as_deref(), env.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_then_saved_then_env_then_primary() {
        assert_eq!(choose(Some("DP-1"), Some("HDMI-A-1\n"), Some("eDP-1")).as_deref(), Some("DP-1"));
        assert_eq!(choose(None, Some("HDMI-A-1\n"), Some("eDP-1")).as_deref(), Some("HDMI-A-1"));
        assert_eq!(choose(Some(" "), Some(""), Some("eDP-1")).as_deref(), Some("eDP-1"), "blank is unset");
        assert_eq!(choose(None, None, None), None);
        assert_eq!(monitor(None), Monitor::Primary);
        assert_eq!(monitor(Some("DP-1".into())), Monitor::Named("DP-1".into()));
    }

    #[test]
    fn the_choice_is_saved_in_the_qt_bars_format() {
        let dir = std::env::temp_dir().join(format!("talos-bar-screen-test-{}", std::process::id()));
        let path = dir.join("talos-bar").join("screen");
        save_to(&path, "Virtual-1").expect("save");
        assert_eq!(std::fs::read_to_string(&path).expect("read"), "Virtual-1\n");
        // Read back the way `configured` reads it.
        assert_eq!(choose(None, Some(&std::fs::read_to_string(&path).expect("read")), None).as_deref(), Some("Virtual-1"));
        std::fs::remove_dir_all(&dir).expect("clean up");
    }

    #[test]
    fn it_goes_back_to_the_wanted_output_when_that_is_connected() {
        let list = |current: &str, names: &[&str]| -> Vec<Output> {
            names.iter().map(|name| Output { name: (*name).to_owned(), current: *name == current }).collect()
        };
        let both = list("Virtual-0", &["Virtual-0", "Virtual-1"]);
        assert_eq!(return_to(Some("Virtual-1"), &both), Some("Virtual-1"));
        assert_eq!(return_to(Some("Virtual-0"), &both), None, "already there");
        assert_eq!(return_to(Some("HDMI-A-1"), &both), None, "still gone: stay");
        assert_eq!(return_to(None, &both), None, "primary: the desktop decides");
        assert_eq!(return_to(Some("Virtual-1"), &list("", &["Virtual-0", "Virtual-1"])), None, "not shown yet");
    }

}

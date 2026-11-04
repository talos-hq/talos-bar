//! Splitting the bar into left / centre / right, the Qt bar's shape. Pure arithmetic over widths
//! measured by the gfx facade (`Frame::text_width`, `Frame::available_width`), so it is tested
//! without a compositor.

/// Width one label takes in a horizontal row: its measured text plus the item spacing after it.
pub fn item(text_width: f32, spacing: f32) -> f32 {
    text_width + spacing
}

#[derive(Debug, PartialEq)]
pub struct Split {
    /// How many of the remote zones fit (they are dropped from the end).
    pub zones: usize,
    /// Space before the centre group and before the right group.
    pub gap_left: f32,
    pub gap_right: f32,
}

/// `left`, `centre` and `right` are measured widths; `zones` are the optional per-zone widths
/// appended to the centre group. The centre is centred on the bar when it fits that way, and
/// pushed right of `left` otherwise; zones that do not fit at all are dropped.
pub fn split(width: f32, left: f32, centre: f32, zones: &[f32], right: f32) -> Split {
    let mut count = zones.len();
    let centre_of = |n: usize| centre + zones[..n].iter().sum::<f32>();
    while count > 0 && left + centre_of(count) + right > width {
        count -= 1;
    }
    let mid = centre_of(count);
    let free = (width - left - mid - right).max(0.0);
    let centred = (width - mid) / 2.0 - left;
    let gap_left = centred.clamp(0.0, free);
    Split { zones: count, gap_left, gap_right: free - gap_left }
}

/// `text` cut to fit `budget` logical px as measured by `measure`, ending in `…` when cut.
/// `None` when not even the ellipsis fits. Cuts on characters, never inside one.
pub fn truncate(text: &str, budget: f32, measure: impl Fn(&str) -> f32) -> Option<String> {
    if measure(text) <= budget {
        return Some(text.to_owned());
    }
    let chars: Vec<char> = text.chars().collect();
    let cut = |n: usize| format!("{}…", chars[..n].iter().collect::<String>().trim_end());
    // Widths grow with n: find the longest prefix that fits.
    let (mut lo, mut hi) = (0, chars.len());
    if measure(&cut(0)) > budget {
        return None;
    }
    while lo < hi {
        let mid = (lo + hi).div_ceil(2);
        if measure(&cut(mid)) <= budget {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }
    Some(cut(lo))
}

/// Time until just past the next whole second after `subsec_nanos`, so a seconds clock ticks
/// on the boundary and a minutes clock changes within a second of it.
pub fn until_next_second(subsec_nanos: u32) -> std::time::Duration {
    const NANOS: u32 = 1_000_000_000;
    // A couple of ms past the boundary so the frame lands on the new second, not just before.
    std::time::Duration::from_nanos(u64::from(NANOS - subsec_nanos.min(NANOS - 1)) + 2_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centre_is_centred_when_room() {
        let s = split(1000.0, 100.0, 200.0, &[], 100.0);
        assert_eq!(s, Split { zones: 0, gap_left: 300.0, gap_right: 300.0 });
    }

    #[test]
    fn a_wide_left_pushes_the_centre_right() {
        let s = split(1000.0, 500.0, 200.0, &[], 100.0);
        assert_eq!(s, Split { zones: 0, gap_left: 0.0, gap_right: 200.0 });
    }

    #[test]
    fn zones_drop_from_the_end_until_everything_fits() {
        let s = split(900.0, 100.0, 200.0, &[150.0, 150.0, 150.0, 150.0], 100.0);
        assert_eq!(s.zones, 3);
        assert!((s.gap_left + s.gap_right - 50.0).abs() < 1e-3);
        let tiny = split(300.0, 100.0, 200.0, &[50.0], 100.0);
        assert_eq!(tiny, Split { zones: 0, gap_left: 0.0, gap_right: 0.0 });
    }

    #[test]
    fn a_zone_that_exactly_fits_is_kept() {
        let s = split(600.0, 100.0, 200.0, &[100.0, 100.0], 100.0);
        assert_eq!(s, Split { zones: 2, gap_left: 0.0, gap_right: 0.0 });
    }

    #[test]
    fn truncate_cuts_to_the_budget_with_an_ellipsis() {
        #[allow(clippy::cast_precision_loss, reason = "short strings")]
        let width = |t: &str| t.chars().count() as f32 * 10.0;
        assert_eq!(truncate("10:00 AM · Sync", 500.0, width).as_deref(), Some("10:00 AM · Sync"));
        assert_eq!(truncate("10:00 AM · Sync", 100.0, width).as_deref(), Some("10:00 AM…"));
        assert_eq!(truncate("10:00 AM · Sync", 10.0, width).as_deref(), Some("…"));
        assert_eq!(truncate("10:00 AM · Sync", 9.0, width), None);
        assert!(truncate("ab cd", 30.0, width).is_some_and(|t| width(&t) <= 30.0));
    }

    #[test]
    fn item_adds_the_spacing() {
        assert!((item(40.0, 8.0) - 48.0).abs() < f32::EPSILON);
    }

    #[test]
    fn next_second_lands_just_past_the_boundary() {
        let d = until_next_second(999_000_000);
        assert!(d > std::time::Duration::from_millis(1) && d < std::time::Duration::from_millis(5), "{d:?}");
        let d = until_next_second(0);
        assert!(d > std::time::Duration::from_secs(1) && d < std::time::Duration::from_millis(1005), "{d:?}");
    }
}

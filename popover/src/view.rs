//! Popover state and the per-frame UI, independent of where results come from.

use crate::source::{Hit, Source};
use kernal_api::gfx::{App, Event, Frame, Key};

/// Height of the scrolling results region, in logical pixels.
pub const RESULTS_HEIGHT: f32 = 300.0;

/// What a key press did to the popover.
#[derive(Debug, PartialEq, Eq)]
pub enum Action {
    None,
    Activate,
    Close,
}

/// Selection and query, with keyboard navigation. Pure, so it is unit tested without a window.
#[derive(Default)]
pub struct Model {
    pub query: String,
    pub hits: Vec<Hit>,
    pub selected: usize,
    pub error: Option<String>,
    focused_once: bool,
}

impl Model {
    pub fn key(&mut self, key: Key) -> Action {
        match key {
            Key::ArrowDown if !self.hits.is_empty() => {
                self.selected = (self.selected + 1).min(self.hits.len() - 1);
            }
            Key::ArrowUp => self.selected = self.selected.saturating_sub(1),
            Key::Enter if self.selected < self.hits.len() => return Action::Activate,
            Key::Escape => return Action::Close,
            _ => {}
        }
        Action::None
    }

    /// Focus loss closes the popover, but only after it has been focused once: some hosts
    /// report "unfocused" before the compositor hands the new window focus.
    pub fn focus(&mut self, focused: bool) -> Action {
        if focused {
            self.focused_once = true;
            Action::None
        } else if self.focused_once {
            Action::Close
        } else {
            Action::None
        }
    }

    pub fn set_hits(&mut self, hits: Vec<Hit>) {
        self.hits = hits;
        self.selected = 0;
    }
}

pub struct Popover<S> {
    pub model: Model,
    source: S,
}

impl<S: Source> Popover<S> {
    /// A popover showing the results for `query`, which is prefilled in the field.
    pub fn new(source: S, query: String) -> Self {
        let mut popover = Self { model: Model { query, ..Model::default() }, source };
        popover.refresh();
        popover
    }

    fn refresh(&mut self) {
        match self.source.query(&self.model.query) {
            Ok(hits) => {
                self.model.error = None;
                self.model.set_hits(hits);
            }
            Err(err) => {
                self.model.error = Some(err.to_string());
                self.model.set_hits(Vec::new());
            }
        }
    }

    /// Activates the selected hit; `true` when the popover should close.
    fn activate(&mut self, index: usize) -> bool {
        let Some(hit) = self.model.hits.get(index) else {
            return false;
        };
        match self.source.activate(&hit.id) {
            Ok(true) => true,
            Ok(false) => {
                self.model.error = Some(format!("could not open {}", hit.title));
                false
            }
            Err(err) => {
                self.model.error = Some(err.to_string());
                false
            }
        }
    }
}

impl<S: Source> App for Popover<S> {
    fn frame(&mut self, frame: &mut Frame<'_>) {
        let mut action = Action::None;
        for event in frame.events() {
            let step = match event {
                Event::Key { key, pressed: true, .. } => self.model.key(*key),
                Event::Focused(focused) => self.model.focus(*focused),
                _ => Action::None,
            };
            if step != Action::None {
                action = step;
            }
        }
        if frame.text_field(&mut self.model.query) {
            self.refresh();
        }
        frame.separator();
        let mut clicked = None;
        let model = &self.model;
        frame.scroll(RESULTS_HEIGHT, |frame| {
            if model.hits.is_empty() {
                frame.label(if model.query.trim().is_empty() { "Type to search" } else { "No results" });
                return;
            }
            frame.list(|frame| {
                for (index, hit) in model.hits.iter().enumerate() {
                    let text = if hit.subtitle.is_empty() {
                        hit.title.clone()
                    } else {
                        format!("{}  —  {}", hit.title, hit.subtitle)
                    };
                    if frame.selectable_row(&text, index == model.selected) {
                        clicked = Some(index);
                    }
                }
            });
        });
        if let Some(error) = &self.model.error {
            frame.label(error);
        }
        let target = match (clicked, action) {
            (Some(index), _) => Some(index),
            (None, Action::Activate) => Some(self.model.selected),
            (None, Action::Close) => return frame.close(),
            (None, Action::None) => None,
        };
        if let Some(index) = target {
            self.model.selected = index;
            if self.activate(index) {
                frame.close();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hit(id: &str) -> Hit {
        Hit { id: id.into(), title: id.into(), subtitle: String::new() }
    }

    #[test]
    fn arrows_clamp_to_the_list_and_enter_activates() {
        let mut model = Model::default();
        assert_eq!(model.key(Key::Enter), Action::None, "nothing to activate");
        assert_eq!(model.key(Key::ArrowDown), Action::None);
        assert_eq!(model.selected, 0);
        model.set_hits(vec![hit("a"), hit("b")]);
        model.key(Key::ArrowDown);
        model.key(Key::ArrowDown);
        assert_eq!(model.selected, 1);
        model.key(Key::ArrowUp);
        model.key(Key::ArrowUp);
        assert_eq!(model.selected, 0);
        assert_eq!(model.key(Key::Enter), Action::Activate);
        assert_eq!(model.key(Key::Escape), Action::Close);
    }

    #[test]
    fn new_results_reset_the_selection() {
        let mut model = Model::default();
        model.set_hits(vec![hit("a"), hit("b")]);
        model.key(Key::ArrowDown);
        model.set_hits(vec![hit("c")]);
        assert_eq!(model.selected, 0);
    }

    #[test]
    fn focus_loss_closes_only_after_first_focus() {
        let mut model = Model::default();
        assert_eq!(model.focus(false), Action::None);
        assert_eq!(model.focus(true), Action::None);
        assert_eq!(model.focus(false), Action::Close);
    }
}

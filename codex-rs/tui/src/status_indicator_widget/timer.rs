//! Pause-aware status time owned independently of the optional status row.
//! A phase can override the displayed clock without changing turn accounting.

use std::time::Duration;
use std::time::Instant;

#[derive(Debug)]
pub(crate) struct StatusTimer {
    pub(super) output_rate: Option<super::output_rate::OutputRate>,
    /// Optional wall-clock origin for the displayed phase, without resetting turn accounting.
    pub(crate) display_started_at: Option<Instant>,
    pub(super) elapsed_running: Duration,
    pub(super) last_resume_at: Instant,
    pub(super) is_paused: bool,
}

impl Default for StatusTimer {
    fn default() -> Self {
        Self {
            output_rate: None,
            display_started_at: None,
            elapsed_running: Duration::ZERO,
            last_resume_at: Instant::now(),
            is_paused: false,
        }
    }
}

impl StatusTimer {
    pub(crate) fn start_output_rate(&mut self) {
        self.output_rate = Some(super::output_rate::OutputRate::new(Instant::now()));
    }

    pub(crate) fn record_output(&mut self, text: &str) {
        if let Some(rate) = &mut self.output_rate {
            rate.record(text, Instant::now());
        }
    }

    pub(crate) fn record_reasoning_output(&mut self, item_id: &str, text: &str, raw: bool) {
        if let Some(rate) = &mut self.output_rate {
            rate.record_reasoning(item_id, text, raw, Instant::now());
        }
    }

    pub(crate) fn has_output_rate(&self) -> bool {
        self.output_rate.is_some()
    }

    pub(crate) fn stop_output_rate(&mut self) {
        self.output_rate = None;
    }
    pub(crate) fn reset(&mut self, elapsed: Duration) {
        self.stop_output_rate();
        self.elapsed_running = elapsed;
        self.last_resume_at = Instant::now();
        // A turn can start while an MCP elicitation or approval is still open.
        // Reset elapsed time without changing that modal's pause state.
    }

    pub(crate) fn pause_at(&mut self, now: Instant) {
        if !self.is_paused {
            self.elapsed_running += now.saturating_duration_since(self.last_resume_at);
            self.is_paused = true;
        }
    }

    pub(crate) fn resume_at(&mut self, now: Instant) {
        if self.is_paused {
            self.last_resume_at = now;
            self.is_paused = false;
        }
    }

    pub(crate) fn elapsed_at(&self, now: Instant) -> Duration {
        self.elapsed_running
            + if self.is_paused {
                Duration::ZERO
            } else {
                now.saturating_duration_since(self.last_resume_at)
            }
    }
}

#[cfg(test)]
#[path = "timer_tests.rs"]
mod tests;

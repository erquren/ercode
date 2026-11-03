//! One-second output samples, estimated from streamed UTF-8 bytes rather than SSE chunks.
//! Only live model text is recorded; completed usage, tool output and replay are excluded.

use std::time::Duration;
use std::time::Instant;

#[derive(Debug)]
pub(super) struct OutputRate {
    started_at: Instant,
    second: u64,
    bytes: usize,
    previous_bytes: usize,
}

impl OutputRate {
    pub(super) fn new(now: Instant) -> Self {
        Self {
            started_at: now,
            second: 0,
            bytes: 0,
            previous_bytes: 0,
        }
    }

    pub(super) fn record(&mut self, text: &str, now: Instant) {
        let second = now.saturating_duration_since(self.started_at).as_secs();
        if second > self.second {
            self.previous_bytes = if second == self.second + 1 {
                self.bytes
            } else {
                0
            };
            self.bytes = 0;
            self.second = second;
        }
        self.bytes = self.bytes.saturating_add(text.len());
    }

    pub(super) fn tokens_per_second(&self, now: Instant) -> u64 {
        let second = now.saturating_duration_since(self.started_at).as_secs();
        let bytes = if second == self.second {
            self.previous_bytes
        } else if second == self.second + 1 {
            self.bytes
        } else {
            0
        };
        codex_utils_string::approx_tokens_from_byte_count(bytes)
    }

    pub(super) fn next_sample_in(&self, now: Instant) -> Duration {
        let elapsed = now.saturating_duration_since(self.started_at);
        Duration::from_secs(elapsed.as_secs() + 1).saturating_sub(elapsed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn samples_completed_seconds_and_expires_during_idle() {
        let start = Instant::now();
        let mut rate = OutputRate::new(start);
        rate.record(&"a".repeat(136), start + Duration::from_millis(100));
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_millis(999)),
            0
        );
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(1)), 34);
        rate.record(&"b".repeat(80), start + Duration::from_millis(1500));
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_millis(1999)),
            34
        );
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(2)), 20);
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(3)), 0);
        rate.record("test", start + Duration::from_secs(10));
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(10)), 0);
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(11)), 1);
    }

    #[test]
    fn chunk_boundaries_do_not_inflate_estimates() {
        let start = Instant::now();
        let mut single = OutputRate::new(start);
        let mut split = OutputRate::new(start);
        let text = "实时输出 token speed";
        single.record(text, start);
        for ch in text.chars() {
            split.record(&ch.to_string(), start);
        }
        let now = start + Duration::from_secs(1);
        assert_eq!(single.tokens_per_second(now), split.tokens_per_second(now));
        assert_eq!(
            single.next_sample_in(start + Duration::from_millis(250)),
            Duration::from_millis(750)
        );
    }
}

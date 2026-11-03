//! One-second output samples, estimated from streamed UTF-8 bytes rather than SSE chunks.
//! Only live model text is recorded; completed usage, tool output and replay are excluded.

use std::collections::HashMap;
use std::time::Duration;
use std::time::Instant;

#[derive(Debug, Default)]
struct ReasoningBytes {
    raw: usize,
    summary: usize,
}

#[derive(Debug, Default)]
struct Sample {
    output: usize,
    reasoning: HashMap<String, ReasoningBytes>,
}

impl Sample {
    fn tokens(&self) -> Option<u64> {
        let bytes = self
            .reasoning
            .values()
            .fold(self.output, |bytes, reasoning| {
                bytes.saturating_add(if reasoning.raw > 0 {
                    reasoning.raw
                } else {
                    reasoning.summary
                })
            });
        (bytes > 0).then(|| codex_utils_string::approx_tokens_from_byte_count(bytes))
    }
}

#[derive(Debug)]
pub(super) struct OutputRate {
    started_at: Instant,
    second: u64,
    sample: Sample,
    previous_sample: Sample,
}

impl OutputRate {
    pub(super) fn new(now: Instant) -> Self {
        Self {
            started_at: now,
            second: 0,
            sample: Sample::default(),
            previous_sample: Sample::default(),
        }
    }

    fn advance(&mut self, now: Instant) {
        let second = now.saturating_duration_since(self.started_at).as_secs();
        if second > self.second {
            self.previous_sample = if second == self.second + 1 {
                std::mem::take(&mut self.sample)
            } else {
                self.sample = Sample::default();
                Sample::default()
            };
            self.second = second;
        }
    }

    pub(super) fn record(&mut self, text: &str, now: Instant) {
        self.advance(now);
        self.sample.output = self.sample.output.saturating_add(text.len());
    }

    /// Prefer raw reasoning over its summary within each item and sample, regardless
    /// of whether the user has enabled displaying raw reasoning in the transcript.
    pub(super) fn record_reasoning(&mut self, item_id: &str, text: &str, raw: bool, now: Instant) {
        self.advance(now);
        let reasoning = self.sample.reasoning.entry(item_id.to_owned()).or_default();
        let bytes = if raw {
            &mut reasoning.raw
        } else {
            &mut reasoning.summary
        };
        *bytes = bytes.saturating_add(text.len());
    }

    /// No received text is not evidence of zero model generation (hidden reasoning,
    /// buffering and tool waits are indistinguishable here), so report no measurement.
    pub(super) fn tokens_per_second(&self, now: Instant) -> Option<u64> {
        let second = now.saturating_duration_since(self.started_at).as_secs();
        if second == self.second {
            self.previous_sample.tokens()
        } else if second == self.second + 1 {
            self.sample.tokens()
        } else {
            None
        }
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
            None
        );
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_secs(1)),
            Some(34)
        );
        rate.record(&"b".repeat(80), start + Duration::from_millis(1500));
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_millis(1999)),
            Some(34)
        );
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_secs(2)),
            Some(20)
        );
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(3)), None);
        rate.record("test", start + Duration::from_secs(10));
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_secs(10)),
            None
        );
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_secs(11)),
            Some(1)
        );
    }

    #[test]
    fn raw_reasoning_and_summaries_are_not_double_counted() {
        let start = Instant::now();
        let mut rate = OutputRate::new(start);
        rate.record_reasoning("raw-item", &"r".repeat(136), true, start);
        rate.record_reasoning("raw-item", &"s".repeat(40), false, start);
        rate.record_reasoning("summary-only", &"s".repeat(20), false, start);
        rate.record("text", start);
        assert_eq!(
            rate.tokens_per_second(start + Duration::from_secs(1)),
            Some(40)
        );
        assert_eq!(rate.tokens_per_second(start + Duration::from_secs(2)), None);
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

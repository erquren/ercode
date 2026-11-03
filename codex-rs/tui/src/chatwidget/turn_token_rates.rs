//! Request-weighted input/output averages based on provider usage, never rendered text.
//! Only measured model requests contribute time: gaps spent running tools are excluded.

use std::collections::HashSet;

#[derive(Debug, Default)]
pub(super) struct TurnTokenRates {
    responses: HashSet<String>,
    input_tokens: u128,
    output_tokens: u128,
    request_ms: u128,
    incomplete: bool,
}

impl TurnTokenRates {
    pub(super) fn record(
        &mut self,
        response_id: &str,
        usage: Option<(i64, i64)>,
        request_duration_ms: Option<u64>,
    ) {
        if !self.responses.insert(response_id.to_owned()) {
            return;
        }
        match (usage, request_duration_ms) {
            (Some((input, output)), Some(ms)) if input >= 0 && output >= 0 && ms > 0 => {
                self.input_tokens += input as u128;
                self.output_tokens += output as u128;
                self.request_ms += u128::from(ms);
            }
            _ => self.incomplete = true,
        }
    }

    pub(super) fn averages(&self) -> Option<(u64, u64)> {
        if self.incomplete || self.request_ms == 0 {
            return None;
        }
        let per_second = |tokens: u128| {
            u64::try_from((tokens * 1_000 + self.request_ms / 2) / self.request_ms)
                .unwrap_or(u64::MAX)
        };
        Some((
            per_second(self.input_tokens),
            per_second(self.output_tokens),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn averages_multiple_requests_without_tool_wait_time_or_duplicate_events() {
        let mut rates = TurnTokenRates::default();
        rates.record("first", Some((1_000, 100)), Some(2_000));
        // A 40-second build between these requests does not enter request duration.
        rates.record("second", Some((3_000, 36)), Some(2_000));
        rates.record("second", Some((3_000, 36)), Some(2_000));
        assert_eq!(rates.averages(), Some((1_000, 34)));
    }

    #[test]
    fn uses_weighted_request_time_and_supports_subsecond_requests() {
        let mut rates = TurnTokenRates::default();
        rates.record("first", Some((1_000, 100)), Some(100));
        rates.record("second", Some((1_000, 100)), Some(900));
        assert_eq!(rates.averages(), Some((2_000, 200)));
    }

    #[test]
    fn missing_usage_or_timing_does_not_produce_a_misleading_average() {
        for (usage, duration) in [
            (None, Some(100)),
            (Some((5, 5)), None),
            (Some((5, 5)), Some(0)),
            (Some((-1, 5)), Some(100)),
        ] {
            let mut rates = TurnTokenRates::default();
            rates.record("valid", Some((10, 5)), Some(100));
            rates.record("missing", usage, duration);
            assert_eq!(rates.averages(), None);
        }
        assert_eq!(TurnTokenRates::default().averages(), None);
    }
}

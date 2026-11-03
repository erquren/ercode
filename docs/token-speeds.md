# Token speeds

The live `Working` row and completed `Worked for` row use different measurements.

## Live output estimate

`~34t/s` estimates the model text received in the previous complete second using
UTF-8 bytes divided by four. It includes received reasoning even when raw
reasoning is hidden, prefers raw reasoning over its summary within an item and
sample, and excludes tool logs and replay. `--t/s` means no measurement is
available; it does not mean the model has stopped consuming tokens.

## Completed request averages

`Worked for 44s • in 1200t/s • out 34t/s • 2:12 PM` shows the turn's elapsed time
alongside separate input and output averages for its observed completed model
requests:

- Input rate: sum of reported `input_tokens` / sum of model request seconds.
- Output rate: sum of reported `output_tokens` / sum of model request seconds.

Input includes cached tokens reported by the provider. Reasoning tokens are
already part of reported output and are not added again. The two rates share
request duration; they are not separate measurements of hardware prefill and
decode speeds. Request time includes connection/setup, queueing, input processing
and generation. Gaps between requests spent waiting for builds, tools or approvals
are excluded. Tool execution overlapping an active model request is not subtracted.

The measurement is scoped to completed requests observed for the displayed thread
and turn, including measured foreground compaction requests. It is not an aggregate
of all subagents or unreported failed/background requests. Duplicate response IDs
are counted once. Missing usage or duration suppresses the averages. Historical
replay does not reconstruct missing request timings.

The completed averages require a backend that supplies `requestDurationMs`.
For local test packages sharing the same version number, use `er --no-daemon`
to exercise the updated backend rather than an older running daemon.

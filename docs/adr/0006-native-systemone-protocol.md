# ADR 0006: explicit native llama.cpp System One protocol

**Status:** Accepted

## Context

Decision-only models require the native API rather than a chat envelope. The
source contract is the [llama.cpp server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md),
section POST `/v1/systemone`. Live smoke tests exercised the native protocol but
do not establish broad classification, calibration, or security efficacy.

## Decision

Add `protocol: chat_completions|systemone`, defaulting to the existing chat path,
with `ONE_GUARD_PROTOCOL` override and installer `--protocol`. Validate its exact
endpoint path. No inference from URLs, ambient protocol changes, redirects, or
fallback on errors. `--allow-insecure-http` provides an explicit first-install
LAN HTTP opt-in. Existing endpoint settings are preserved and must match.

Native requests contain optional `model`, full bounded `{tool_name,args}` as
`state`, and exactly one `questions.risk`. The instructions combine the policy,
appended operator prompt, and an explicit risk proposition question. Prioritize
malicious sleeper-model exfiltration via GET queries, POST bodies, uploads and
inline scripts, plus disabling the guard; benign local work is not inherently
unsafe. Hidden script contents remain unobserved.

Use native `noul` for probability mode with true/false descriptions. `noul` is
p(risk=true); allow strictly below the configured local threshold. Equality
denies. Use native `choice` for yes/no with descriptions keyed `yes` (risk) and
`no` (safe). The response must contain exactly `model`, `answers` with only `risk`,
and `usage` with nonnegative integer `input_tokens` and zero `output_tokens`.

Noul answers must contain exactly `type: noul` and finite numeric `noul` in `[0,1]`.
Choice answers must contain exactly `type: choice`, `choice: yes|no`,
`probabilities: {yes:<p>,no:<p>}`, and finite numeric `confidence` in `[0,1]`.
Probabilities must be finite numeric `[0,1]`, sum to 1 within absolute tolerance
`1e-6`, and agree with the chosen maximum. Exact ties deny; only a strictly
winning `no` allows. Threshold does not apply to choice. Missing/extra fields,
questions, booleans, duplicate keys, non-finite values, and protocol mismatches
deny. Model aliases need not echo the requested model identifier.

Reuse transport bounds, full-argument limits, concurrency slots, secrets and
deadline semantics. Retain timeout default 5 seconds. The slow-endpoint follow-up
explicitly extends the supported range to 0.01 through 120 seconds and adds
installer `--timeout`, preserving existing settings when omitted. Services slower
than the effective budget fail closed. Hermes' separate host hook timeout defaults
to 30 seconds; warn when the plugin deadline reaches or exceeds it, recommending
at least timeout + 5 seconds or higher. Never mutate that global setting. Host
timeout 0 is unlimited and produces no warning. A 60-second plugin timeout needs
a separate host setting of at least 65 seconds; the installer warns but does not
mutate that global setting. Add an explicitly opted-in smoke script with synthetic
fixtures only, no actual tool execution or exfiltration
network traffic. It reports transport/parser failure separately from risk.

## Consequences

The documented native contract is covered by unit, loopback HTTP and installed
real-Hermes dispatcher/sentinel tests. Existing H1/H2/H3/M1 regressions and accepted
C1 boundary remain in place. Stub success establishes protocol handling, not
classification efficacy. No inference server, model, package or Hermes installation
is downloaded. Upstream warns probabilities are not guaranteed calibrated, and
models can impose their own token limits despite complete client serialization.
Future incompatible server response fields fail closed until explicitly supported.

# ADR 0003: use a bounded OpenAI-compatible judge contract

**Status:** Accepted

Extended by [ADR 0006](0006-native-systemone-protocol.md): chat completions remains the default, with explicit native System One support alongside it. The shared bounds, transport, model-setting and secret contracts below apply to both protocols.

## Context

The project has no selected judge provider. An OpenAI-compatible chat-completions contract supports local and remote endpoints without adding a runtime package or coupling the plugin to a vendor SDK.

## Decision

POST the bounded tool name and full arguments to the exact `/v1/chat/completions` path. Send no conversation history, follow no redirects, and ignore proxy environment settings. Default to literal loopback HTTP. Require HTTPS for remote endpoints unless the operator explicitly opts into insecure HTTP. Accept only a single strict response shape.

Store the optional model identifier as `plugins.entries.one-guard.settings.judge_model`. Hermes validates plugin-relative paths before reading settings and reserves `model`; its settings UI applies the same validation. Installer `--model` writes `judge_model`, and `ONE_GUARD_MODEL` retains precedence at runtime. Map the setting to internal `Config.model` and the OpenAI-compatible request field `model`, omitting that field when empty. YAML-only installation must work without environment overrides.

Bound capacity waiting and all response I/O by one monotonic deadline. Retain four transport slots until their workers exit, including non-cancellable system DNS. Header and body drip must not renew socket deadlines. Store the token in Hermes `.env` and resolve it through the profile-aware secret API. Environment overrides and custom prompts are trusted policy inputs and can weaken the gate or redirect sensitive arguments.

## Consequences

Tool arguments may contain secrets and are sent to the configured judge. The endpoint is a trust boundary. Oversized data is denied rather than truncated. The plugin does not redact arguments because removing content could hide the action being judged. The model's judgment remains advisory and cannot inspect scripts or effects not exposed in arguments.

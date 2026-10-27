# ADR 0001: use the native pre-tool hook

**Status:** Accepted

## Context

One Guard must judge tool requests before dispatch. Hermes provides a native plugin API and a `pre_tool_call` policy hook. Hermes also supports shell-script hooks, but a second subprocess-based installation and consent path is not needed for this plugin.

## Decision

Register one synchronous native `pre_tool_call` callback and return Hermes' block directive for every result other than a valid safe judgment. Do not filter tool names. Keep the integration in the plugin folder and do not patch Hermes core.

## Consequences

The guard covers calls reaching the Hermes dispatcher, not operating-system calls or side effects hidden inside a tool. Hermes runtime compatibility must be checked against source, including callback timeout and exception behavior. Current checked source blocks timed-out or raising pre-tool callbacks; older docs differed.

[ADR 0005](0005-accept-hook-visible-proposal-boundary.md) records the user's explicit Option 1 decision. The callback judges hook-visible proposals, not immutable final execution arguments. Post-judgment argument-rewriting hooks or middleware are excluded from supported deployment; C1 is accepted out of scope, not fixed.

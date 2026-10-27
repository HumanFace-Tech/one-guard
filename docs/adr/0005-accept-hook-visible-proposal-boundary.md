# ADR 0005: accept the hook-visible proposal boundary

**Status:** Accepted by explicit user decision, 2026-10-26 (Option 1)

## Context

C1 identified that Hermes peer hooks can modify arguments after One Guard judges a proposal. The native hook does not bind a verdict to immutable final execution arguments. Source probes reproduced this through direct tool dispatch and the normal agent executor.

## Decision

The user selected Option 1, a normal public-API native `pre_tool_call` plugin. Its scope is model-originated exfiltration attempts expressed in hook-visible GET URLs, POST bodies, commands, and inline scripts. The runtime and plugin environment are trusted. Supported deployment excludes post-judgment argument-rewriting hooks or middleware. Hostile installed extensions are outside scope.

Close C1 as an accepted out-of-scope limitation, not a fixed vulnerability. Retain the existing integration without middleware, private runtime monkeypatches, or Hermes core patches. Do not enable rewriting plugins as part of deployment.

## Consequences

One Guard judges the exact hook-visible proposal, not immutable final execution arguments. A safe judgment on the original proposal does not authorize or inspect a later rewrite. Keep this limitation prominent in the README and preserve a real-Hermes regression that demonstrates the unsupported configuration using benign sentinels. The regression covers direct dispatch and the agent executor without a model or dangerous command execution.

An offline source probe supplied the executor test shape. Production plugin code remains on the public API. Test-only UI/activity stubs do not change policy or dispatch behavior. See [research](../research.md) and [verification](../verification.md#c1-accepted-boundary-regression).

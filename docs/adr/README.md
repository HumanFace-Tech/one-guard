# Architecture decision records

These records capture the current One Guard design. Implementation details are documented in the [root README](../../README.md) and the [docs](../) pages, and upstream evidence is in [research notes](../research.md).

| ADR | Decision |
| --- | --- |
| [0001](0001-native-pre-tool-hook.md) | Use Hermes' native `pre_tool_call` hook. |
| [0002](0002-deny-on-uncertain-judgment.md) | Allow only an explicit, valid safe judgment. |
| [0003](0003-openai-compatible-judge-contract.md) | Use a bounded OpenAI-compatible chat-completions request. |
| [0004](0004-install-local-release-safely.md) | Install from a local release folder and preserve/verify user state. |
| [0005](0005-accept-hook-visible-proposal-boundary.md) | Accept C1 as out of scope; judge hook-visible proposals in a trusted runtime/plugin environment. |
| [0006](0006-native-systemone-protocol.md) | Add explicit native System One choice/Noul alongside default chat completions. |
| [0007](0007-guided-interactive-installer.md) | Add a guided six-step wizard with an optional `prompt_toolkit` UI and a dependency-free fallback. |

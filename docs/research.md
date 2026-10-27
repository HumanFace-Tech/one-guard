# Research and compatibility notes

This document records the local source evidence used for the design. It does not claim that the checked-out sources are current upstream releases.

## Source revisions checked

| Source | Revision |
| --- | --- | --- |
| Hermes Agent | `e5f2b15300a3d04978e4c07cc64adc7bcf8e7b1c` |
| Jev / TypeSafe reference | `5dddbeaac5be08281e6c3b372eeaba3639971f0a` |

The installer identifies a source tree structurally; it does not check out or enforce a particular Hermes revision. The tested source HEAD is therefore a compatibility baseline, not an installer version lock.

## Hermes hook behavior

In the checked Hermes source, `hermes_cli/plugins.py` documents the native `pre_tool_call` result directives and gives a `block` result precedence over approval requests (`_get_pre_tool_call_directive_details`, around lines 1907-1959). `hermes_cli/plugins_dispatch.py` marks `pre_tool_call` as fail-closed on callback timeout or a still-running callback (`_HOOK_TIMEOUT_FAIL_CLOSED_HOOKS`, around lines 59-67). `model_tools.py` calls the hook during tool dispatch and returns a plugin block before the tool runs (around lines 759-781).

This source behavior differs from older plugin documentation that described callback exceptions as fail-open. One Guard explicitly returns a block result for its own caught failures and does not rely only on the outer runtime. Do not generalize this source behavior to an unverified Hermes build.

Hermes also has shell hooks in `agent/shell_hooks.py` and `hermes_cli/config_defaults.py`, with timeout and `fail_closed` controls. One Guard chooses the native plugin interface because it directly returns the policy directive and avoids a separate shell subprocess configuration path. The choice is documented in [ADR 0001](adr/0001-native-pre-tool-hook.md).

### C1 proposal-to-execution boundary

An offline source probe demonstrated a peer `pre_tool_call` `modify` directive changing arguments after the original is judged, through both `model_tools.handle_function_call` and the agent executor. In the pinned source, `agent/tool_executor.py:672-686` obtains modified arguments from `_dispatch_pre_tool_call_hooks`; `:712-759` applies them before guardrails and dispatch. Hook-visible proposals are therefore not immutable final execution arguments.

The project regression `test_unsupported_peer_modify_can_execute_unjudged_args` uses the installed One Guard callback, actual HTTP stub judge, public peer hook registration, and real Hermes dispatch. It checks the judge receives `SAFE_SENTINEL` while a registry sentinel receives `CHANGED_SENTINEL`. No shell or model executes. The executor case follows the source probe and stubs only UI/activity bookkeeping.

The user explicitly selected Option 1. C1 is closed as an accepted out-of-scope limitation, not fixed. Supported deployment requires a trusted runtime/plugin environment and excludes post-judgment argument-rewriting hooks or middleware. Hostile installed extensions are outside scope. [ADR 0005](adr/0005-accept-hook-visible-proposal-boundary.md) records the decision; no middleware or private runtime patch is added.

## Hermes plugin settings paths

`hermes_cli/plugins.py:266-276` validates `PluginContext.get_config` keys before reading `plugins.entries.<id>.settings`. `hermes_cli/plugins_state.py:16-39` reserves root segments `model`, `plugins`, `security`, and `settings`; `judge_model` passes its segment validation. `hermes_cli/plugins_settings.py:102-106` filters manifest fields through that same validator, and `save_plugin_settings` validates paths before writing (`:128-151`).

One Guard therefore declares and reads `judge_model`, with installer `--model` writing that key. Runtime `ONE_GUARD_MODEL` overrides it; internal `Config.model` and the HTTP payload retain the API name `model`. The integration regression verifies the installed YAML value, its visibility through Hermes' actual settings UI helper, and a successful judge request with no policy environment overrides. The token is loaded from `.env` using the upstream startup loader. See [verification](verification.md) for the executed suite results.

`hermes_cli/plugins_settings.py:89-125` exposes a `type: secret` field as `env` and `has_value`, without a raw `value`. Its writer rejects secret values in YAML (`:128-143`). The supported credential route stores them in `.env`. `hermes_cli/env_loader.py:330-430` parses and expands dotenv assignments at startup, including `${...}` references. The installer rejects that sequence in literal credentials to prevent restart-time corruption. `agent/secret_scope.py:230-261` resolves active profile secrets; the runtime uses this resolver before starting transport workers.

## Jev and System One reference

The Jev clone describes a TypeSafe System One route and typed Noul answers, alongside other provider routes. Its `plugins/jev-judge/jev.py` sends TypeSafe requests to `/v1/systemone` and normalizes Noul answers. Its gate applies thresholds in code. One Guard copies no Jev implementation. It now supports explicit native `/v1/systemone` alongside the default generic `/v1/chat/completions` contract, based directly on the llama.cpp API reference.

System One's typed Noul probabilities must not be conflated with generic free-form LLM scores. One Guard's optional chat `probability` mode accepts a syntactically valid JSON number; native `probability` uses Noul p(risk=true). Neither provides calibration evidence. A threshold cannot make an uncalibrated score a reliable probability. Default mode remains yes/no (strict JSON verdict for chat, native binary choice for System One).

### Native llama.cpp contract

Read on 2026-10-26 from the primary [raw server README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md), section POST `/v1/systemone`. Context7's indexed llama.cpp documentation corroborates the request and keyed choice probabilities, but its generated example omits answer `type`; the primary README's explicit response example includes it and is the parser contract. The remote README is mutable, not a pinned server version.

- Request: optional `model`, JSON `state`, and keyed `questions` with `type`, `instructions`, and typed `criteria`.
- Noul criteria describe `true` and `false`; answer has `type: noul`, `noul` p(true).
- Choice criteria map labels to descriptions; answer has `type: choice`, chosen label, a probabilities object keyed by label (sum 1), and confidence in `[0,1]`.
- Response has `model`, `answers`, and `usage` (`input_tokens`, `output_tokens: 0`).
- Non-decision models return 501. Redirects and non-200 responses deny locally without fallback.
- Upstream warns probabilities may be uncalibrated and h2o-lightning states exceeding 32000 tokens keep only the first and last 16000. Complete bounded serialization cannot prove complete model-side processing.

Past live smoke tests contacted a private LAN judge. GET `/v1/models` exposed `h2o-lightning-4b` with decisions capability. Earlier POST attempts timed out at 15 seconds, and the initial six-case smoke timed out at 10 seconds per case. Subsequent full live smoke runs passed all six synthetic cases in both Noul probability and binary yes/no modes with `--timeout 60`; requests took 19.479 to 21.831 seconds. These twelve outcomes do not establish broad classification efficacy, calibration, or prompt-injection resistance. The private address is intentionally omitted. The default stays 5 seconds, with an explicitly supported maximum of 120. Hermes' independent `plugins.hook_callback_timeout` defaults to 30; a 60-second plugin timeout needs a separate host budget of at least 65 seconds, or higher for host overhead. The installer warns about an insufficient host budget and never changes it; 0 is unlimited. [ADR 0006](adr/0006-native-systemone-protocol.md) records strict schemas, policy direction and protocol selection. The opt-in smoke script sends only synthetic proposals to the chosen judge; it never executes them. Normal automated tests contact loopback stubs only.

The Jev reference's README makes failure handling and calibration claims for its own implementation. Those claims do not transfer to One Guard. One Guard does not claim real-model effectiveness or security efficacy. The tests described in [docs/verification.md](verification.md) should be read as transport/runtime verification only unless they explicitly document a separately evaluated model.

## Scope and information exposure

Hermes' native hook receives the tool name and arguments at dispatch. It does not provide syscall interception. A browser, terminal, MCP, or other tool may perform internal work that is not fully represented by callback arguments. Likewise, a shell command containing `sh /path/to/hidden.sh` does not reveal that script's contents. One Guard forwards inline arguments intact, does not open referenced files or decode payloads, and asks the judge to treat missing script context conservatively.

Since arguments may contain secrets, a remote judge receives sensitive tool data. The endpoint operator must be trusted for that data. HTTPS protects transport but does not change the recipient's trust status. Local isolation and egress controls are required for stronger guarantees.

## Installer source evidence

`scripts/install.py` defines the source selector, settings merge, validation, backups, and ownership marker. `HERMES_DIR`, the resolved `hermes` executable, and common source locations are discovery inputs. `--hermes-dir` resolves ambiguity; `--hermes-home` or `HERMES_HOME` selects the data home. PyYAML and python-dotenv come from the running interpreter or Hermes `.venv`; the installer installs neither. Written files and new directories have private modes; existing directory permissions remain unchanged. Unsafe links and unowned or changed plugin directories are refused.

`hermes_cli/config_migrations.py:285-332` defines v21 grandfathering. When `plugins.enabled` is absent, it scans sorted immediate user-plugin directories for `plugin.yaml` or `plugin.yml`, takes each manifest name or directory fallback, and excludes disabled names. The migration registry (`:858-892`) runs v21 only on stamped pre-v21 configs, not unversioned or modern configs. The installer follows that selection without executing plugins and keeps modern configs opt-in. Unversioned configs with installed plugins require explicit enablement review. Unsafe or ambiguous manifests, portable-only packages, and nested legacy layouts are refused rather than silently changing effective enablement.

The documented installation entry point is `sh ./install.sh` from a local checkout.

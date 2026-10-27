# One Guard internal reference

This file records implementation contracts for maintainers and code agents. The user-facing installation and configuration guide is [README.md](README.md), with the details in [docs/installation.md](docs/installation.md), [docs/configuration.md](docs/configuration.md), and [docs/threat-model.md](docs/threat-model.md). Do not treat model judgment as a sandbox boundary.

## Source baseline

The verified upstream Hermes source was at `e5f2b15300a3d04978e4c07cc64adc7bcf8e7b1c`. The related Jev source was at `5dddbeaac5be08281e6c3b372eeaba3639971f0a`. Evidence is in [docs/research.md](docs/research.md). Do not copy Jev implementation code or describe this plugin as using System One unless its actual endpoint does so.

Hermes dispatcher APIs can change. Recheck callback keyword arguments, directive parsing, timeout behavior, and runtime loading against the selected Hermes source HEAD whenever compatibility changes. In the cited Hermes source, pre-tool callback exceptions and timeouts fail closed; older docs described failures as fail-open.

## Runtime path

`plugins/one-guard/__init__.py` registers `Guard.pre_tool_call` for the native `pre_tool_call` hook. The hook accepts `tool_name`, `args`, and additional keyword metadata. It returns `None` only after a safe judgment; otherwise it returns `{"action":"block","message":...}`. There are no per-tool exclusions, shadow mode, verdict cache, or extra plugin tools.

`guard.py` loads environment overrides over Hermes plugin config and resolves the token with Hermes' profile-aware secret API. It validates tool data, builds a bounded chat-completions or native System One request, validates the response, then applies policy. Every malformed, uncertain, unavailable, or failed judgment denies. One monotonic deadline covers capacity waiting and transport; concurrency is capped at four workers. Every socket read recomputes its timeout. Non-cancellable system DNS may retain a slot after the caller denies; never release a slot before its worker exits.

The model setting is `plugins.entries.one-guard.settings.judge_model`, declared in `DEFAULTS` and `plugin.yaml`. Installer `--model` writes this key; runtime `ONE_GUARD_MODEL` overrides it and maps to internal `Config.model` and the API payload's `model`. Hermes reserves `model` as a plugin-relative key in `plugins_state._plugin_relative_segments`; never read it through `ctx.get_config`. The integration regression verifies YAML-only success and the exact model without any `ONE_GUARD_*` environment variables.

The hook gates calls delivered to Hermes' dispatcher. It is not syscall interception. It cannot see network requests, file reads, subprocesses, or other side effects hidden inside tool implementations beyond what appears in callback arguments. Hermes hooks and model judgment do not supply OS isolation.

The user selected Option 1, the normal public-API native `pre_tool_call` plugin. Scope is model-originated exfiltration attempts in hook-visible GET/POST proposals, commands, and inline scripts, in a trusted runtime/plugin environment. Supported deployment excludes post-judgment argument-rewriting hooks or middleware and hostile installed extensions. The judged proposal is not immutable final execution arguments. C1 is closed as an accepted out-of-scope limitation, not fixed. Do not add middleware, private runtime monkeypatches, or Hermes core patches to address C1. Preserve the benign peer-`modify` regression through direct dispatch and the agent executor. See ADR 0005.

## Request and response invariants

- `protocol` / `ONE_GUARD_PROTOCOL` is `chat_completions` (default) or `systemone`. The endpoint path must be exactly `/v1/chat/completions` or `/v1/systemone` respectively; no query, fragment, or embedded credentials. Never infer, fall back, or switch protocols on failure.
- HTTP is allowed by default only for literal loopback IP addresses. Hostnames such as `localhost` require HTTPS or `allow_insecure_http: true`.
- The payload includes the full bounded `tool_name` and `args`, but no conversation history. Tool argument data is untrusted prompt input.
- No argument truncation, redirect following, or ambient proxy use. Limits are 128 KiB tool data, 256 KiB serialized request, and 64 KiB response.
- Chat response requires one completed assistant choice and strict JSON content. Refusals, tool calls, malformed JSON, duplicate keys, and non-finite numbers deny.
- Chat yes/no accepts only `{"verdict":"yes"}` or `{"verdict":"no"}`. Numeric probability must be a bare JSON number in `[0,1]`; it allows only when strictly below threshold.
- Native requests send full bounded `{tool_name,args}` as `state`, and exactly one `questions.risk`. Probability uses `type: noul` and true/false criteria; yes/no uses `type: choice` and yes/no criteria. See ADR 0006.
- Native response requires exactly `{model,answers,usage}`, only `answers.risk`, and documented token counters. Noul answer is exactly `{type:'noul',noul:<finite [0,1]>}`. Choice is exactly `{type:'choice',choice:'yes'|'no',probabilities:{yes:<p>,no:<p>},confidence:<p>}`. Probabilities sum to 1 within `1e-6`; inconsistent choice/max denies, ties deny, only a strictly winning `no` allows. Threshold applies only to probability mode. Missing/extra fields deny.
- `prompt` is appended to the built-in prompt. Operators and their environment overrides are trusted; appended instructions can contradict or weaken policy.
- Inline scripts are judged only when included in the arguments. Referenced or hidden scripts are not fetched, opened, decoded, or executed for inspection.

TypeSafe System One's Noul probability output is not interchangeable with a generic LLM-generated numeric score. Neither protocol has a calibration guarantee. Initial live smoke timed out in all six cases at 10 seconds. Later full live smoke runs passed six synthetic cases in both probability and yes/no modes with a 60-second deadline; each request took about 19.5 to 21.9 seconds. These twelve outcomes are not proof of broad classification efficacy, calibration, or prompt-injection resistance. See [the verification record](docs/verification.md#live-endpoint-observations) for commands and cases. Timeout default remains 5, with a supported range of 0.01 through 120 seconds. Installer `--timeout` preserves existing settings when absent. Warn if the configured/preserved timeout reaches or exceeds Hermes' `plugins.hook_callback_timeout` (default 30), recommending at least timeout + 5 seconds; never change that global setting. A 60-second plugin timeout needs a separate Hermes host timeout of at least 65 seconds. Host timeout 0 is unlimited and needs no warning. The opt-in `scripts/smoke-systemone.py --run-live` sends synthetic proposals only and never executes them. Normal automated tests use a local stub.

## Installer invariants

`install.sh` dispatches to `scripts/install.py` with Python 3. The installer selects Hermes source separately from Hermes data home; it can discover a source from `HERMES_DIR`, the executable, and common locations. Ambiguity requires an interactive selection or explicit `--hermes-dir`, including with `--yes`.

`scripts/install.py` imports `scripts/installer_ui.py` and `scripts/installer_bootstrap.py` through `importlib` by path, not as packages, so all three files must stay siblings and must not rely on relative imports. `installer_ui` owns terminal rendering. `PlainUI` is the always-available fallback; `ToolkitUI` uses `prompt_toolkit` for arrow-driven menus, masked input, and inline validation. `create_ui` requires a TTY on stdin and stdout. `--plain`, `--yes`, `TERM=dumb`, or any set `NO_COLOR` disables enhanced UI selection and bootstrap. Otherwise, source selection starts in plain mode; if the UI is missing, the installer asks before downloading its pinned requirements into a temporary isolated virtual environment. `installer_bootstrap.provision()` removes that environment on exit and cleans its imports. It installs only the optional UI packages, not PyYAML/python-dotenv, Hermes, or runtime dependencies, and does not modify system Python or Hermes. Declining or failing to provision keeps the plain wizard available.

The wizard flow is: source selection, data home, judge connection, decision policy, authentication, review. Every step is re-entrant from the review screen. `run_wizard` returns only after the user confirms. Cancel, Ctrl-C, and EOF raise `Cancelled` before any filesystem write, so the target home is never created. `--yes` skips `create_ui` entirely and never prompts.

Validation lives in the installer, not only in the UI: `validate_source`, `validate_home`, `number_field`, and the endpoint and model checks reuse `merge_config` with a throwaway `argparse.Namespace`, so the wizard and the CLI apply identical rules. `trial_args` builds that namespace. Endpoint failures in the wizard mention the transport-policy step instead of CLI flags, via `endpoint_error`.

Terminal output passes through `installer_ui.safe_text`, which escapes control characters, and `UI.clean`, which replaces known secrets with `[credential]`. Never print a token, and never echo one from a validation error. The review screen reports credential presence only.

Installer `--protocol` selects the explicit protocol. First-install default endpoint follows that selection; existing endpoints are preserved and must match. `--allow-insecure-http` enables first-install LAN HTTP without preexisting YAML. No inference server, model, or Hermes installation is performed.

PyYAML and python-dotenv must already exist in the current interpreter or Hermes `.venv`; the installer does not install them. Only after explicit consent, the optional UI bootstrap runs pip inside a temporary isolated virtual environment with pinned, hash-checked UI requirements; it cleans that environment on exit. Settings merge into existing YAML; comments/formatting may change. Absent `plugins.enabled` on a pre-v21 stamped config preserves upstream top-level user-plugin grandfathering, excluding disabled manifest names. Modern configs remain opt-in. Unversioned configs with installed plugins and ambiguous legacy layouts require explicit enablement before writes. Unsafe YAML, symlinks, hard links, unowned target directories, and changed ownership hashes stop installation. Written files use `0600`, new directories `0700`; existing directory permissions stay unchanged. Atomic per-file replacement uses a random backup directory and rolls back plugin/config/`.env` on failure. Installer code is POSIX-oriented (`fcntl`).

The token comes from `ONE_GUARD_TOKEN` or a hidden interactive prompt and is written to Hermes `.env`. The manifest declares `type: secret`, `env: ONE_GUARD_TOKEN`; dashboard fields expose presence only. Without a supplied token, preserve `.env` or migrate a legacy YAML token, removing that YAML setting. Reject ambiguous token assignments and `${` interpolation. Backups can contain credentials. No `--token` argument is offered. Never add secrets to logs, manifests, errors, or examples.

## AI-assisted rollout

Read the README and inspect the target environment before helping with installation. Confirm with the human which Hermes source checkout, data home, judge endpoint, and model to use. Do not install Hermes, an inference service, or a model as part of this plugin setup.

Ask the human to enter credentials directly in the masked wizard prompt; do not request, display, or log them in chat. Obtain explicit approval before enabling plaintext HTTP to a remote judge: tool arguments and credentials would travel unencrypted. The human can operate the wizard in a terminal; `--yes` is available for explicitly approved non-interactive setups.

Explain and obtain approval before changing Hermes' global `plugins.hook_callback_timeout`. The installer warns but does not change it. Stop or restart Hermes only with the operator's approval. Verify plugin loading and safe/denied behavior with inert proposals, without executing suspicious commands or contacting their embedded destinations. The guard is not a sandbox; post-judgment rewriting hooks are unsupported, and the live judge previously allowed a guard-removal proposal.

## Documentation and verification ownership

The ADR system in `docs/adr/` is public and visible to everyone, as is this repository. Before writing or updating an ADR, review it for disclosure: never include credentials, tokens, private endpoints or network addresses, identifying local paths, personal data, confidential infrastructure details, or private incident material. Use fictional placeholders and public sources where needed; record the architectural decision without carrying sensitive deployment details into the ADR. Review existing and changed ADRs before publication.

Keep README configuration names and constraints synchronized with `DEFAULTS`, `Config.validate`, `plugin.yaml`, and `merge_config`. Keep installation steps synchronized with `scripts/install.py`, `scripts/installer_ui.py`, and `install.sh`. Document test scope, not only test commands. [docs/verification.md](docs/verification.md) records executed Hermes-runtime integration results; do not invent results or claim that stub tests establish model efficacy.

Wizard changes need a real terminal check, not only mocked input. `tests/test_installer_terminal.py` drives the installer through a pseudo-terminal with arrow keys and asserts the resulting `config.yaml`. `tests/test_installer_wizard.py` covers the flow logic with a scripted UI. Keep both: the scripted tests are fast, and the pseudo-terminal tests catch rendering and keybinding regressions that mocks cannot.

See [ADRs](docs/adr/README.md) for accepted design decisions. Documentation must not silently change the project's scope or implementation claims.

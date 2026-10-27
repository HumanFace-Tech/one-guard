# One Guard

One Guard is a native Hermes plugin that asks a chat-completions or native llama.cpp System One judge to assess each hook-visible tool proposal delivered to its `pre_tool_call` callback. The callback allows only a validated safe judgment. Risk, uncertainty, invalid input, transport errors, and timeouts deny the proposal.

One Guard is an advisory model gate, not an OS sandbox or a guarantee against data exfiltration. It cannot inspect a tool's internal network activity or stop a user with equivalent filesystem access from disabling it. Use process isolation and network egress controls when enforcement must not depend on model judgment.

## Threat model and supported deployment

The scope is model-originated exfiltration attempts expressed in tool proposals, including GET URLs, POST bodies, commands, and inline scripts. One Guard judges the exact hook-visible proposal, not immutable final execution arguments. Detection depends on the judge and the context visible in those arguments.

The supported deployment requires a trusted Hermes runtime and plugin environment. It excludes post-judgment argument-rewriting hooks or middleware. A peer `pre_tool_call` hook can return `modify` after One Guard judges the original arguments, and Hermes can execute the changed arguments without another judgment. Hostile installed extensions and runtime tampering are outside the threat model.

C1 is closed as an accepted out-of-scope limitation by user decision, not fixed. The [boundary regression](docs/verification.md#c1-accepted-boundary-regression) demonstrates this unsupported configuration with benign sentinel strings through real Hermes dispatch and the agent executor. [ADR 0005](docs/adr/0005-accept-hook-visible-proposal-boundary.md) records the decision to retain the normal public-API native plugin.

## Compatibility

The integration was checked against Hermes source HEAD `e5f2b15300a3d04978e4c07cc64adc7bcf8e7b1c`. The plugin targets the native plugin API and `pre_tool_call` directive shape in that checkout. Recheck the hook contract when upgrading Hermes.

In that source revision, Hermes blocks a `pre_tool_call` callback that raises or times out. Older Hermes documentation described callback failures as fail-open. One Guard also returns an explicit block directive for its own failures, but do not assume the runtime behavior carries forward to other Hermes versions. The source and references are summarized in [research notes](docs/research.md).

## Install from a local checkout

This repository has no published release URL. Clone or otherwise obtain the project locally, stop Hermes, and run the installer from the checkout:

```sh
sh ./install.sh
```

That opens the interactive setup. It walks through six steps and writes nothing until you confirm on the review screen.

```text
ONE GUARD
A native Hermes tool guard · Configure, review, then install · Ctrl-C cancels

01 / Hermes source       pick a discovered checkout, or type a path
02 / Hermes data home    where config.yaml, .env, and the plugin are written
03 / Judge connection    protocol, transport policy, endpoint, model
04 / Decision policy     yes/no or probability, timeout, threshold
05 / Authentication      keep, replace, or clear the bearer credential
06 / Review              the effective settings, then install or cancel
```

With the optional terminal UI, use Up/Down, `j`/`k`, or Tab to move, Enter to select, and number keys to choose an option. Esc cancels in arrow-key menus; Ctrl-C cancels throughout setup. Text fields validate on submission and use the displayed default when you press Enter on an empty field. Credential input is hidden and never written to history.

The installer uses a numbered wizard when `prompt_toolkit` is unavailable, `NO_COLOR` is set, or `TERM=dumb`. Force it with `--plain`; this is also a workaround if arrow-key rendering fails. Installation requires a POSIX environment.

Nothing is written until the review screen. Cancelling, pressing Ctrl-C, or reaching end-of-file leaves the target directory untouched. From the review screen you can go back and change the judge, the credential, the destination, or the source before installing.

The installer detects a Hermes source checkout using `HERMES_DIR`, the `hermes` executable, and common source locations. If it finds multiple candidates, select one in the wizard or pass `--hermes-dir`. This source selector is distinct from the Hermes data home where files are installed.

For non-interactive installation, choose both the source and data home explicitly and pass `--yes`:

```sh
ONE_GUARD_TOKEN='your-token' sh ./install.sh \
  --yes \
  --hermes-dir /path/to/hermes-agent \
  --hermes-home "$HOME/.hermes" \
  --endpoint http://127.0.0.1:8080/v1/chat/completions \
  --model local-judge \
  --response-mode yes_no
```

`--yes` never prompts and never opens the wizard. The same flags seed the wizard's fields when it is used, so `--endpoint`, `--model`, `--timeout`, `--threshold`, `--protocol`, and `--response-mode` prefill and can still be edited before the review screen.

`--hermes-dir` must point to a Hermes source tree, not the Hermes data home. The installer uses existing PyYAML and python-dotenv from the active Python environment or Hermes' `.venv`. If neither environment has both, it stops with an installation hint. `install.sh` selects `python3`, then `python`, and reports an error if neither is available. The interactive menus use `prompt_toolkit` when it is importable and fall back to the plain wizard otherwise; the installer never installs a package for itself.

For the existing native decision service, use explicit protocol and LAN HTTP opt-in:

```sh
sh ./install.sh --yes \
  --hermes-dir /path/to/hermes-agent \
  --hermes-home "$HOME/.hermes" \
  --protocol systemone \
  --endpoint http://judge.example.test:9292/v1/systemone \
  --model h2o-lightning-4b \
  --response-mode probability \
  --timeout 60 \
  --allow-insecure-http
```

This is an example command, not a recorded installation. The live smoke contacted the judge service, but no Hermes installation was performed. The installer does not install Hermes, an inference server, or a model. `--protocol` accepts `chat_completions` or `systemone`; omitted values preserve existing settings, defaulting to chat completions on first install. A new native installation without `--endpoint` defaults to `http://127.0.0.1:8080/v1/systemone`. When changing an existing installation's protocol, supply the matching endpoint too: the installer never rewrites an existing endpoint or guesses a protocol from its path. `--allow-insecure-http` writes the opt-in on first installation; it does not require a preexisting config file.

`--timeout` accepts a finite number of seconds from `0.01` through `120`. When omitted, it preserves an existing timeout; new installations use the unchanged runtime default of 5 seconds. Hermes separately defaults `plugins.hook_callback_timeout` to 30 seconds. For the 60-second h2o example, explicitly merge this host setting into the selected Hermes home's `config.yaml`:

```yaml
plugins:
  hook_callback_timeout: 65
```

Choose a host budget at least `timeout + 5` seconds, or appropriately higher for host overhead. The 60-second smoke timeout therefore needs a separate Hermes `plugins.hook_callback_timeout` value of at least 65 seconds. This is a global setting affecting other hooks. The installer warns when the plugin timeout reaches or exceeds the host budget, but never changes that setting. A host value of `0` disables that host limit and produces no budget warning. Account for runtime `ONE_GUARD_TIMEOUT` overrides when choosing the host budget too. No real Hermes installation was performed.

The installer saves the token as `ONE_GUARD_TOKEN` in the selected Hermes home's `.env`. It reads `ONE_GUARD_TOKEN` or prompts without echo; the value is not a CLI option. In the wizard, the credential step can keep the current value, replace it with a masked input, or remove it. The review screen reports only whether a credential is present. The config file, `.env`, plugin files, and installer backups are written with mode `0600`. New directories use `0700`; existing directory permissions are unchanged. Stop Hermes before installing or updating so it cannot load a partially replaced plugin.

The installer preserves unrelated YAML values and merges the plugin's enablement and settings. It may rewrite YAML formatting and comments. For a config stamped `_config_version` below 21 with no `plugins.enabled`, it preserves Hermes' v21 grandfathering of installed top-level user plugins, using manifest names (or directory names when absent) and excluding `plugins.disabled`. A modern config with no enabled list, or an explicit empty list, stays opt-in. An unversioned config with installed plugins and no enabled list requires explicit review, as do ambiguous legacy manifests or layouts. Set `plugins.enabled` to the intended list before retrying; preflight refuses ambiguity before writes. Bundled plugins are not grandfathered.

The installer rejects malformed or duplicate YAML keys, unsafe links, unowned plugin directories, and modified files in an installer-owned plugin directory. It atomically replaces each file and rolls back the plugin, config, and `.env` together on a replacement failure. This is not a crash-atomic transaction across files. Each successful run reports a randomly named backup directory. Review the selected source, data home, and backup path in the output.

## Configure

The installer enables the plugin at `plugins.enabled` and writes non-secret settings under `plugins.entries.one-guard.settings` in `config.yaml`. Runtime environment variables named `ONE_GUARD_<SETTING>` override settings from Hermes config, except `judge_model` uses `ONE_GUARD_MODEL`. Installer `--model` writes `judge_model`; the judge request uses the API field `model`. Hermes reserves the plugin key `model`, so use `judge_model` in YAML. The token uses Hermes' secret resolution and `.env` loading at startup, including profile-scoped secrets. Values for numeric and boolean environment settings must be valid JSON literals, such as `0.4`, `5`, or `true`.

| Setting | Default | Contract |
| --- | --- | --- |
| `protocol` | `chat_completions` | `chat_completions` or `systemone`. No automatic switching or fallback. Environment override: `ONE_GUARD_PROTOCOL`. |
| `endpoint` | `http://127.0.0.1:8080/v1/chat/completions` | Exact `/v1/chat/completions` for chat or `/v1/systemone` for native. Runtime protocol changes require a matching endpoint. URLs with credentials, query, fragment, whitespace, or a different path are rejected. |
| `token` (secret field) | `""` | Optional bearer token stored as `ONE_GUARD_TOKEN` in Hermes `.env`, never as a YAML setting. Printable ASCII, at most 16384 characters. Installer rejects `${` because Hermes expands dotenv references. |
| `judge_model` | `""` | Optional model identifier. Sent as `model` in the request; omitted when empty. Overridden by `ONE_GUARD_MODEL`. |
| `prompt` | `""` | Optional text appended to the built-in system prompt. It augments rather than replaces the default policy. |
| `mode` | `yes_no` | Chat: exact JSON verdict or bare number. Native: binary `choice` for `yes_no`, `noul` for `probability`. See schemas below. |
| `timeout` | `5.0` seconds | Finite number from `0.01` through `120.0`. One monotonic deadline covers serialization, capacity waiting, connection, headers, body, and verdict parsing. Hermes' separate host hook budget also applies. DNS caveat below. |
| `threshold` | `0.5` | Finite number from `0.0` through `1.0`, used only in probability mode. A score equal to the threshold denies. |
| `allow_insecure_http` | `false` | Allows remote HTTP only when explicitly enabled. HTTPS and literal loopback IP HTTP are accepted by default. |

Environment overrides use `ONE_GUARD_PROTOCOL`, `ONE_GUARD_ENDPOINT`, `ONE_GUARD_TOKEN`, `ONE_GUARD_MODEL`, `ONE_GUARD_PROMPT`, `ONE_GUARD_MODE`, `ONE_GUARD_TIMEOUT`, `ONE_GUARD_THRESHOLD`, and `ONE_GUARD_ALLOW_INSECURE_HTTP`. For example:

```sh
ONE_GUARD_TOKEN='your-token' \
ONE_GUARD_THRESHOLD=0.4 \
ONE_GUARD_ALLOW_INSECURE_HTTP=false \
hermes
```

`localhost` is a hostname, not a literal loopback IP, so `http://localhost:...` is rejected unless `allow_insecure_http` is true. Prefer a literal loopback address or HTTPS. Remote HTTP sends tool arguments without transport encryption and should be avoided.

## Judge request and response

For every callback, the plugin sends a POST with JSON content and an optional `Authorization: Bearer ...` header. Chat requests contain one system message and one user message. The user message is a JSON object with `tool_name` and the complete `args`; native requests put that object in `state`. Neither includes conversation history. The plugin does not follow redirects or ambient proxy settings.

The combined UTF-8 JSON object containing `tool_name` and `args`, including JSON syntax and escaping, is limited to 128 KiB. The full serialized request is limited to 256 KiB, including the second layer of escaping when that JSON becomes a message string. A response is limited to 64 KiB. Inputs must be finite JSON-compatible values with bounded nesting. One Guard denies rather than truncating oversized input.

At most four transport workers run concurrently. Additional calls wait for capacity within their original deadline, so eight ordinary simultaneous calls can complete in two waves. Socket connect, TLS, writes, and each raw header/body read use the remaining deadline. Slow-drip responses cannot renew it and sockets close when the exchange ends. The caller denies on deadline expiry. Python's system DNS resolver cannot be cancelled reliably; a stuck resolver may retain a worker and slot after that deadline. Slots stay reserved until workers actually exit, bounding thread creation even during DNS failure. Literal IP endpoints avoid DNS lookup delays.

The OpenAI-compatible response must have a single `choices` entry, a `stop` finish reason, an assistant message with string content, and no refusal or tool call. The content must itself be strict JSON:

```json
{"verdict":"no"}
```

In `yes_no` mode the only accepted object is `{"verdict":"yes"}` or `{"verdict":"no"}` with no extra keys. `no` allows and `yes` denies. In `probability` mode the content must be a bare finite JSON number from 0 to 1. A value below the configured threshold allows; equality or greater denies. Boolean values, quoted numbers, NaN, and extra response fields are invalid.

### Native System One

With `protocol: systemone`, the POST follows the [llama.cpp native decision API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1systemone-typesafe-compatible-system-one-api). Probability mode sends:

```json
{
  "model": "h2o-lightning-4b",
  "state": {"tool_name": "terminal", "args": {"command": "pwd"}},
  "questions": {
    "risk": {
      "type": "noul",
      "instructions": "<built-in policy, appended prompt, and risk proposition question>",
      "criteria": {"true": "Risk/unsafe/exfiltration", "false": "Safe/no exfiltration"}
    }
  }
}
```

The parser requires exactly `model` (nonempty string), `answers` (only `risk`), and `usage` (nonnegative integer `input_tokens`, integer `output_tokens: 0`). A probability answer must be exactly:

```json
{"type":"noul","noul":0.123}
```

Noul is p(risk=true). Only a finite number in `[0,1]` strictly below `threshold` allows; equality denies. `yes_no` instead sends `type: choice` with criteria `{"yes":"Risk/unsafe/exfiltration","no":"Safe/no exfiltration"}` and requires exactly:

```json
{"type":"choice","choice":"no","probabilities":{"yes":0.1,"no":0.9},"confidence":0.8}
```

Choice must be `yes` or `no`, both named probabilities and confidence must be finite numbers in `[0,1]`, probabilities must sum to 1 within absolute tolerance `1e-6`, and choice must agree with their maximum. Ties deny. `no` allows only when strictly more likely than `yes`; `yes` denies. The configured threshold applies only to probability/Noul mode. Missing or extra fields, extra questions, wrong answer types, booleans, NaN, conflicting probabilities, duplicate JSON keys, and chat envelopes on the native path deny. There is no retry with another protocol.

Noul is a native typed probability, unlike the generic chat mode's model-generated JSON estimate. Upstream explicitly does not guarantee calibration for your data; One Guard establishes neither calibration nor real-world detection quality. Upstream also notes model-side input limits (for example, h2o-lightning keeps the first/last 16000 tokens for states exceeding 32000). One Guard sends full bounded arguments without local truncation, but cannot guarantee that a server processes every token.

Past live smoke tests contacted a private LAN judge service. Initial six-case requests timed out at 10 seconds each; later runs used a 60-second deadline and passed all six synthetic cases in both Noul probability and binary yes/no modes. Individual requests took 19.479 to 21.831 seconds. The smoke establishes those twelve observed outcomes only; it is not a security benchmark, calibration study, proof of prompt-injection resistance, or broad classification evaluation. The address is intentionally omitted. The example endpoint above is fictional and was not used for those tests. The default remains 5 seconds; explicit timeouts support up to 120 seconds. See [verification notes](docs/verification.md#live-endpoint-observations).

A separate trace-derived run on 2026-10-27 sent 12 inert proposals to the
user-supplied private LAN judge using `h2o-lightning-4b`, threshold `0.5`, a
120-second timeout, and two workers. It matched 11 of 12 subjective expected
labels. The judge allowed the `guard-removal` proposal (score `0.2594485504`),
which was the one miss. This single run does not establish calibration or
general detection efficacy. The incident command and three fragments came from
one published trace; other cases were synthetic adaptations, not independent
traces. See the [full results and limitations](docs/verification.md#trace-derived-live-run-2026-10-27).

To repeat the run, replace the placeholder with a judge endpoint you control. For
non-loopback connections, prefer HTTPS: the HTTP opt-in sends the token and tool
arguments unencrypted.

```sh
python3 scripts/live-trace-systemone.py --run-live --endpoint http://judge.example.test:9292/v1/systemone --model h2o-lightning-4b --timeout 120 --allow-insecure-http --workers 2
```

The script uses probability mode with its fixed `0.5` threshold. It sends
proposals to the configured judge but never executes them or contacts their
embedded destinations. It exercises the production payload builder, response
parser, and deadline connection, not the complete Hermes dispatcher.

## What the hook covers

The plugin registers one native `pre_tool_call` hook. It does not filter tool names, so Hermes dispatches the hook for calls that reach this dispatcher, including terminal, browser/HTTP, file, and MCP tools. The hook sees the tool name and arguments exposed by Hermes at judgment time; it does not bind them to immutable final execution arguments. It does not intercept system calls, nested requests made inside a tool, or activity that occurs outside the guarded dispatcher. A tool's own effects are not necessarily represented fully in its arguments.

Inline script text and chained commands included in arguments are sent as-is for judgment. Referenced script files, encoded payloads, and hidden tool implementation are not read or executed for inspection. A filename is not evidence that a script is safe. The default prompt directs the judge to deny when hidden content or missing context prevents a safe assessment. A preceding file-write call and later script-execution call are judged separately; the execution decision does not automatically include the file's contents.

Tool arguments may include credentials or private data. Sending them to a remote judge makes that service a data recipient and trust boundary. Configure a trusted local endpoint or a service approved for the data. One Guard does not redact arguments before sending them.

## Token and judge trust boundaries

The manifest declares `token` as `type: secret` with `env: ONE_GUARD_TOKEN`. Hermes' settings UI receives only its environment name and presence flag, never the token value. Hermes loads `.env` on restart; the plugin reads its supported secret resolver. Do not expose `.env` contents in shell history, logs, support bundles, or shared process environments. The plugin does not include the token in its block message.

On upgrade, the installer removes an old YAML `token` setting and migrates it into `.env` when no token was supplied and no `.env` token already exists. An explicit installer environment value takes precedence, including an empty value to clear the token. Unrelated dotenv bindings remain intact. Malformed dotenv data, duplicate token assignments, and multiline token assignments require repair before installation. Backups may contain old YAML credentials or `.env` secrets and need the same protection as the active files.

Environment overrides can change every policy control and the recipient, not only authentication. An operator can change `ONE_GUARD_ENDPOINT`, allow plaintext remote HTTP with `ONE_GUARD_ALLOW_INSECURE_HTTP=true`, or weaken policy through the prompt, mode, model, or threshold. Plaintext HTTP exposes both bearer credentials and tool arguments. Treat environment access, config writes, custom prompts, judge operators, and installed peer plugins as trusted policy administration. Appending a custom prompt does not prevent that prompt from contradicting or weakening the built-in policy.

The judge receives the full bounded tool name and arguments. Its response is treated as a policy decision only after strict shape and value validation, but model output can still be wrong or manipulated by adversarial content. Tool arguments are explicitly marked untrusted in the built-in prompt; this is not a security boundary against a compromised or unreliable judge.

## Update and uninstall

Stop Hermes before an update. Rerun `sh ./install.sh` from the new local checkout with the same explicit selector flags if needed. The installer replaces only an unchanged plugin directory that it previously marked as its own. If files were edited, added, or replaced, move the directory aside and inspect it before retrying. Successful installs retain randomly named `.one-guard-backup-*` directories under the selected Hermes home.

Manual uninstall steps:

1. Stop Hermes.
2. Remove `one-guard` from `plugins.enabled` in `config.yaml`. Remove its `plugins.entries.one-guard` settings if no longer needed.
3. Remove the `plugins/one-guard` directory under the Hermes data home.
4. Remove the `.one-guard-backup-*` directories only after checking that they contain no config or plugin version you need.
5. Remove `ONE_GUARD_TOKEN` from the Hermes home's `.env` if no longer needed.

The installer has no uninstall command and does not remove its backups. Removing a plugin does not undo any action previously allowed by the judge.

## Development and verification

The plugin uses the Python standard library and Hermes' secret resolver. The installer needs existing PyYAML and python-dotenv from the active or Hermes Python environment. `prompt_toolkit` is optional and only improves the interactive menus. The project declares Python `>=3.10`.

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall -q plugins/one-guard tests scripts
```

The suite covers the plugin runtime, the Hermes runtime integration, the installer, the wizard flow, and the installer driven through a real pseudo-terminal. Hermes integration tests require a compatible Hermes source checkout selected with `HERMES_SOURCE`; without it, those tests are skipped. The pseudo-terminal tests need a POSIX environment and may not run on Windows.

See [verification notes](docs/verification.md) for tests through the cloned Hermes runtime. Those notes distinguish a deterministic local HTTP stub and dispatcher checks from real-model efficacy, which is not established here. See [architecture decisions](docs/adr/README.md) and [research notes](docs/research.md) for design rationale and compatibility evidence.

The live smoke script requires an explicit opt-in and never runs as a live endpoint test under normal discovery:

```sh
python3 scripts/smoke-systemone.py \
  --run-live --endpoint http://judge.example.test:9292/v1/systemone \
  --model h2o-lightning-4b --allow-insecure-http \
  --response-mode probability --timeout 60
```

Replace `judge.example.test` with a judge you control before running this opt-in command. It sends only synthetic local-operation, GET-query, POST-body, inline-script, and guard-disabling proposals to that judge. It executes no tools, reads no real secrets, and contacts no fixture exfiltration destinations. Authentication uses `ONE_GUARD_TOKEN` when provided. It reports transport/parser failures separately from risk denials and exits nonzero for either failures or mismatches. Repeat with `--response-mode yes_no` to probe binary choice. The placeholder is not a functioning test endpoint. Normal tests exercise this script only against a loopback stub.

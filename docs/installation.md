# Installation and operations

For the short human-facing setup, see [the README](../README.md). This page documents the installer workflow, flags, and operational requirements.

## Install from a local checkout

This repository has no published release URL. Clone or otherwise obtain the project locally, stop Hermes, and run the installer from the checkout:

```sh
sh ./install.sh
```

The installer detects a Hermes source checkout using `HERMES_DIR`, the `hermes` executable, and common source locations. If it finds multiple candidates, select one in the wizard or pass `--hermes-dir`. This source selector is distinct from the Hermes data home where files are installed. `--hermes-dir` must point to a Hermes source tree, not the Hermes data home.

The interactive wizard has seven steps:

1. Hermes source: pick a discovered checkout or enter a path.
2. Hermes data home: choose where `config.yaml`, `.env`, and the plugin are written.
3. Judge connection: configure protocol, transport policy, and endpoint.
4. Decision policy: choose yes/no or probability, timeout, and, in probability mode, the approval and block cutoffs.
5. Authentication: keep, replace, or clear the bearer credential.
6. Model discovery: optionally ask the judge for its model list, or enter/select a model manually.
7. Review: inspect effective settings, then install or cancel.

With the optional terminal UI, use Up/Down, `j`/`k`, or Tab to move, Enter to select, and number keys to choose an option. Esc cancels in arrow-key menus; Ctrl-C cancels throughout setup. Text fields validate on submission and use the displayed default when you press Enter on an empty field. Credential input is hidden and never written to history.

The arrow-key UI is optional. On a TTY with `TERM` other than `dumb`, and when `NO_COLOR` is not set, the source selector uses the enhanced UI if `prompt_toolkit` is already available. Otherwise, before source selection, the installer offers to download the pinned UI packages into a temporary, isolated virtual environment and displays progress while preparing it. This requires your explicit consent; declining, or a failed/unavailable download, continues with the numbered wizard. The temporary environment is cleaned up when the installer exits. The download installs only the terminal UI: it does not install or alter Hermes, system Python packages, a model, or an inference server.

`--plain`, `--yes`, `NO_COLOR` (when set, including to an empty value), and `TERM=dumb` disable the enhanced UI and its download offer. `--yes` is non-interactive and never prompts. The plain wizard uses numbered choices and supports normal line input, defaults on Enter, and hidden credential entry; use `--plain` if arrow-key rendering is unsuitable. The UI bootstrap does not supply the installer's other dependencies: PyYAML and python-dotenv must already be available in the active Python or Hermes' `.venv`.

No Hermes files are written until the review screen is confirmed. If you consent to the optional UI bootstrap, it creates a temporary environment before then and removes it when the installer exits. Cancelling, pressing Ctrl-C, or reaching end-of-file leaves the Hermes target directory untouched. From the review screen you can go back and change the judge, decision policy, model, credential, destination, or source before installing. `--yes` skips the wizard and never prompts or makes a model-discovery request.

Model discovery is a separate, opt-in network request after authentication. Before asking for consent, the wizard identifies the configured judge origin, says it will send one `GET <origin>/v1/models`, and states whether the credential selected by this installer will be sent. That credential is the one this installer stores in the Hermes `.env`; Hermes resolves the runtime credential from its own profile and environment, so the value used at runtime can differ. When the endpoint is a remote plaintext HTTP address, the consent screen says that the credential would travel unencrypted, or, with no credential, that the model-list request would be visible on the network. A literal loopback address is not reported as remote. This request goes to the same origin as the configured endpoint; it does not follow redirects, use a proxy, or fetch pagination. The response is bounded to 64 KiB and at most 64 model identifiers. The probe runs on a worker thread with a five-second deadline. Name resolution is not cancellable, so a worker waiting on DNS holds its probe slot until the lookup returns, which can outlast that deadline; the slot is never released before the worker exits. If the judge is offline, does not serve this route, or returns unusable data, enter the model identifier manually or leave it blank when the endpoint selects a model. `--model` supplies the initial/current value and writes `judge_model`; it does not enable discovery in non-interactive mode.

Identifiers that contain the selected credential anywhere are dropped before the list is shown, so a hostile judge cannot get the credential written into `judge_model`. A stored `judge_model` that already contains the credential is not offered again. The check is conservative: a short credential suppresses every identifier that contains it. Identifiers holding Unicode control or format characters, including bidirectional overrides, are dropped as well, and the same rule rejects such characters in a configured endpoint or model. The identifier limit counts usable identifiers, so entries dropped as invalid or duplicated do not consume it. Credential-bearing identifiers are dropped before that count, so a hostile judge cannot push legitimate identifiers out of the list by filling it with credential-bearing ones.

The list and capability notes are claims from the judge, not compatibility checks. For System One, the wizard labels native decisions only when the server lists `decisions` among `architecture.output_modalities`, and text-only output only when that list is exactly the single value `text`; every other combination, and any malformed value, is reported as unknown. The comparison is exact: a differently cased or padded value such as `Decisions` or ` text` does not count, and a list that repeats `text` is not text-only. For chat completions, compatibility is not declared by the server. One Guard does not verify that a listed model works with the selected protocol. The probe does not load or download a model. If the endpoint uses remote plaintext HTTP, any credential sent for discovery is unencrypted. Runtime judge requests also send tool arguments, so use HTTPS for remote endpoints.

Installation requires a POSIX environment. `install.sh` selects `python3`, then `python`, and reports an error if neither is available. PyYAML and python-dotenv must already exist in the active Python environment or Hermes' `.venv`. If neither environment has both, the installer stops with an installation hint; it does not install these dependencies. The optional consent-based bootstrap uses pip only inside a temporary virtual environment to install the hash-pinned terminal UI packages, then removes that environment on exit. The plugin runtime uses only the Python standard library and Hermes' secret resolver. The project declares Python `>=3.10`.

## Non-interactive installation

Choose both the Hermes source and data home explicitly and pass `--yes`:

```sh
ONE_GUARD_TOKEN='your-token' sh ./install.sh \
  --yes \
  --hermes-dir /path/to/hermes-agent \
  --hermes-home "$HOME/.hermes" \
  --endpoint http://127.0.0.1:8080/v1/chat/completions \
  --model local-judge \
  --response-mode yes_no
```

`--yes` never prompts and never opens the wizard. The same flags seed the wizard's fields when it is used, so `--endpoint`, `--model`, `--timeout`, `--threshold`, `--approval-threshold`, `--protocol`, and `--response-mode` prefill and can still be edited before the review screen.

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
  --approval-threshold 0.5 \
  --threshold 0.7 \
  --allow-insecure-http
```

This is an example command, not a recorded installation. The live smoke contacted the judge service, but no Hermes installation was performed. The installer does not install Hermes, an inference server, or a model.

`--protocol` accepts `chat_completions` or `systemone`; omitted values preserve existing settings, defaulting to chat completions on first install. A new native installation without `--endpoint` defaults to `http://127.0.0.1:8080/v1/systemone`. When changing an existing installation's protocol, supply the matching endpoint too: the installer never rewrites an existing endpoint or guesses a protocol from its path. `--allow-insecure-http` writes the opt-in on first installation; it does not require a preexisting config file.

## Installer flags

| Flag | Purpose |
| --- | --- |
| `--hermes-dir` | Select the Hermes source tree. This is not the Hermes data home. |
| `--hermes-home` | Select the Hermes data home where config, `.env`, and plugin files are written. |
| `--yes` | Run non-interactively. Requires explicit source and data home selection when discovery is ambiguous; never prompts, opens the wizard, or contacts the judge for model discovery. |
| `--plain` | Use the numbered plain wizard instead of the optional terminal UI. |
| `--wizard` | Open the interactive wizard. |
| `--protocol` | Select `chat_completions` or `systemone`. |
| `--endpoint` | Set the judge endpoint. It must match the selected protocol. |
| `--allow-insecure-http` | Explicitly allow remote HTTP. This sends credentials and arguments without encryption. |
| `--model` | Set the judge model; writes the `judge_model` setting. In the wizard it prefills the model value; discovery remains a separate opt-in. |
| `--timeout` | Set the timeout in seconds; finite values from `0.01` through `120`. When omitted, an existing setting is preserved and a new install uses `5` seconds. |
| `--threshold` | Set the block cutoff: a risk score at or above it is blocked. Finite values from `0.0` through `1.0`. When omitted, an existing setting is preserved and a new install uses `0.7`. Passing it returns the review band to the default approval cutoff `0.5`, which can allow the scores that a lower configured approval cutoff would have reviewed. |
| `--approval-threshold` | Set the approval cutoff: a risk score at or above it, and below the block cutoff, goes to the Hermes approval gate. Finite values from `0.0` through `1.0`. A value above the block cutoff is reduced to the block cutoff rather than rejected. When omitted, an existing setting is preserved and a new install uses `0.5`, except that an explicit `--threshold` returns the review band to `0.5`. |
| `--response-mode` | Select `yes_no` or `probability`. |

## Thresholds and existing installations

The installer always writes both `threshold` (the block cutoff) and `approval_threshold` (the approval cutoff) into `plugins.entries.one-guard.settings`, and its review screen shows the effective band.

An installation that already has a `threshold` and no `approval_threshold` keeps its exact old behaviour: the approval cutoff follows the block cutoff, the review band stays empty, and every score at or above the block cutoff is still blocked. That holds for cutoffs below and above `0.5`. The installer writes the matching `approval_threshold` so the migrated state is explicit in `config.yaml`.

Passing `--threshold` sets the block cutoff explicitly. Because that is an explicit change to the cutoff, the review band returns to the default `approval_threshold` of `0.5` unless `--approval-threshold` is passed as well. That reset can widen what is allowed: an installation whose approval cutoff was below `0.5` will now allow the scores between that cutoff and `0.5` instead of sending them to the Hermes approval gate. It happens only when the operator asks for it with an explicit `--threshold`, and the effective band is shown on the review screen before anything is written.

Two different rules apply to the cutoffs. The clamp is the exception rule: an approval cutoff above the block cutoff is reduced to the block cutoff, so the block cutoff always wins, that clamp never widens what is allowed, and no configuration becomes invalid. The reset is the `--threshold` rule above, and it can widen what is allowed. See [runtime configuration](configuration.md#threshold-migration) for the full rule and the [decision record](adr/0008-three-band-approval-policy.md).

## Timeout and Hermes host budget

Hermes separately defaults `plugins.hook_callback_timeout` to 30 seconds. Choose a host budget at least `timeout + 5` seconds, or appropriately higher for host overhead. A 60-second plugin timeout therefore needs a separate Hermes `plugins.hook_callback_timeout` of at least 65 seconds. For example, merge this into the selected Hermes home's `config.yaml`:

```yaml
plugins:
  hook_callback_timeout: 65
```

This is a global setting affecting other hooks. The installer warns when the plugin timeout reaches or exceeds the host budget, but never changes that setting. A host value of `0` disables that host limit and produces no budget warning. Account for runtime `ONE_GUARD_TIMEOUT` overrides when choosing the host budget too. No real Hermes installation was performed.

## Credentials and filesystem behavior

The installer saves the token as `ONE_GUARD_TOKEN` in the selected Hermes home's `.env`. It reads `ONE_GUARD_TOKEN` or prompts without echo; the value is not a CLI option. In the wizard, the credential step can keep the current value, replace it with masked input, or remove it. The review screen reports only whether a credential is present. A model identifier typed by hand is the operator's own choice; when it contains the selected credential, the installer warns that the credential would be written to `config.yaml`. Stop Hermes before installing or updating so it cannot load a partially replaced plugin.

The config file, `.env`, plugin files, and installer backups are written with mode `0600`. New directories use `0700`; existing directory permissions are unchanged.

The installer preserves unrelated YAML values and merges the plugin's enablement and settings. It may rewrite YAML formatting and comments. For a config stamped `_config_version` below 21 with no `plugins.enabled`, it preserves Hermes' v21 grandfathering of installed top-level user plugins, using manifest names (or directory names when absent) and excluding `plugins.disabled`. A modern config with no enabled list, or an explicit empty list, stays opt-in. An unversioned config with installed plugins and no enabled list requires explicit review, as do ambiguous legacy manifests or layouts. Set `plugins.enabled` to the intended list before retrying; preflight refuses ambiguity before writes. Bundled plugins are not grandfathered.

The installer rejects malformed or duplicate YAML keys, unsafe links, unowned plugin directories, and modified files in an installer-owned plugin directory. It atomically replaces each file and rolls back the plugin, config, and `.env` together on a replacement failure. This is not a crash-atomic transaction across files. Each successful run reports a randomly named backup directory. Review the selected source, data home, and backup path in the output. Backups can contain credentials and require protection like the active files.

## Update and uninstall

Stop Hermes before an update. Rerun `sh ./install.sh` from the new local checkout with the same explicit selector flags if needed. The installer replaces only an unchanged plugin directory that it previously marked as its own. If files were edited, added, or replaced, move the directory aside and inspect it before retrying. Successful installs retain randomly named `.one-guard-backup-*` directories under the selected Hermes home.

Manual uninstall steps:

1. Stop Hermes.
2. Remove `one-guard` from `plugins.enabled` in `config.yaml`. Remove its `plugins.entries.one-guard` settings if no longer needed.
3. Remove the `plugins/one-guard` directory under the Hermes data home.
4. Remove the `.one-guard-backup-*` directories only after checking that they contain no config or plugin version you need.
5. Remove `ONE_GUARD_TOKEN` from the Hermes home's `.env` if no longer needed.

The installer has no uninstall command and does not remove its backups. Removing a plugin does not undo any action previously allowed by the judge.

## Compatibility and verification

The integration was checked against Hermes source HEAD `e5f2b15300a3d04978e4c07cc64adc7bcf8e7b1c`. The plugin targets the native plugin API and `pre_tool_call` directive shape in that checkout. Recheck the hook contract when upgrading Hermes.

In that source revision, Hermes blocks a `pre_tool_call` callback that raises or times out. Older Hermes documentation described callback failures as fail-open. One Guard also returns an explicit block directive for its own failures, but do not assume the runtime behavior carries forward to other Hermes versions. The source and references are summarized in [research notes](research.md).

Run the automated suite and compilation check with:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall -q plugins/one-guard tests scripts
```

The suite covers the plugin runtime, the Hermes runtime integration, the installer, the wizard flow, model-discovery behavior against loopback HTTP and TLS stubs, and the installer driven through a real pseudo-terminal. It does not establish behavior against a live model-list service or actual model compatibility. Hermes integration tests require a compatible Hermes source checkout selected with `HERMES_SOURCE`; without it, those tests are skipped. The pseudo-terminal tests need a POSIX environment and may not run on Windows. See [verification notes](verification.md) for tests through the cloned Hermes runtime. Those notes distinguish deterministic local HTTP stub and dispatcher checks from real-model efficacy, which is not established here. See [architecture decisions](adr/README.md) and [research notes](research.md) for design rationale and compatibility evidence.

The live smoke script requires an explicit opt-in and never runs as a live endpoint test under normal discovery:

```sh
python3 scripts/smoke-systemone.py \
  --run-live --endpoint http://judge.example.test:9292/v1/systemone \
  --model h2o-lightning-4b --allow-insecure-http \
  --response-mode probability --timeout 60
```

Replace `judge.example.test` with a judge you control before running this opt-in command. It sends only synthetic local-operation, GET-query, POST-body, inline-script, and guard-disabling proposals to that judge. It executes no tools, reads no real secrets, and contacts no fixture exfiltration destinations. Authentication uses `ONE_GUARD_TOKEN` when provided. It reports transport/parser failures separately from risk denials and exits nonzero for either failures or mismatches. Repeat with `--response-mode yes_no` to probe binary choice. The placeholder is not a functioning test endpoint. Normal tests exercise this script only against a loopback stub.

In probability mode the script applies the same three bands as the plugin and reports `allow`, `approve`, or `deny` per case. It takes `--approval-threshold` and `--block-threshold`; giving both the same value reproduces the earlier single-cutoff behaviour.

A separate trace-derived live run can be repeated with a judge endpoint you control. For non-loopback connections, prefer HTTPS: the HTTP opt-in sends the token and tool arguments unencrypted.

```sh
python3 scripts/live-trace-systemone.py --run-live --endpoint http://judge.example.test:9292/v1/systemone --model h2o-lightning-4b --timeout 120 --allow-insecure-http --workers 2
```

The script uses probability mode with its default cutoffs (`0.5` approval, `0.7` block); pass `--approval-threshold` and `--block-threshold` to change them, and give both the same value to reproduce the earlier single-cutoff behaviour. It sends proposals to the configured judge but never executes them or contacts their embedded destinations. It exercises the production payload builder, response parser, and deadline connection, not the complete Hermes dispatcher.

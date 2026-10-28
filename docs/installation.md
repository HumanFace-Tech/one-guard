# Installation and operations

For the short human-facing setup, see [the README](../README.md). This page documents the installer workflow, flags, and operational requirements.

## Install from a local checkout

This repository has no published release URL. Clone or otherwise obtain the project locally, stop Hermes, and run the installer from the checkout:

```sh
sh ./install.sh
```

The installer detects a Hermes source checkout using `HERMES_DIR`, the `hermes` executable, and common source locations. If it finds multiple candidates, select one in the wizard or pass `--hermes-dir`. This source selector is distinct from the Hermes data home where files are installed. `--hermes-dir` must point to a Hermes source tree, not the Hermes data home.

The interactive wizard has six steps:

1. Hermes source: pick a discovered checkout or enter a path.
2. Hermes data home: choose where `config.yaml`, `.env`, and the plugin are written.
3. Judge connection: configure protocol, transport policy, endpoint, and model.
4. Decision policy: choose yes/no or probability, timeout, and threshold.
5. Authentication: keep, replace, or clear the bearer credential.
6. Review: inspect effective settings, then install or cancel.

With the optional terminal UI, use Up/Down, `j`/`k`, or Tab to move, Enter to select, and number keys to choose an option. Esc cancels in arrow-key menus; Ctrl-C cancels throughout setup. Text fields validate on submission and use the displayed default when you press Enter on an empty field. Credential input is hidden and never written to history.

The arrow-key UI is optional. On a TTY with `TERM` other than `dumb`, and when `NO_COLOR` is not set, the source selector uses the enhanced UI if `prompt_toolkit` is already available. Otherwise, before source selection, the installer offers to download the pinned UI packages into a temporary, isolated virtual environment and displays progress while preparing it. This requires your explicit consent; declining, or a failed/unavailable download, continues with the numbered wizard. The temporary environment is cleaned up when the installer exits. The download installs only the terminal UI: it does not install or alter Hermes, system Python packages, a model, or an inference server.

`--plain`, `--yes`, `NO_COLOR` (when set, including to an empty value), and `TERM=dumb` disable the enhanced UI and its download offer. `--yes` is non-interactive and never prompts. The plain wizard uses numbered choices and supports normal line input, defaults on Enter, and hidden credential entry; use `--plain` if arrow-key rendering is unsuitable. The UI bootstrap does not supply the installer's other dependencies: PyYAML and python-dotenv must already be available in the active Python or Hermes' `.venv`.

No Hermes files are written until the review screen is confirmed. If you consent to the optional UI bootstrap, it creates a temporary environment before then and removes it when the installer exits. Cancelling, pressing Ctrl-C, or reaching end-of-file leaves the Hermes target directory untouched. From the review screen you can go back and change the judge, the credential, the destination, or the source before installing. `--yes` skips the wizard and never prompts.

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

`--yes` never prompts and never opens the wizard. The same flags seed the wizard's fields when it is used, so `--endpoint`, `--model`, `--timeout`, `--threshold`, `--protocol`, and `--response-mode` prefill and can still be edited before the review screen.

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

This is an example command, not a recorded installation. The live smoke contacted the judge service, but no Hermes installation was performed. The installer does not install Hermes, an inference server, or a model.

`--protocol` accepts `chat_completions` or `systemone`; omitted values preserve existing settings, defaulting to chat completions on first install. A new native installation without `--endpoint` defaults to `http://127.0.0.1:8080/v1/systemone`. When changing an existing installation's protocol, supply the matching endpoint too: the installer never rewrites an existing endpoint or guesses a protocol from its path. `--allow-insecure-http` writes the opt-in on first installation; it does not require a preexisting config file.

## Installer flags

| Flag | Purpose |
| --- | --- |
| `--hermes-dir` | Select the Hermes source tree. This is not the Hermes data home. |
| `--hermes-home` | Select the Hermes data home where config, `.env`, and plugin files are written. |
| `--yes` | Run non-interactively. Requires explicit source and data home selection when discovery is ambiguous; never prompts or opens the wizard. |
| `--plain` | Use the numbered plain wizard instead of the optional terminal UI. |
| `--wizard` | Open the interactive wizard. |
| `--protocol` | Select `chat_completions` or `systemone`. |
| `--endpoint` | Set the judge endpoint. It must match the selected protocol. |
| `--allow-insecure-http` | Explicitly allow remote HTTP. This sends credentials and arguments without encryption. |
| `--model` | Set the judge model; writes the `judge_model` setting. |
| `--timeout` | Set the timeout in seconds; finite values from `0.01` through `120`. When omitted, an existing setting is preserved and a new install uses `5` seconds. |
| `--threshold` | Set the probability threshold, from `0.0` through `1.0`. |
| `--response-mode` | Select `yes_no` or `probability`. |

## Timeout and Hermes host budget

Hermes separately defaults `plugins.hook_callback_timeout` to 30 seconds. Choose a host budget at least `timeout + 5` seconds, or appropriately higher for host overhead. A 60-second plugin timeout therefore needs a separate Hermes `plugins.hook_callback_timeout` of at least 65 seconds. For example, merge this into the selected Hermes home's `config.yaml`:

```yaml
plugins:
  hook_callback_timeout: 65
```

This is a global setting affecting other hooks. The installer warns when the plugin timeout reaches or exceeds the host budget, but never changes that setting. A host value of `0` disables that host limit and produces no budget warning. Account for runtime `ONE_GUARD_TIMEOUT` overrides when choosing the host budget too. No real Hermes installation was performed.

## Credentials and filesystem behavior

The installer saves the token as `ONE_GUARD_TOKEN` in the selected Hermes home's `.env`. It reads `ONE_GUARD_TOKEN` or prompts without echo; the value is not a CLI option. In the wizard, the credential step can keep the current value, replace it with masked input, or remove it. The review screen reports only whether a credential is present. Stop Hermes before installing or updating so it cannot load a partially replaced plugin.

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

The suite covers the plugin runtime, the Hermes runtime integration, the installer, the wizard flow, and the installer driven through a real pseudo-terminal. Hermes integration tests require a compatible Hermes source checkout selected with `HERMES_SOURCE`; without it, those tests are skipped. The pseudo-terminal tests need a POSIX environment and may not run on Windows. See [verification notes](verification.md) for tests through the cloned Hermes runtime. Those notes distinguish a deterministic local HTTP stub and dispatcher checks from real-model efficacy, which is not established here. See [architecture decisions](adr/README.md) and [research notes](research.md) for design rationale and compatibility evidence.

The live smoke script requires an explicit opt-in and never runs as a live endpoint test under normal discovery:

```sh
python3 scripts/smoke-systemone.py \
  --run-live --endpoint http://judge.example.test:9292/v1/systemone \
  --model h2o-lightning-4b --allow-insecure-http \
  --response-mode probability --timeout 60
```

Replace `judge.example.test` with a judge you control before running this opt-in command. It sends only synthetic local-operation, GET-query, POST-body, inline-script, and guard-disabling proposals to that judge. It executes no tools, reads no real secrets, and contacts no fixture exfiltration destinations. Authentication uses `ONE_GUARD_TOKEN` when provided. It reports transport/parser failures separately from risk denials and exits nonzero for either failures or mismatches. Repeat with `--response-mode yes_no` to probe binary choice. The placeholder is not a functioning test endpoint. Normal tests exercise this script only against a loopback stub.

A separate trace-derived live run can be repeated with a judge endpoint you control. For non-loopback connections, prefer HTTPS: the HTTP opt-in sends the token and tool arguments unencrypted.

```sh
python3 scripts/live-trace-systemone.py --run-live --endpoint http://judge.example.test:9292/v1/systemone --model h2o-lightning-4b --timeout 120 --allow-insecure-http --workers 2
```

The script uses probability mode with its fixed `0.5` threshold. It sends proposals to the configured judge but never executes them or contacts their embedded destinations. It exercises the production payload builder, response parser, and deadline connection, not the complete Hermes dispatcher.

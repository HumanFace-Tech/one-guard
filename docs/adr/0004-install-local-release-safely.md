# ADR 0004: install a local release safely

**Status:** Accepted

## Context

No public repository or release URL is available. An invented one-line download command would be misleading and would leave release integrity undefined. Hermes config is user-owned YAML and may contain unrelated settings and secrets.

## Decision

Ship the plugin folder with `install.sh` and `scripts/install.py`. Install from the local checkout into one explicitly selected Hermes data home. Use existing PyYAML from the current or Hermes Python environment. Merge plugin settings, write private files, back up existing config and plugin content, and refuse unowned or modified plugin directories.

As extended by ADR 0006, `--protocol` explicitly selects chat completions or native System One, with exact matching endpoint validation. `--allow-insecure-http` writes an explicit remote/LAN plaintext opt-in, including on first installation. The installer never installs Hermes, an inference server, or a model.

Preserve the v21 grandfathered user-plugin set when `plugins.enabled` is missing on a pre-v21 stamped config. Keep modern configs opt-in. Refuse unversioned configs with installed plugins, ambiguous manifests, or ambiguous layouts before writes. Use Hermes' manifest `secret` field and `ONE_GUARD_TOKEN` in the data home's `.env`, parsed with existing python-dotenv. Migrate old YAML credentials, preserving unrelated dotenv bindings. Back up and roll back `.env`, config, and plugin replacements together. Backups have random suffixes. New directories use `0700`; existing directory permissions are unchanged.

## Consequences

Users obtain the repository/release by a separately trusted means and run `sh ./install.sh` locally. The installer may change YAML formatting but preserves unrelated values. It has no automated uninstall; operators stop Hermes and remove the plugin/config entry manually after reviewing backups. Future hosted installation requires a real published URL and integrity metadata.

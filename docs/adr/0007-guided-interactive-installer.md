# ADR 0007: Guided interactive installer with an optional terminal UI

## Status

Accepted, 2026-10-27.

## Context

The first installer was a flag-driven script. It worked, but a new user had to
read the README to learn the flag names, the order of the settings, the
protocol/path coupling, and the fact that a LAN judge needs an explicit
plaintext opt-in. The project is meant to be open-sourced and used by people who
do not already know the tool.

Two constraints shaped the design:

- The plugin runtime must stay dependency-free. It is installed into a Hermes
  home and loaded by Hermes' own Python.
- The installer must not silently install packages or reach the network during
  setup. Any optional network activity needs explicit consent and must not alter
  Hermes or the system Python environment.

The existing `--yes` flag path also had to keep working exactly as before, for
automation and for CI.

## Decision

Add a guided wizard to `scripts/install.py`, with terminal rendering isolated in
a new sibling module `scripts/installer_ui.py`.

The wizard has seven steps: Hermes source, Hermes data home, judge connection,
decision policy, authentication, optional model discovery, and a review screen
that is the only place an install can be confirmed. Every step is re-entrant
from the review screen, so a user can go back and change one field without
restarting.

Model discovery is opt-in and occurs after authentication. The wizard explains
that it will make one direct `GET /v1/models` request to the configured endpoint's
origin and whether the credential selected by this installer will accompany it.
The credential is the one stored in the Hermes `.env` by this installation;
Hermes resolves the runtime credential from its own profile and environment, so
the two can differ. A remote plaintext HTTP endpoint is called out as carrying
that credential unencrypted, or, without one, as exposing the request itself. The
request is bounded, does not follow redirects or pagination, and has a manual
model-entry fallback when the endpoint is offline or returns unusable data.
Returned identifiers and System One capability metadata are server claims, not
verified model/protocol compatibility. Discovery does not download or load a
model. `--yes` retains its non-interactive contract and never makes this request.

Identifiers that contain the selected credential anywhere are dropped before the
list is shown, so a hostile judge cannot steer the credential into `judge_model`.
The rule is conservative: a short credential suppresses every identifier that
contains it. Identifiers carrying Unicode control or format characters are
dropped, and capability is reported as unknown unless the server explicitly lists
`decisions` or lists exactly `text`.

Two rendering backends sit behind one interface:

- `ToolkitUI` uses `prompt_toolkit` for arrow-driven menus, masked input, and
  inline validation.
- `PlainUI` is a numbered, always-available fallback with no third-party
  dependency.

`prompt_toolkit` is optional. If it is already importable, the wizard can use it
directly. Otherwise, in an eligible interactive terminal, the installer asks
before using pip to install the hash-pinned UI requirements into a temporary,
isolated virtual environment. The environment and its imports are removed when
the installer exits. Declining or failing to provision falls back to `PlainUI`.
The bootstrap is only for terminal rendering; PyYAML and python-dotenv must
already be available in the active Python or Hermes `.venv`.

## Consequences

- The wizard validates through the same `merge_config` code as the CLI, so an
  interactive install cannot accept a setting the flag path would reject. The
  wizard never re-implements a rule.
- No Hermes files are written before confirmation. If the user consents to the
  optional UI bootstrap, it creates a disposable temporary environment before
  the review; cancel, Ctrl-C, and EOF still leave the Hermes target untouched,
  and the temporary environment is removed on exit.
- The credential is entered through a masked field, is excluded from prompt
  history, is replaced with `[credential]` on every output path, and never
  reaches `config.yaml`. A model identifier typed by hand stays the operator's
  own choice; the installer warns when it contains the selected credential,
  because that credential would then be written to `config.yaml`.
- Terminal output escapes control characters, so an untrusted model name or
  endpoint cannot emit ANSI escapes into the user's terminal.
- The installer is now more complex, and `scripts/installer_ui.py` must stay a
  sibling of `scripts/install.py` because it is loaded by path.
- The opt-in model-list request adds a network action to the wizard, so consent
  is specific and informed: a credential selected by this installer may be sent
  to the configured origin. Remote plaintext HTTP exposes that credential in
  transit, and the consent screen says so before the request is made. Automated
  model-discovery tests use a loopback stub, including a self-signed TLS stub;
  they do not establish live service behavior or actual model compatibility.
- The optional UI bootstrap reaches PyPI only after explicit consent. It does
  not install Hermes, system packages, plugin runtime dependencies, a model, or
  an inference server. `--yes`, `--plain`, `NO_COLOR`, and `TERM=dumb` skip the
  bootstrap offer. When the toolkit is unavailable, the installer offers the
  optional download before the initial source selector, with a plain fallback.
- Wizard behavior is verified with a scripted UI for flow logic, loopback HTTP
  stubs for discovery boundaries, and a real pseudo-terminal for rendering and
  keybindings.

## Alternatives considered

- **Keep flags only.** Rejected. The setup order and the protocol/path coupling
  are easy to get wrong without guidance.
- **Ship a web or GUI installer.** Rejected. It adds a runtime and a port for a
  one-time setup task.
- **Bundle `prompt_toolkit` as a hard dependency.** Rejected. The installer
  must work in a bare Hermes environment; enhanced UI packages are optional and
  downloaded only after consent into a disposable environment.
- **Hand-roll raw ANSI and read single keys.** Rejected. Raw terminal handling
  is fragile across terminals and platforms, and easy to get wrong in ways that
  corrupt user input.

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
- The installer must never install packages on the user's behalf, and must never
  reach the network during setup.

The existing `--yes` flag path also had to keep working exactly as before, for
automation and for CI.

## Decision

Add a guided wizard to `scripts/install.py`, with terminal rendering isolated in
a new sibling module `scripts/installer_ui.py`.

The wizard has six steps: Hermes source, Hermes data home, judge connection,
decision policy, authentication, and a review screen that is the only place an
install can be confirmed. Every step is re-entrant from the review screen, so a
user can go back and change one field without restarting.

Two rendering backends sit behind one interface:

- `ToolkitUI` uses `prompt_toolkit` for arrow-driven menus, masked input, and
  inline validation.
- `PlainUI` is a numbered, always-available fallback with no third-party
  dependency.

`prompt_toolkit` is an optional dependency (`[project.optional-dependencies]
installer-ui`). The installer probes for it and falls back; it never runs pip.

## Consequences

- The wizard validates through the same `merge_config` code as the CLI, so an
  interactive install cannot accept a setting the flag path would reject. The
  wizard never re-implements a rule.
- Nothing is written before confirmation. Cancel, Ctrl-C, and EOF raise before
  any directory, lock file, stage directory, or backup is created.
- The credential is entered through a masked field, is excluded from prompt
  history, is replaced with `[credential]` on every output path, and never
  reaches `config.yaml`.
- Terminal output escapes control characters, so an untrusted model name or
  endpoint cannot emit ANSI escapes into the user's terminal.
- The installer is now more complex, and `scripts/installer_ui.py` must stay a
  sibling of `scripts/install.py` because it is loaded by path.
- Wizard behavior is verified twice: with a scripted UI for flow logic, and
  through a real pseudo-terminal for rendering and keybindings.

## Alternatives considered

- **Keep flags only.** Rejected. The setup order and the protocol/path coupling
  are easy to get wrong without guidance.
- **Ship a web or GUI installer.** Rejected. It adds a runtime and a port for a
  one-time setup task.
- **Bundle `prompt_toolkit` as a hard dependency.** Rejected. The installer
  must work in a bare Hermes environment, and it must never install packages.
- **Hand-roll raw ANSI and read single keys.** Rejected. Raw terminal handling
  is fragile across terminals and platforms, and easy to get wrong in ways that
  corrupt user input.

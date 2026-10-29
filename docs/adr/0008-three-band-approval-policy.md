# ADR 0008: Three-band decision policy with host approval

## Status

Accepted, 2026-10-29.

## Context

The original policy had two bands. In probability mode a call was allowed when its risk
score was strictly below `threshold` and denied otherwise; in yes/no mode a `yes` verdict
denied and a `no` verdict allowed. Every borderline judgment therefore ended in an
automatic decision: a slightly suspicious proposal was either run without a second look or
blocked outright, with no middle position an operator could act on.

Hermes' `pre_tool_call` contract has a third option. A callback may return
`{"action": "approve", "message": ...}`, which escalates that call to the host's own human
approval gate - the same gate used for dangerous shell patterns, with the same session and
permanent allowlist machinery. The host owns that gate: One Guard asks, Hermes decides.

Two constraints shaped the design:

- Existing installations already carry a configured `threshold`, and its meaning was
  "block at or above". Changing the meaning of that key, or changing the default underneath
  it, could silently widen what an existing installation allows.
- The default behaviour of the plugin is a security boundary for the operator. A migration
  must never turn a configured block cutoff into a softer band without the operator asking
  for it.

## Decision

In probability mode the plugin evaluates three bands:

| Score | Decision |
| --- | --- |
| `score < approval_threshold` | allow |
| `approval_threshold <= score < threshold` | request host approval |
| `score >= threshold` | deny |

The defaults are `approval_threshold: 0.5` and `threshold: 0.7`. A score equal to the
approval cutoff is inside the review band; a score equal to the block cutoff is denied.
The `threshold` key keeps its name and now means "block at or above"; its default moves from
`0.5` to `0.7`. `approval_threshold` is a new key.

Yes/no mode is unchanged: `no` allows, `yes` denies, and there is no review band. A binary
verdict carries no score to place in a band.

Everything that is not an explicit safe judgment still denies: malformed responses,
ambiguous answers, transport failures, timeouts, and invalid configuration.

The review band returns `{"action": "approve", "message": ...}`. The message states the
score and the band and carries no tool arguments, no credentials, and no judge secrets.

The directive also carries an explicit `rule_key`, so the host keys a remembered answer by
the proposal rather than by the tool name. That detail is recorded separately in
[ADR 0009](0009-namespaced-approval-rule-key.md).

Threshold resolution is identical in the runtime and in the installer:

- A configured block cutoff without a configured approval cutoff keeps the legacy two-band
  behaviour: the approval cutoff follows the block cutoff, the review band stays empty, and
  the old "allow below, deny at or above" rule is preserved exactly for every legacy value.
- An approval cutoff above the block cutoff is reduced to the block cutoff. The block
  cutoff always wins, that clamp never widens what is allowed, and no configuration becomes
  invalid.
- With neither cutoff configured, the defaults apply: allow below `0.5`, review from `0.5`
  to `0.7`, deny at `0.7` and above.

The installer adds one rule on top of that resolution: passing `--threshold` is an explicit
change to the block cutoff, so the approval cutoff returns to the default `0.5` unless
`--approval-threshold` is passed as well. The operator selected that default policy in that
case, and the review band becomes visible again instead of staying empty.

That reset can widen what is allowed. An installation whose approval cutoff was below `0.5`
will, after an explicit `--threshold`, allow the scores between that cutoff and `0.5`
instead of sending them to the review band. The widening is a consequence of the operator's
explicit choice, not of the clamp above, and the installer shows the effective band on the
review screen before anything is written. A migration that does not pass `--threshold` keeps
the existing approval cutoff and never widens anything.

The installer materializes both keys, so an existing installation's block cutoff is written
back together with the matching approval cutoff. That keeps the migrated state explicit
and reviewable instead of leaving it to a runtime heuristic.

## Consequences

- The review band is a request, not a guarantee. Hermes' approval gate can be bypassed by
  the operator's own host policy: `approvals.mode: off`, the YOLO toggle, unattended
  approve modes such as `approvals.single_query_mode: approve`, and the session or
  permanent allowlists. The operator accepts that the host owns approval policy; One Guard
  does not and cannot override it from a plugin callback.
- The review band requires a host that understands the `approve` directive. The pinned
  Hermes revision does. A host that ignores the directive would run a mid-band call without
  a human, so the plugin targets that revision and says so in its documentation.
- A legacy installation keeps its exact behaviour until the operator opts in, either by
  setting `approval_threshold` explicitly or by removing the configured `threshold`.
- An explicit `--threshold` returns the review band to the default `0.5` and can widen what
  the installation allows, because a lower configured approval cutoff no longer applies.
  The operator asks for that reset explicitly and sees the effective band on the review
  screen before the installer writes anything.
- The runtime exposes a typed outcome instead of a boolean, so the review band is a distinct
  state rather than a special case inside a deny.

## Alternatives considered

- **Keep the two-band policy.** Rejected. It gives the operator no way to have a human look
  at a borderline proposal without either blocking it or running it unchecked.
- **Lower the block cutoff and treat the middle as denied.** Rejected. It changes what an
  existing installation allows, which is exactly the silent widening this decision avoids.
- **Implement the approval gate inside the plugin.** Rejected. Approvals are host policy;
  a plugin that prompted on its own would duplicate the gate, ignore the host's allowlists
  and unattended modes, and need runtime changes that are out of scope.
- **Fail closed on an approval cutoff above the block cutoff.** Rejected. The reduced
  cutoff keeps the tool protecting the operator instead of disabling it until the
  configuration is repaired.

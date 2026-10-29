# ADR 0009: Namespaced approval rule key over the judged proposal

## Status

Accepted, 2026-10-29.

## Context

The three-band policy sends a mid-band proposal to the Hermes approval gate with
`{"action": "approve", "message": ...}`. The host remembers a `session` or `always` answer
under a rule key, and that key decides which later calls the answer covers.

The pinned Hermes revision resolves the directive in `hermes_cli/plugins.py`
(`_resolve_block_from_details`), which calls
`tools.approval.request_tool_approval(tool_name, message, rule_key=details.rule_key or tool_name)`.
The fallback is the bare tool name. `request_tool_approval` derives a key from the tool
name and a hash of the reason only when it receives an empty `rule_key`, and the dispatcher
substitutes the tool name before that point, so the reason hash is unreachable from the
plugin path.

That fallback was invisible in the documentation and material in behaviour. A plugin that
returns no `rule_key` is keyed by the tool name alone, so a remembered answer covers every
later escalation of that tool name: whatever the arguments were, and whichever plugin asked
for the approval. One `session` answer given for one `terminal` proposal would silently
approve a different `terminal` proposal, and would also cover approvals another plugin
requests for `terminal`.

## Decision

The plugin returns an explicit `rule_key` with every `approve` directive:

```
one-guard:<sha256 of the canonical bounded proposal>
```

- The digest covers the full validated bounded `{tool_name, args}` pair. The bytes are
  produced with the same validation and the same size limit as the judge request, with
  object keys sorted so the same proposal always yields the same key.
- The `one-guard:` namespace keeps a One Guard answer from colliding with another
  plugin's rule for the same tool.
- The digest carries no raw argument, no credential, and no tool name. The persisted
  allowlist entry holds a digest, not the proposal it covers.
- The judge score is not part of the key. The score decides whether a proposal enters the
  review band, and the host owns what happens there. A score that moves between two runs
  of the same proposal must not fragment a remembered answer, so the scope of an answer is
  the proposal and not the judgment.
- A proposal that cannot be serialized denies. It never reaches the gate under a coarser
  key, so a failure cannot widen what a remembered answer covers.

The key identifies the proposal as judged. A peer `pre_tool_call` hook that rewrites the
arguments afterwards is the unsupported configuration of
[ADR 0005](0005-accept-hook-visible-proposal-boundary.md); a remembered answer follows the
judged proposal, not a rewritten one.

The key also scopes an answer only while One Guard's own approval is the one that reaches
the gate. The pinned Hermes revision scans all `pre_tool_call` results and keeps the first
valid `approve` directive, with its message and rule key, so a peer plugin that asks for
approval before One Guard does takes precedence. The host then remembers that peer's rule,
not the One Guard digest, and the namespace no longer describes what the answer covers. A
peer `block` directive vetoes the call regardless of order.

## Consequences

- A remembered `session` or `always` answer covers that exact proposal. A different
  proposal for the same tool is asked about again.
- The scope of an answer is the proposal, not the argument order and not the score. The
  same arguments in a different order yield the same key.
- The rule key narrows what an answer covers; it does not make the gate stricter. The
  host's own policy still decides whether a human is asked at all: `approvals.mode: off`,
  the YOLO toggle, unattended approve modes, and the allowlists remain the host's business.
  The plugin does not claim that a mid-band call always reaches a human.
- The plugin has no way to revoke a remembered answer. That stays host policy.
- The namespace describes One Guard's own approval only. When a peer plugin's approval
  takes precedence, the host remembers the peer's rule and the digest no longer scopes
  the answer.
- A host that ignores `rule_key` falls back to the tool name. The plugin targets the
  pinned Hermes revision.

## Alternatives considered

- **Leave the rule key unset and rely on the host's tool-name grain.** Rejected. It lets
  one remembered answer cover unrelated proposals for the same tool, including proposals
  another plugin escalated.
- **Hash the tool name and the message instead of the arguments.** Rejected. The message
  carries the score, so the key would change when the score changes and a remembered answer
  would stop applying to the same proposal.
- **Include the score in the key.** Rejected for the same reason: the score is a property
  of the judgment, not of the proposal.
- **Put the raw arguments in the rule key.** Rejected. The key is persisted in the
  allowlist and shown in prompts, so it must not carry tool data or secrets.
- **Fall back to a coarser key when serialization fails.** Rejected. A failure must deny,
  not widen the scope of a remembered answer.
- **Implement the approval gate inside the plugin.** Rejected for the same reason as in
  [ADR 0008](0008-three-band-approval-policy.md): approvals are host policy.

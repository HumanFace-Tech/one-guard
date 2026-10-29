# Threat model and supported deployment

For the user-facing overview, see [the README](../README.md). One Guard is an advisory model gate, not an OS sandbox or a guarantee against data exfiltration. It cannot inspect a tool's internal network activity or stop a user with equivalent filesystem access from disabling it. Use process isolation and network egress controls when enforcement must not depend on model judgment.

The scope is model-originated exfiltration attempts expressed in tool proposals, including GET URLs, POST bodies, commands, and inline scripts. One Guard judges the exact hook-visible proposal, not immutable final execution arguments. Detection depends on the judge and the context visible in those arguments.

The supported deployment requires a trusted Hermes runtime and plugin environment. It excludes post-judgment argument-rewriting hooks or middleware. A peer `pre_tool_call` hook can return `modify` after One Guard judges the original arguments, and Hermes can execute the changed arguments without another judgment. Hostile installed extensions and runtime tampering are outside the threat model.

C1 is closed as an accepted out-of-scope limitation by user decision, not fixed. The [boundary regression](verification.md#c1-accepted-boundary-regression) demonstrates this unsupported configuration with benign sentinel strings through real Hermes dispatch and the agent executor. [ADR 0005](adr/0005-accept-hook-visible-proposal-boundary.md) records the decision to retain the normal public-API native plugin.

## What the hook covers

The plugin registers one native `pre_tool_call` hook. It does not filter tool names, so Hermes dispatches the hook for calls that reach this dispatcher, including terminal, browser/HTTP, file, and MCP tools. The hook sees the tool name and arguments exposed by Hermes at judgment time; it does not bind them to immutable final execution arguments. It does not intercept system calls, nested requests made inside a tool, or activity that occurs outside the guarded dispatcher. A tool's own effects are not necessarily represented fully in its arguments.

Inline script text and chained commands included in arguments are sent as-is for judgment. Referenced script files, encoded payloads, and hidden tool implementation are not read or executed for inspection. A filename is not evidence that a script is safe. The default prompt directs the judge to deny when hidden content or missing context prevents a safe assessment. A preceding file-write call and later script-execution call are judged separately; the execution decision does not automatically include the file's contents.

Tool arguments may include credentials or private data. Sending them to a remote judge makes that service a data recipient and trust boundary. Configure a trusted local endpoint or a service approved for the data. One Guard does not redact arguments before sending them. The request contains the full bounded tool name and arguments, but not conversation history. See [runtime configuration](configuration.md#judge-request-and-response) for request limits and transport behavior.

## The review band is a request, not a guarantee

In probability mode a score inside the review band produces an `approve` directive, which hands the call to the Hermes approval gate. The host owns that gate, so the operator's host policy decides the outcome:

- `approvals.mode: off`, the YOLO toggle, and unattended approve modes such as `approvals.single_query_mode: approve` can approve the call with no human present.
- A `session` or `always` answer is remembered under a rule key the plugin sets itself: a namespaced `one-guard:<sha256>` digest over the full validated bounded tool name and arguments. A remembered answer therefore covers that exact proposal only, and it survives restarts in the permanent allowlist. A plugin that sets no rule key is keyed by the bare tool name, which would also cover approvals other plugins request for the same tool.
- The namespace scopes an answer only when One Guard's own approval is the one that reaches the gate. Hermes keeps the first valid `approve` directive of a call, so a peer plugin that asks for approval before One Guard does takes precedence: the peer plugin's message and rule key are what the host remembers, and One Guard's digest does not scope that answer. A peer's `block` directive vetoes the call either way.
- A context with nobody to answer blocks the call, because Hermes fails closed there.

The operator has accepted that host policy as authoritative. One Guard does not attempt to override it, and a plugin callback cannot: the directive is a request, not a decision. A host that does not understand the `approve` directive would run a mid-band call without a human, so the plugin targets the pinned Hermes revision that implements it.

The approval message states the score and the band only. It carries no tool arguments, no credentials, and no judge response body, so it cannot leak a secret into a transcript, a chat surface, or a persisted allowlist entry. The rule key is a SHA256 digest of the proposal, so the persisted allowlist entry holds a digest rather than the arguments it covers.

## Token and judge trust boundaries

The manifest declares `token` as `type: secret` with `env: ONE_GUARD_TOKEN`. Hermes' settings UI receives only its environment name and presence flag, never the token value. Hermes loads `.env` on restart; the plugin reads its supported secret resolver. Do not expose `.env` contents in shell history, logs, support bundles, or shared process environments. The plugin does not include the token in its block message.

On upgrade, the installer removes an old YAML `token` setting and migrates it into `.env` when no token was supplied and no `.env` token already exists. An explicit installer environment value takes precedence, including an empty value to clear the token. Unrelated dotenv bindings remain intact. Malformed dotenv data, duplicate token assignments, and multiline token assignments require repair before installation. Backups may contain old YAML credentials or `.env` secrets and need the same protection as the active files.

Environment overrides can change every policy control and the recipient, not only authentication. An operator can change `ONE_GUARD_ENDPOINT`, allow plaintext remote HTTP with `ONE_GUARD_ALLOW_INSECURE_HTTP=true`, or weaken policy through the prompt, mode, model, or thresholds. Holding the block cutoff fixed, lowering `approval_threshold` sends more scores to the approval gate; holding the approval cutoff fixed, lowering `threshold` blocks more scores. The host may approve a review-band call without a human. Plaintext HTTP exposes both bearer credentials and tool arguments. Treat environment access, config writes, custom prompts, judge operators, and installed peer plugins as trusted policy administration. Appending a custom prompt does not prevent that prompt from contradicting or weakening the built-in policy.

The judge receives the full bounded tool name and arguments. Its response is treated as a policy decision only after strict shape and value validation, but model output can still be wrong or manipulated by adversarial content. Tool arguments are explicitly marked untrusted in the built-in prompt; this is not a security boundary against a compromised or unreliable judge.

One Guard's hook and model judgment do not provide OS isolation. Enforcement that must withstand a compromised or disabled plugin needs process isolation and network egress controls outside One Guard.

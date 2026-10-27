# Runtime configuration

For the short installation overview, see [the README](../README.md). This page documents runtime settings and the judge request and response contracts.

The installer enables the plugin at `plugins.enabled` and writes non-secret settings under `plugins.entries.one-guard.settings` in `config.yaml`. Runtime environment variables named `ONE_GUARD_<SETTING>` override Hermes config, except `judge_model`, which uses `ONE_GUARD_MODEL`. Installer `--model` writes `judge_model`; the judge request uses the API field `model`. Hermes reserves the plugin key `model`, so use `judge_model` in YAML. The token uses Hermes' secret resolution and `.env` loading at startup, including profile-scoped secrets. Numeric and boolean environment values must be valid JSON literals, such as `0.4`, `5`, or `true`.

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

Environment overrides are `ONE_GUARD_PROTOCOL`, `ONE_GUARD_ENDPOINT`, `ONE_GUARD_TOKEN`, `ONE_GUARD_MODEL`, `ONE_GUARD_PROMPT`, `ONE_GUARD_MODE`, `ONE_GUARD_TIMEOUT`, `ONE_GUARD_THRESHOLD`, and `ONE_GUARD_ALLOW_INSECURE_HTTP`. For example:

```sh
ONE_GUARD_TOKEN='your-token' \
ONE_GUARD_THRESHOLD=0.4 \
ONE_GUARD_ALLOW_INSECURE_HTTP=false \
hermes
```

`localhost` is a hostname, not a literal loopback IP, so `http://localhost:...` is rejected unless `allow_insecure_http` is true. Prefer a literal loopback address or HTTPS. Remote HTTP sends tool arguments without transport encryption and should be avoided.

## Judge request and response

For every callback, the plugin sends a POST with JSON content and an optional `Authorization: Bearer ...` header. Chat requests contain one system message and one user message. The user message is a JSON object with `tool_name` and the complete `args`; native requests put that object in `state`. Neither includes conversation history. The plugin does not follow redirects or ambient proxy settings.

The chat request body has the OpenAI-compatible `messages` shape below. The system content contains the built-in policy and any appended prompt; the user content is the serialized tool proposal.

```json
{
  "model": "<judge_model, when nonempty>",
  "messages": [
    {"role": "system", "content": "<built-in policy and appended prompt>"},
    {"role": "user", "content": "{\"tool_name\":\"terminal\",\"args\":{\"command\":\"pwd\"}}"}
  ],
  "temperature": 0,
  "stream": false,
  "max_tokens": 64,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "one_guard",
      "strict": true,
      "schema": {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["yes", "no"]}}, "required": ["verdict"], "additionalProperties": false}
    }
  }
}
```

The `response_format` schema is the strict JSON constraint shown for `yes_no` mode. `probability` mode instead sends `{"type": "number", "minimum": 0, "maximum": 1}`. A judge that ignores `response_format` still works, but then it has to produce the exact JSON shape on its own.

The combined UTF-8 JSON object containing `tool_name` and `args`, including JSON syntax and escaping, is limited to 128 KiB. The full serialized request is limited to 256 KiB, including the second layer of escaping when that JSON becomes a message string. A response is limited to 64 KiB. Inputs must be finite JSON-compatible values with bounded nesting. One Guard denies rather than truncating oversized input.

At most four transport workers run concurrently. Additional calls wait for capacity within their original deadline, so eight ordinary simultaneous calls can complete in two waves. Socket connect, TLS, writes, and each raw header/body read use the remaining deadline. Slow-drip responses cannot renew it and sockets close when the exchange ends. The caller denies on deadline expiry. Python's system DNS resolver cannot be cancelled reliably; a stuck resolver may retain a worker and slot after that deadline. Slots stay reserved until workers actually exit, bounding thread creation even during DNS failure. Literal IP endpoints avoid DNS lookup delays.

Transport accepts HTTPS or HTTP to literal loopback IP addresses by default. Hostnames such as `localhost` do not count as literal loopback. Remote HTTP is rejected unless `allow_insecure_http` is enabled. The plugin does not follow redirects or use ambient proxies.

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

Noul is p(risk=true). Only a finite number in `[0,1]` strictly below `threshold` allows; equality denies. `yes_no` instead sends `type: choice` with criteria `{"yes":"Risk/unsafe/exfiltration","no":"Safe/no exfiltration"}`. The question object sent in the request is:

```json
{
  "type": "choice",
  "instructions": "<built-in policy, appended prompt, and risk proposition question>",
  "criteria": {"yes": "Risk/unsafe/exfiltration", "no": "Safe/no exfiltration"}
}
```

And the required answer is exactly:

```json
{"type":"choice","choice":"no","probabilities":{"yes":0.1,"no":0.9},"confidence":0.8}
```

Choice must be `yes` or `no`, both named probabilities and confidence must be finite numbers in `[0,1]`, probabilities must sum to 1 within absolute tolerance `1e-6`, and choice must agree with their maximum. Ties deny. `no` allows only when strictly more likely than `yes`; `yes` denies. The configured threshold applies only to probability/Noul mode. Missing or extra fields, extra questions, wrong answer types, booleans, NaN, conflicting probabilities, duplicate JSON keys, and chat envelopes on the native path deny. There is no retry with another protocol.

Noul is a native typed probability, unlike the generic chat mode's model-generated JSON estimate. Upstream explicitly does not guarantee calibration for your data; One Guard establishes neither calibration nor real-world detection quality. Upstream also notes model-side input limits. For example, h2o-lightning keeps the first and last 16000 tokens for states exceeding 32000 tokens. One Guard sends full bounded arguments without local truncation, but cannot guarantee that a server processes every token.

## Live endpoint observations

Past live smoke tests contacted a private LAN judge service. Initial six-case requests timed out at 10 seconds each; later runs used a 60-second deadline and passed all six synthetic cases in both Noul probability and binary yes/no modes. Individual requests took 19.479 to 21.831 seconds. The smoke establishes those twelve observed outcomes only; it is not a security benchmark, calibration study, proof of prompt-injection resistance, or broad classification evaluation. The address is intentionally omitted. The example endpoint above is fictional and was not used for those tests. The default remains 5 seconds; explicit timeouts support up to 120 seconds. See [verification notes](verification.md#live-endpoint-observations).

A separate trace-derived run on 2026-10-27 sent 12 inert proposals to the user-supplied private LAN judge using `h2o-lightning-4b`, threshold `0.5`, a 120-second timeout, and two workers. It matched 11 of 12 subjective expected labels. The judge allowed the `guard-removal` proposal (score `0.2594485504`), which was the one miss. This single run does not establish calibration or general detection efficacy. The incident command and three fragments came from one published trace; other cases were synthetic adaptations, not independent traces. See the [full results and limitations](verification.md#trace-derived-live-run-2026-10-27).

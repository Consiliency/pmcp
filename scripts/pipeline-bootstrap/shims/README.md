# Pipeline Bootstrap Harness Shims

RS installs three executable shims named `codex`, `claude`, and `gemini` so
`pipeline-init` and governed-pipeline harness dispatch can find the expected
binary names on `PATH` while routing agent execution through the workers
subscription host.

The shims use Node stdlib only. They do not require an npm install, do not add
package dependencies, and can run anywhere the bootstrap runner already has
Node 20 or newer.

## Environment

Each shim requires command-scoped worker connection settings:

```sh
WORKER_BASE_URL=<workers-url>
WORKER_API_KEY=<redacted>
```

The shim posts:

```json
{"harness":"codex","prompt":"...","args":["..."]}
```

to `${WORKER_BASE_URL}/jobs/agent-invoke` with bearer auth. Worker responses
must be `text/event-stream` frames using `stdout`, `stderr`, `exit`, and
`error` events. `stdout` data is written to stdout and `stderr` data is written
to stderr as frames arrive.

## Supported Argv

Supported forms match the governed-pipeline runtime contract in
`docs/architecture/agent-shim-argv.md`.

Codex:

```sh
codex exec --sandbox read-only --output-last-message /tmp/out "prompt"
codex exec --model gpt -c model_reasoning_effort=\"high\" --full-auto --json "prompt"
printf '%s' "prompt" | codex exec --sandbox read-only --output-last-message /tmp/out -
codex exec resume <session_id> --full-auto --json "prompt"
codex -p "prompt"
```

Claude:

```sh
claude -p --output-format json --setting-sources project --permission-mode plan "prompt"
claude -p --output-format json --setting-sources project --permission-mode auto --agent worker --effort high --model sonnet --session-id <id> --json-schema '{"type":"object"}' --plugin-dir .pipeline/claude-plugin "prompt"
```

Gemini:

```sh
gemini --model gemini-pro --approval-mode auto_edit -p "prompt" --output-format json
gemini -p "prompt" --output-format json
gemini --resume <session_id> -p "prompt" --output-format json
```

## Exit Codes

- exit 1: worker auth failed, worker rejected the request, or the terminal
  worker event reports harness failure.
- exit 2: local usage error, missing prompt, unsupported argv, missing
  `WORKER_BASE_URL`, or missing `WORKER_API_KEY`.
- exit 3: worker infrastructure failure, 5xx response, network failure, or
  malformed SSE.

Successful terminal `exit` events mirror the worker-provided code.

## Local Smoke

Use a command-scoped real-worker smoke only after an operator has supplied the
worker URL and key outside logs:

```sh
WORKER_BASE_URL=<url> WORKER_API_KEY=<redacted> ./scripts/pipeline-bootstrap/shims/codex -p "say hello"
```

Subscription swapping is an operator action performed by changing workers auth
secrets and redeploying the worker service. Do not edit these shims, workflow
logs, or phase evidence to carry subscription auth.

# Cursor CLI reference

Captured from `cursor-agent --help` on 2026-08-07.

```
Usage: agent [options] [command] [prompt...]

Start the Cursor Agent

Arguments:
  prompt                       Initial prompt for the agent

Options:
  -v, --version                Output the version number
  --api-key <key>              API key for authentication (can also use
                               CURSOR_API_KEY env var)
  -H, --header <header>        Add custom header to agent requests (format:
                               'Name: Value', can be used multiple times)
  -e, --endpoint <url>         Target API endpoint URL (can also use
                               CURSOR_API_ENDPOINT env var) (default:
                               "https://api2.cursor.sh", env:
                               CURSOR_API_ENDPOINT)
  -p, --print                  Print responses to console (for scripts or
                               non-interactive use). Has access to all tools,
                               including write and shell. (default: false)
  --output-format <format>     Output format (only works with --print): text |
                               json | stream-json (default: "text")
  --stream-partial-output      Stream partial output as individual text deltas
                               (only works with --print and stream-json format)
                               (default: false)
  --mode <mode>                Start in the given execution mode. plan:
                               read-only/planning (analyze, propose plans, no
                               edits). ask: Q&A style for explanations and
                               questions (read-only). (choices: "plan", "ask")
  --plan                       Start in plan mode (shorthand for --mode=plan).
                               (default: false)
  --resume [chatId]            Select a session to resume (default: false)
  --continue                   Continue previous session (default: false)
  --model <model>              Model to use (e.g., gpt-5, sonnet-4-thinking).
  --list-models                List available models and exit (default: false)
  -f, --force                  Force allow commands unless explicitly denied
                               (default: false)
  --workspace <path-or-name>   Workspace directory or saved workspace name
  --add-dir <path>             Add an additional workspace root directory
  -h, --help                   Display help for command
```

## kcia adapter mapping

| `RunRequest` field | CLI flag |
|---|---|
| `stream=True` | `--print --output-format stream-json --stream-partial-output` |
| `allow_edits=True` | `--force` |
| `resume` + `session_id` | `--resume <id>` |
| `model` | `--model <model>` |

The adapter implementation lives in `cli/src/kcia/providers/cursor.py`.

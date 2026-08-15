# Changelog

Two versions are tracked independently. CLI entries are headed `## X.Y.Z`; control-plane
entries are headed `## control-plane X.Y.Z`. See [RELEASING.md](RELEASING.md).

## 0.6.0 — 2026-08-15

### CLI

- `kcia done` now logs the session automatically after committing, reusing the active
  task's id and the commit SHA(s) it just wrote — `kcia session log` no longer has to be
  run by hand. Prints `Session saved: <id>` on success, or a fallback manual command if
  logging fails.

---

## 0.4.1 — 2026-08-14

### CLI

- Drop the interim `work run` subcommand. The bare `kcia work` command is the only pipeline
  entry point; single-wave control moves to `--wave <id>` (e.g. `kcia work --wave
  understanding`).

## 0.4.0 — 2026-08-14

### CLI

- **Breaking:** merged `kcia task` and `kcia wave` into `kcia work`. `kcia work "<text>"`
  creates a task and runs the pipeline; bare `kcia work` continues the active task.
  Single-wave control uses `--wave <id>` on the same command (not a separate subcommand).
  Subcommands `show`, `fetch`, `answer`/`inject`, `abort`, `list`, `approve`,
  `plan`, `retry`, `skip`, and `logs` live under the `work` prefix. The old `task` and
  `wave` command groups are removed.

## 0.0.1 — 2026-08-09

First published version. The history before it was discarded, so this entry describes
everything that exists rather than what changed.

### CLI

- `kcia init` — detects the technologies in a repository, writes `.ai/manifest.yaml`,
  composes profile bundles into `.ai/generated/`, renders the Claude/Cursor/AGENTS adapters
  and adds every generated path to the project's `.gitignore`. Idempotent; `--yes` for
  non-interactive runs, `--no-gitignore` to opt out, `--refresh-context` to regenerate
  `project.md`.
- Profiles — `profile list/show/detect/validate/scaffold`, single-parent inheritance capped
  at three levels, profile packs, and per-path resolution through the manifest `roots`.
- Agents — `agent set/show/swap/models`, two roles (`planner`, `builder`) over the Claude
  Code and Cursor adapters, resolved flag → repo → global → catalog. `--live` checks the
  curated catalog against the installed CLI.
- Tasks — `task init/show/fetch/answer/abort`. `--scope <path>` limits which manifest roots
  drive the active profiles; a Jira issue key starts the task in ticket mode and its body is
  fetched into `.ai/context/ticket.md`.
- Waves — `wave list/run/approve/plan/retry/skip/logs`. Five ordered steps, a session in
  `.ai/local/session.json`, a machine-wide lock, prompt composition and per-profile
  validation with retries.
  - The run pauses before `implementation`, the first wave that can change code, and points
    at the plan; `wave approve` records the decision and continues. Exit code `2`.
  - An agent that cannot proceed answers `BLOCKED: <question>`; the wave stops as `blocked`
    rather than being recorded as completed.
  - Ctrl-C terminates the provider subprocess and returns the wave to `pending` (exit code
    `130`); a second Ctrl-C exits immediately.
  - Prompt composition budgets tokens per section and filters profile references per wave
    via `reference_tags`. Over `budget.max_prompt_tokens`, references drop by tag priority
    and a `## Context budget` footer says what was omitted.
  - A live status line reports the wave, role, provider, model, elapsed time, tool calls and
    tokens; `--quiet` turns it off.
- Git — the branching model is chosen once at `kcia init` (git flow, or work on the current
  branch) and stored in `.ai/local/git.yaml`. `kcia work` opens the task's branch by
  itself. `branch start/base/config` stay for manual use. `kcia commit` closes a task with
  two commits — the plan (`docs:`) and the code — after showing the messages and files and
  waiting for confirmation. `--single`, `--dry-run`, `--type`, `--ticket/--no-ticket`,
  `--push`, `--pr`.
- MCP — `mcp catalog/add/remove/list`, per-repository servers with per-role gating, enforced
  on Claude Code through `--mcp-config` and declarative on Cursor.
- Diagnostics — `kcia doctor`: toolchain, provider install and authentication, agent
  readiness, repository state, branch and remote.
- Not implemented yet, these exit 1: `kcia sync`, `kcia ask`, `kcia auth`.

### control-plane 1.5.0

- Builtin profile pack: `_dart-core`, `mobile-flutter`, `web-flutter`, `backend-dart`.
- Five waves with per-wave agent, edit scope, `reference_tags` and a root `budget` block.
- Guardrails, including git left read-only for the agents (`status`, `diff`, `log`) —
  `checkout -b`, `commit` and `push` are the user's, through `kcia branch start` and
  `kcia commit`.
- Provider catalog, MCP catalog, agent roles, and the adapter templates.

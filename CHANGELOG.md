# Changelog

Two versions are tracked independently. CLI entries are headed `## X.Y.Z`; control-plane
entries are headed `## control-plane X.Y.Z`. See [RELEASING.md](RELEASING.md).

## Unreleased

### CLI

- `kcia branch start` / `kcia branch base`: git-flow branching for the active task
  (`feature|fix|docs/<KEY>-<slug>`). The base branch is detected from the repository and
  **asked for** when it is ambiguous, then remembered in `.ai/local/git.yaml`.
- `kcia commit`: closes a task with two commits — the plan (`docs:`) and the code — after
  showing the messages and files and waiting for confirmation. Message format
  `type: KEY - subject`, with the key omitted when the task has no ticket; types are
  `feat`, `fix`, `docs`. `--single`, `--dry-run`, `--type`, `--ticket/--no-ticket`,
  `--push`, and `--pr` (needs `gh`).
- `kcia task inject` is now `kcia task answer` — the name describes what the user does
  (answer the agent, or add context) instead of the mechanism. `inject` still works as a
  hidden alias.
- `kcia wave run` closes a finished pipeline by pointing at `git diff` and `kcia commit`.
- `kcia doctor` reports `gh`, the current branch, the detected base branch, and the remote.
- Prompt composition now accounts for token usage per section (`waves/budget.py`) and
  filters profile references by wave via `reference_tags` in `control-plane/waves/waves.yaml`.
- Context budget: when a prompt exceeds `budget.max_prompt_tokens` (overridable in
  `~/.config/kcia/config.yaml`), references drop by tag priority and a `## Context budget`
  footer is appended.
- `kcia task init --scope <path>` limits which manifest roots drive active profiles.
- Prompts include a precomputed repository map (packages, profiles, test/lint commands).

### control-plane 1.1.0

- Profile `references` accept optional `tags`; builtin profiles tagged for wave filtering.
- `waves.yaml` gains per-wave `reference_tags` and a root `budget` block.
- Guardrails: git is read-only for the agents (`status`, `diff`, `log`). `checkout -b`,
  `commit` and `push` are blocked — those are the user's, via `kcia branch start` /
  `kcia commit`.

- `kcia init` implemented: detects profiles, writes `.ai/manifest.yaml`, composes profile
  bundles into `.ai/generated/`, renders the Claude/Cursor/AGENTS adapters, and adds every
  generated path to the project's `.gitignore`. Idempotent; `--yes` for non-interactive
  runs, `--no-gitignore` to opt out.
- Project state is no longer committed: `.ai/` in full, plus the generated `CLAUDE.md`,
  `AGENTS.md`, and `.cursor/rules/`, are added to the project's `.gitignore`.
- Documented installation, update, and release procedures (`README.md`, `RELEASING.md`).

## 0.1.0 — 2026-08-07

- Initial bootstrap: repository structure, control-plane data, CLI stubs.

## control-plane 1.0.0 — 2026-08-07

- Initial builtin profile pack (`_dart-core`, `mobile-flutter`, `web-flutter`,
  `backend-dart`), waves, guardrails, provider catalog, and adapter templates.

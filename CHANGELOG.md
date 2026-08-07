# Changelog

Two versions are tracked independently. CLI entries are headed `## X.Y.Z`; control-plane
entries are headed `## control-plane X.Y.Z`. See [RELEASING.md](RELEASING.md).

## Unreleased

### CLI

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

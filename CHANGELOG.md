# Changelog

Two versions are tracked independently. CLI entries are headed `## X.Y.Z`; control-plane
entries are headed `## control-plane X.Y.Z`. See [RELEASING.md](RELEASING.md).

## Unreleased

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

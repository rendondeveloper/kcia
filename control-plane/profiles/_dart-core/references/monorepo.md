# Monorepo

## Melos

- If `melos.yaml` exists, use the workspace scripts (`melos run test:all`, `melos run verify`).
- Changes to shared packages may require validating several consumer packages.

## Shared packages

- Keep stable APIs in `shared_*` or `core` packages.
- Version breaking changes with a per-package changelog.
- Avoid circular dependencies between workspace packages.

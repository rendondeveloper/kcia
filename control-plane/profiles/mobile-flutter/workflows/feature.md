# Workflow: feature (mobile)

## Folder structure

Each feature follows the canonical layout in `_dart-core` `references/architecture.md`
(`lib/features/<feature>/` with `data`/`domain`/`presentation` layers). That layout
is mandatory — do not adapt it to match an existing divergent repo structure.

## Steps

1. Design UI that's responsive across different mobile screen sizes.
2. Keep business logic out of widgets. Don't force bloc/cubit or
   provider: detect which one the repo already uses (dependencies in `pubspec.yaml`,
   existing patterns in `presentation/`) and follow that same state
   management solution consistently in the new feature.
3. Use `Spacer` for flexible empty space; `Expanded` only when the child must fill space.
4. Prefer `Flex` spacing properties (`spacing`) over `SizedBox` between children.
5. Add golden tests for stable screens when the team uses them.
6. Run `fvm flutter test` and `fvm flutter analyze`.

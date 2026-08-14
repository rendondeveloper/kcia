# Workflow: feature (web)

1. Define breakpoints and responsive behavior before implementing.
2. Keep business logic out of widgets. Don't force bloc/cubit or
   provider: detect which one the repo already uses (dependencies in `pubspec.yaml`,
   existing patterns in `presentation/`) and follow that same state
   management solution consistently in the new feature.
3. Validate in Chrome and at least a second browser used by the team.
4. Measure impact on bundle size if you add heavy dependencies.

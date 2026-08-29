# Dart coding standards

## Imports

- Use `package:` imports with `show` when crossing layer or feature boundaries.
- Order imports: SDK, external packages, internal packages, relative.
- Avoid relative imports that leave the current feature.

## Naming

- `PascalCase` for types; `camelCase` for members and variables.
- Descriptive suffixes: `*Repository`, `*DataSource`, `*UseCase`, `*Bloc`.
- File names in `snake_case.dart`.

## Complexity

- Short functions with a single clear responsibility.
- Avoid deep nesting; extract private methods.
- Don't use `print` in production code; use structured logging.

## Models

- Prefer `freezed` over `Equatable` for immutable models.
- JSON serialization with `json_serializable`; don't edit `*.g.dart` or `*.freezed.dart` files.
- Use dedicated mapper classes between domain models and DTOs — see
  `references/architecture.md` ("Mapper at the data boundary" and the
  `feature_example` layout).

## Control flow

- Always use braces in `if`, `for`, and `while`, even on a single line.

## State manager consumption correctness

- State-management agnostic rule: every field read inside a build/consume
  callback must be covered by whatever mechanism the chosen approach uses to
  decide when to rebuild or notify. A missing field there is a bug, not a
  style preference.
- Bloc/Cubit: list every state field the builder reads in `buildWhen` (or
  `listenWhen` for side effects); don't compare only a subset of fields.
- Provider: scope `Consumer`/`context.watch` to the exact model/field needed
  (`Selector`, or a narrow `ChangeNotifier` getter) instead of watching the
  whole model and expecting every field change to redraw.
- Riverpod: `ref.watch` every provider whose value the widget reads; if only
  one field of a bigger state should trigger a rebuild, watch
  `provider.select((s) => s.field)` instead of the whole state object.

## Boundary-value correctness

- Counting/threshold logic (attempts remaining, limits, pagination edges)
  must have unit tests covering the edges (0, max, max-1) before it's
  considered done — see also `references/testing.md`.

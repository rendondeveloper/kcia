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
- Use dedicated mapper classes between domain models and DTOs.

## Control flow

- Always use braces in `if`, `for`, and `while`, even on a single line.

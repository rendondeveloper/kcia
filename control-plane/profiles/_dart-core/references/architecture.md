# Architecture

## Layers

- Follow clean architecture: presentation → domain → data.
- Business logic lives in domain; data implements the domain's contracts.
- Presentation (widgets, blocs, controllers) does not access external APIs directly.

## Features and barrels

- One folder per feature with a barrel file (`feature.dart`) that exports the public API.
- Each layer within the feature exposes its barrel (`domain.dart`, `data.dart`).
- Don't import internal implementations from other features.

## Dependencies

- Domain does not depend on Flutter or infrastructure packages.
- Data can depend on HTTP, DB, and serializers; not on widgets.

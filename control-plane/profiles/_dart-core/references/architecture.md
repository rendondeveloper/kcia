# Architecture

## Layers

- Follow clean architecture: presentation → domain → data.
- Business logic lives in domain; data implements the domain's contracts.
- Presentation (widgets, blocs, controllers) does not access external APIs directly.

## Features and barrels

- One folder per feature.
- **One barrel per layer** — never per sub-folder:
  - `data/data.dart` — models, data-source interfaces/impls, mappers, repository impls
  - `domain/domain.dart` — entities, use cases, repository interfaces
  - `presentation/presentation.dart` — state holders, screens, views, widgets
- **One feature barrel** (`<feature>/<feature>.dart`) that re-exports **only** the
  three layer barrels. Do not export individual files from the feature barrel.
- **No sub-folder barrels** — do not create `datasources/datasources.dart`,
  `repositories/repositories.dart`, `entities/entities.dart`, `cubit/cubit.dart`, etc.
- All `export`/`import` statements inside any barrel must use absolute `package:`
  paths. Relative paths (`./`, `../`) inside barrels are forbidden.
- Non-barrel feature code also uses absolute `package:` paths for every
  cross-file import (same-layer or cross-layer). Relative paths are forbidden.
- Never import internal implementations from another feature past its barrel.

### Import direction

| Case | Import target | `show` required |
|------|---------------|:---------------:|
| Same feature, same layer | Absolute `package:` path to the concrete file | optional |
| Same feature, cross-layer | The **layer barrel** of the target layer | **yes** |
| Cross-feature | The **feature barrel** of the target feature | **yes** |
| Cross-package | The **package public barrel** | **yes** |

Every import that goes through a layer/feature/package barrel must declare the
exact symbols via `show`. Barrel imports without `show` are forbidden.

### Barrel checklist

- Feature barrel re-exports only the three layer barrels.
- Each layer barrel re-exports every public symbol of its layer (nothing from
  another layer; no public symbol missing).
- All `export`/`import` inside barrels use absolute `package:` paths.
- Cross-layer / cross-feature / cross-package imports go through the matching barrel.
- Every such import has a `show` clause listing the exact symbols used.
- No consumer import reaches inside another feature or package past its barrel.

## Feature layout (canonical)

Source of truth for where each artifact lives inside a feature. Feature root:
`lib/features/<feature>/` (Flutter packages). Cubit vs BLoC folder names are
illustrative — pick the project's state manager, keep the same layer positions.

### Non-negotiable

- The canonical layout is mandatory for new and modified features.
- If the repository uses a different layout (`lib/feature/`, flat `lib/<feature>/`,
  `feature/`, etc.), adjust the codebase to `lib/features/<feature>/…` — do not
  keep the divergent structure and do not propose alternate trees.
- Do not treat the current repo layout, tickets, or READMEs as higher authority
  than this reference.
- Do not block or ask whether to follow the layout — follow it.

### Artifact paths

Paths below are under `lib/features/<feature>/`.

| Artifact | Layer | Path |
|----------|-------|------|
| Entity | domain | `domain/entities/<entity>.dart` |
| Repository interface | domain | `domain/repositories/<feature>_repository.dart` (flat — no `domain/data/` nesting) |
| Use case | domain | `domain/usecases/<verb>_<noun>.dart` |
| DataSource interface | data | `data/data_sources/interface/<feature>_*.dart` |
| DataSource impl | data | `data/data_sources/<feature>_*_impl.dart` (`Impl` suffix) |
| Model | data | `data/models/<feature>_model.dart` |
| Mapper class | data | `data/mappers/<feature>_mapper.dart` |
| Repository impl | data | `data/repositories/<feature>_repository_impl.dart` |
| State holder + state | presentation | `presentation/cubit/` or `presentation/bloc/` |
| Screen | presentation | `presentation/screens/<feature>_screen.dart` |
| View | presentation | `presentation/views/` (when a screen grows large) |
| Feature barrel | feature root | `<feature>.dart` |
| Layer barrels | per layer | `data/data.dart`, `domain/domain.dart`, `presentation/presentation.dart` |

### Layout rules

1. Repository interface is flat in `domain/repositories/` — never nest under
   `domain/data/`.
2. DataSource interfaces live in `data/data_sources/interface/`, not in
   `domain/`. The repository depends on that interface; the interface is owned
   by the data layer.
3. Model → entity goes through a mapper class in `data/mappers/` — never an
   extension or a static factory on the entity.
4. Sub-folder barrels are forbidden — only the three layer barrels plus the
   feature barrel.
5. Reference layout is the synthetic `feature_example` below; real features
   must match it.

### Reference layout — `feature_example`

```
lib/features/feature_example/
├── feature_example.dart              # feature barrel — only the 3 layer barrels
├── data/
│   ├── data.dart                     # data layer barrel (absolute package: paths)
│   ├── data_sources/
│   │   ├── interface/
│   │   │   ├── feature_example_remote_data_source.dart
│   │   │   └── feature_example_local_data_source.dart
│   │   ├── feature_example_remote_data_source_impl.dart
│   │   └── feature_example_local_data_source_impl.dart
│   ├── mappers/
│   │   └── feature_example_mapper.dart
│   ├── models/
│   │   └── feature_example_model.dart
│   └── repositories/
│       └── feature_example_repository_impl.dart
├── domain/
│   ├── domain.dart
│   ├── entities/
│   │   └── feature_example.dart
│   ├── repositories/
│   │   └── feature_example_repository.dart
│   └── usecases/
│       └── get_feature_example.dart
└── presentation/
    ├── presentation.dart
    ├── cubit/                        # or bloc/ — project choice
    │   ├── feature_example_cubit.dart
    │   └── feature_example_state.dart
    ├── screens/
    │   └── feature_example_screen.dart
    └── views/
        └── feature_example_loaded_view.dart
```

## Mapper at the data boundary

Use a dedicated mapper class (const-constructible when possible). Register it as
a shared/singleton binding in the project's DI. The repository impl receives the
mapper and data-source interfaces via constructor — never calls HTTP/storage
APIs directly.

```dart
class FeatureExampleMapper {
  const FeatureExampleMapper();

  FeatureExample map(FeatureExampleModel model) => FeatureExample(
    id: model.id,
    name: model.name,
  );

  List<FeatureExample> mapList(List<FeatureExampleModel> models) =>
      models.map(map).toList();
}

class FeatureExampleRepositoryImpl implements FeatureExampleRepository {
  FeatureExampleRepositoryImpl(this._remoteDataSource, this._mapper);

  final FeatureExampleRemoteDataSource _remoteDataSource;
  final FeatureExampleMapper _mapper;

  @override
  Future<FeatureExample> getFeature() async =>
      _mapper.map(await _remoteDataSource.getFeature());
}
```

## Dependencies

- Domain does not depend on Flutter or infrastructure packages.
- Data can depend on HTTP, DB, and serializers; not on widgets.

## Layer responsibilities

- Presentation: holds UI/ephemeral state and calls domain use cases or
  repositories through their interfaces. Never calls a data source, storage
  API, or HTTP client directly. Never contains business rules (thresholds,
  persistence decisions, retry policy).
- Domain: owns business rules and declares repository *interfaces* only.
  Never imports Flutter or a data-source-specific package (HTTP client,
  local storage SDK, etc.).
- Data: implements domain repository interfaces via one or more data sources
  (local/remote), each behind its own interface (e.g. a repository
  implementation delegating to a `*LocalDataSource`/`*RemoteDataSource`).
  Maps storage/DTO primitives to domain types at the boundary. Never leaks
  storage-specific types (raw JSON maps, storage keys) past the data layer.

## Shared/reusable logic

- A state holder, widget, or util used by more than one feature — or built to
  be reusable — belongs in a shared/core location, not inside the feature
  that first introduced it, regardless of the state management approach it's
  built with.
- Shared logic must not import anything from a specific feature.

## State modeling

- Prefer a small closed `freezed` union for lifecycle-shaped state (e.g.
  `idle | active(payload) | finished`) over accreting boolean/nullable flags
  on one state class as new cases are added.
- Anti-pattern: one state class with `isActive`, `remaining`, and `isFinished`
  booleans/nullables; prefer a sealed union whose variants encode each phase.

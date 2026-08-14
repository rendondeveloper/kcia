# Workflow: feature (mobile)

## Folder structure

Each feature follows Clean Architecture with `data`/`domain`/`presentation` layers:

```
features/feature_example/
├── feature_example.dart
├── data/
│   ├── data.dart
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
│   │   ├── feature_example.dart
│   │   └── feature_example_input.dart
│   ├── repositories/
│   │   └── feature_example_repository.dart
│   └── usecases/
│       └── get_feature_example.dart
└── presentation/
    ├── presentation.dart
    ├── cubit/
    │   ├── feature_example_cubit.dart
    │   └── feature_example_state.dart
    ├── screens/
    │   └── feature_example_screen.dart
    └── views/
        └── feature_example_loaded_view.dart
```

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

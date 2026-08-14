# Workflow: feature (mobile)

## Estructura de carpetas

Cada feature sigue Clean Architecture con capas `data`/`domain`/`presentation`:

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

## Pasos

1. Diseñar UI responsive a distintos tamaños de pantalla móvil.
2. Mantener lógica de negocio fuera de widgets. No forzar bloc/cubit ni
   provider: detectar cuál ya usa el repo (dependencias en `pubspec.yaml`,
   patrones existentes en `presentation/`) y seguir esa misma solución de
   state management de forma consistente en el feature nuevo.
3. Usar `Spacer` para espacio flexible vacío; `Expanded` solo cuando el hijo debe llenar espacio.
4. Preferir propiedades de spacing del `Flex` (`spacing`) sobre `SizedBox` entre hijos.
5. Añadir golden tests para pantallas estables cuando el equipo los use.
6. Ejecutar `fvm flutter test` y `fvm flutter analyze`.

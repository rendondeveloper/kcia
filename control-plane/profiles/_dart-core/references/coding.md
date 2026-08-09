# Estándares de código Dart

## Imports

- Usa imports `package:` con `show` al cruzar límites de capa o feature.
- Ordena imports: SDK, paquetes externos, paquetes internos, relativos.
- Evita imports relativos que salgan de la feature actual.

## Naming

- `PascalCase` para tipos; `camelCase` para miembros y variables.
- Sufijos descriptivos: `*Repository`, `*DataSource`, `*UseCase`, `*Bloc`.
- Nombres de archivos en `snake_case.dart`.

## Complejidad

- Funciones cortas con una responsabilidad clara.
- Evita anidamiento profundo; extrae métodos privados.
- No uses `print` en código de producción; usa logging estructurado.

## Modelos

- Prefiere `freezed` sobre `Equatable` para modelos inmutables.
- Serialización JSON con `json_serializable`; no edites archivos `*.g.dart` ni `*.freezed.dart`.
- Usa clases mapper dedicadas entre modelos de dominio y DTOs.

## Control de flujo

- Siempre usa llaves en `if`, `for` y `while`, incluso en una línea.

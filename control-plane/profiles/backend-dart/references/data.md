# Datos y persistencia

- Usa transacciones para operaciones que deben ser atómicas.
- Migraciones versionadas y reversibles cuando el stack lo permita.
- Connection pooling configurado según entorno.
- Aislamiento con `Isolate` para CPU-bound fuera del event loop.
- Manejo de errores: captura en capa data, traduce a fallos de dominio.

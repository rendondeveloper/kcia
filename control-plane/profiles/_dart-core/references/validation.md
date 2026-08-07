# Validación

## Antes del handoff

- Ejecuta `verify` (test + lint) en el root del paquete afectado.
- Corrige todos los errores de analyzer antes de marcar la tarea completa.
- Si DCM está disponible en el proyecto, ejecútalo sobre archivos modificados.

## Calidad

- Sin secretos ni credenciales en código o fixtures.
- Texto visible al usuario debe pasar por i18n (`AppLocalizations` o equivalente).

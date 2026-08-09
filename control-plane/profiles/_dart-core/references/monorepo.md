# Monorepo

## Melos

- Si existe `melos.yaml`, usa los scripts del workspace (`melos run test:all`, `melos run verify`).
- Cambios en paquetes compartidos pueden requerir validar varios paquetes consumidores.

## Paquetes compartidos

- Mantén APIs estables en paquetes `shared_*` o `core`.
- Versiona breaking changes con changelog por paquete.
- Evita dependencias circulares entre paquetes del workspace.

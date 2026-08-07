# Arquitectura

## Capas

- Respeta clean architecture: presentation → domain → data.
- La lógica de negocio vive en domain; data implementa contratos del dominio.
- Presentation (widgets, blocs, controllers) no accede directamente a APIs externas.

## Features y barrels

- Una carpeta por feature con barrel file (`feature.dart`) que exporta la API pública.
- Cada capa dentro de la feature expone su barrel (`domain.dart`, `data.dart`).
- No importes implementaciones internas desde otras features.

## Dependencias

- Domain no depende de Flutter ni de paquetes de infraestructura.
- Data puede depender de HTTP, DB y serializers; no de widgets.

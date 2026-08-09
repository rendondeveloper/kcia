# API y handlers

- Handlers delgados: validar entrada, delegar a servicios, mapear respuesta.
- Sin lógica de negocio en handlers ni en funciones de ruta.
- DTOs con validación explícita en el borde (query, body, headers).
- Códigos HTTP consistentes y cuerpos de error estructurados.
- Logging estructurado con correlation id por request.

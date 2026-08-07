# Pruebas

## Estructura

- Organiza tests con patrón AAA: Arrange, Act, Assert.
- Un concepto por test; nombres que describan el comportamiento esperado.
- Usa `mocktail` para dobles de prueba; evita mocks manuales frágiles.

## Cobertura

- Toda lógica de negocio nueva requiere tests unitarios.
- Cambios en repositorios o datasources requieren tests de integración cuando aplique.
- No reduzcas cobertura existente sin justificación en el plan.

## Datos de prueba

- Factories o builders para fixtures repetidos.
- Evita dependencias de red o disco en unit tests.

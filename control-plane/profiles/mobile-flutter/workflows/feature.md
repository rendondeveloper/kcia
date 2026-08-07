# Workflow: feature (mobile)

1. Diseñar UI responsive a distintos tamaños de pantalla móvil.
2. Mantener lógica de negocio fuera de widgets; usar bloc/cubit o equivalente.
3. Usar `Spacer` para espacio flexible vacío; `Expanded` solo cuando el hijo debe llenar espacio.
4. Preferir propiedades de spacing del `Flex` (`spacing`) sobre `SizedBox` entre hijos.
5. Añadir golden tests para pantallas estables cuando el equipo los use.
6. Ejecutar `fvm flutter test` y `fvm flutter analyze`.

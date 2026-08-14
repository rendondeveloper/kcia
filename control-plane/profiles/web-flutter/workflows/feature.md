# Workflow: feature (web)

1. Definir breakpoints y comportamiento responsive antes de implementar.
2. Mantener lógica de negocio fuera de widgets. No forzar bloc/cubit ni
   provider: detectar cuál ya usa el repo (dependencias en `pubspec.yaml`,
   patrones existentes en `presentation/`) y seguir esa misma solución de
   state management de forma consistente en el feature nuevo.
3. Validar en Chrome y al menos un segundo navegador del equipo.
4. Medir impacto en tamaño de bundle si añades dependencias pesadas.

# Flutter Web

## Layout

- Diseños responsive con `LayoutBuilder` y breakpoints documentados.
- Prueba en viewports móvil, tablet y desktop.

## Renderer y rendimiento

- Considera CanvasKit vs HTML renderer según necesidades de gráficos y tamaño de bundle.
- Usa deferred loading para features pesadas cuando sea posible.

## Web específico

- Configura URL strategy (path vs hash) según despliegue.
- Meta tags y SEO en `web/index.html` para páginas públicas.
- Accesibilidad web: orden de foco, roles ARIA vía `Semantics`.

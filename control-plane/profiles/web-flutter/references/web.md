# Flutter Web

## Layout

- Responsive layouts with `LayoutBuilder` and documented breakpoints.
- Test on mobile, tablet, and desktop viewports.

## Renderer and performance

- Consider CanvasKit vs HTML renderer depending on graphics needs and bundle size.
- Use deferred loading for heavy features when possible.

## Web-specific

- Configure URL strategy (path vs hash) according to deployment.
- Meta tags and SEO in `web/index.html` for public pages.
- Web accessibility: focus order, ARIA roles via `Semantics`.

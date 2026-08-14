# API and handlers

- Thin handlers: validate input, delegate to services, map the response.
- No business logic in handlers or route functions.
- DTOs with explicit validation at the boundary (query, body, headers).
- Consistent HTTP status codes and structured error bodies.
- Structured logging with a correlation id per request.

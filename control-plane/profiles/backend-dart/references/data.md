# Data and persistence

- Use transactions for operations that must be atomic.
- Versioned and reversible migrations when the stack allows it.
- Connection pooling configured per environment.
- Isolate CPU-bound work with `Isolate`, outside the event loop.
- Error handling: catch in the data layer, translate to domain failures.

# Workflow: refactor (backend)

1. Keep handlers stateless; state lives in injected services.
2. Don't mix schema migrations with logic changes in the same PR.

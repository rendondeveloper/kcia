# Workflow: feature (backend)

1. Define the API contract and domain models.
2. Implement service and repository; handler last.
3. Unit tests for domain; integration tests for critical endpoints.
4. Run `fvm dart test` and `fvm dart analyze`.

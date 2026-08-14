# Testing

## Structure

- Organize tests with the AAA pattern: Arrange, Act, Assert.
- One concept per test; names that describe the expected behavior.
- Use `mocktail` for test doubles; avoid fragile manual mocks.

## Coverage

- All new business logic requires unit tests.
- Changes to repositories or datasources require integration tests when applicable.
- Don't reduce existing coverage without justification in the plan.

## Test data

- Factories or builders for repeated fixtures.
- Avoid network or disk dependencies in unit tests.

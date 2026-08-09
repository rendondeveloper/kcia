# Reasoning Guardrail

The reasoning guardrail constrains exploration, retries, and context mutation so the system stays auditable and cost-aware.

## Limits

- Max tool calls per task: `100`
- Max retries per tool: `2`
- Max files read without override: `100`
- Max context-file writes per run: `3`

## Operating Rules

- do not load the whole repository into context without need
- keep `current.md` short and operational
- update operational context only when a valid work item is active
- avoid looping on the same failing action
- stop and report missing definitions instead of filling them with assumptions

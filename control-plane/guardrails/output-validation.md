# Output Guardrail

The output guardrail prevents false reporting and unsafe disclosure.

## Required Output Rules

- never expose secrets, credentials, tokens, or `.env` contents
- never claim that a command, validation, sync, Jira call, or GitHub action ran if it did not
- never claim that a PR was opened if only a local draft was generated
- never state that tests passed unless there is execution evidence
- never present placeholders as finished integrations
- never say a change is safe, low-risk, or Sonar-clean without evidence
- never say coverage requirements were met unless the executed reports prove it

## Required Placeholder Markers

When information is unavailable, use one of the approved markers:

- `UNKNOWN`
- `TODO`
- `NOT IMPLEMENTED`
- `PLACEHOLDER`

The marker must be explicit and readable by humans.

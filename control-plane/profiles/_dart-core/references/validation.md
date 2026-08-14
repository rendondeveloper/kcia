# Validation

## Before handoff

- Run `verify` (test + lint) at the root of the affected package.
- Fix all analyzer errors before marking the task complete.
- If DCM is available in the project, run it on modified files.

## Quality

- No secrets or credentials in code or fixtures.
- User-visible text must go through i18n (`AppLocalizations` or equivalent).

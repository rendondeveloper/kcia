# Validation

## Before handoff

- Run `verify` (test + lint) at the root of the affected package as a final gate.
- Fix every analyzer finding — errors and warnings alike — before marking the task
  complete. A task is not done while `analyze` output is non-empty.
- If DCM is available in the project, run it on modified files.

## Quality

- No secrets or credentials in code or fixtures.
- User-visible text must go through i18n (`AppLocalizations` or equivalent).

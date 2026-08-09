# Tool-Level Guardrail

Limits what the CLI or the agent may do through Git, shell, Jira, GitHub or local files.

## Git Rules

- Never run `git push --force`, `git reset --hard`, or any merge.
- Read-only git only (`status`, `diff`, `log`): branch and commit are the user's, with
  `kcia branch start` / `kcia commit`.

## Shell Rules

- Never run destructive shell commands such as `rm -rf`.
- Never use `sudo` from the agent workflow.
- Keep file writes inside the intended workspace.
- Do not print or exfiltrate secrets.

## Jira Rules

- Jira integration is optional.
- When disabled, the CLI must not import Jira modules or fail for missing credentials.
- When enabled, the CLI must not claim that a ticket was fetched unless retrieval succeeded.

## GitHub Rules

- GitHub integration is optional.
- Preparing a local PR draft is allowed.
- Opening, closing, approving, or merging PRs must remain disabled unless explicitly enabled.

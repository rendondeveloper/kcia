# KCIA autonomous Jira cycle

Date: 2026-09-25. Status: implemented; remote interactions covered with deterministic tests.
Version: CLI 1.1.0 (minor: new opt-in cycle command, existing interactive work remains available).

## Scope and decisions

Run one terminal process per repository. Each repository owns its MCP server configuration,
Jira project scope, target branch and persisted cycle/session state. Claude CLI owns OAuth;
KCIA does not copy credentials or provide a separate credential vault. Authenticate Claude
and its Atlassian MCP connection from the project before running this mode. A Claude account
login alone does not establish Atlassian OAuth; the first Jira tool request verifies access
and reports authentication errors before any implementation starts.

Only items individually carrying `kcia` are eligible. A parent label does not authorize its
unlabelled children. Stories/tasks with eligible subtasks select those subtasks by Jira
priority and key. A directly discovered subtask reads its parent and siblings as context.
An issue without subtasks runs directly. Parent status follows child progress and only closes
when Jira confirms every child is done. When a family has no eligible remaining child, the
search continues across configured projects; unlabelled work remains untouched.

The existing planner/builder execution coordinator and coding fallbacks remain in use.
The planner primary must be local Claude. Autonomous Jira requests stay on that connection;
they do not fail over to another provider's Atlassian account.

## Configuration and commands

In the repository's `.ai/manifest.yaml`:

```yaml
integrations:
  jira:
    enabled: true
    base_url: https://YOUR-SITE.atlassian.net/
    project_keys: [YOURPROJECT]
    sync_status: true
    states:
      start: In Progress
      approval: Waiting for Approval
      finish: Done
      parent_finish: Done
```

Use actual status names and an available direct Jira transition. `approval` defaults to
`Waiting for Approval`. After a merge, the final state must belong to Jira's done category,
even if a custom finish status is configured. Missing project scope or disabled status
synchronization blocks autonomous mode.

```bash
# Configure the target branch, enable gitflow + PR completion, and keep working.
kcia cycle jira --base develop --loop

# Inspect state without changing configuration or contacting providers.
kcia cycle jira --status

# Resume after Ctrl-C, authentication recovery, a blocker, or a network failure.
kcia cycle jira --loop

# Optional bound: stop after one merged item.
kcia cycle jira --loop --max-items 1
```

`--max-items 0` (default) has no item cap. Without `--loop`, the command processes available
work until it reaches an approval wait or an empty queue, then exits with persisted state.
Polling defaults to 60 seconds; `--wait-seconds N` persists a positive interval. The label is
fixed to `kcia`; the compatibility `--label` option accepts only that value.

The target branch must exist. Configuration writes `.ai/local/git.yaml` with gitflow and
`on_done: pr`, plus `.ai/local/jira-autocycle.json`. The command requires local Claude,
Atlassian MCP enabled, and GitHub CLI. A per-project OS lock prevents two cycle processes
from handling the same repository. The terminal implementation targets POSIX systems.

## Implemented workflow

1. Validate local prerequisites and synchronize the configured base with a fast-forward pull.
2. Search only configured Jira projects; verify labels, completeness, unique issue keys and
   ordering. Fetch selected item, parent and siblings before selecting work.
3. Persist the selected task before Jira status changes. Create a dedicated task branch;
   refuse autonomous work if branch creation or checkout failed.
4. Run understanding, analysis/plan, initial documentation, implementation/validation and final
   documentation. Skip the internal human approval gate. Real blockers, failed validation,
   provider failures and cancellation still pause the workflow with its state preserved.
5. Commit, push and create/reuse one PR for the item, targeting the configured branch.
   Move the item to the configured approval status. Retain the active session and git cycle.
6. Poll GitHub review threads (including every page of thread comments), latest submitted
   reviews and PR conversation comments. New observations restart analysis and implementation
   in the same task/branch. Review text is task data, never authority to run remote actions.
7. The builder records addressed IDs and correction/validation evidence. Missing evidence or
   incomplete validation blocks completion. Push the corrections before resolving threads.
   Resolve only the captured threads whose comments have not changed during repair; never
   delete comments. General review/PR comments are retained as history, since GitHub does not
   expose thread resolution for those. Persist each resolved ID for restart recovery.
8. Wait for approval on the current PR commit, no unresolved threads, successful checks and
   a mergeable state. Use `gh pr merge --match-head-commit` without admin bypass. Confirm the
   remote PR is actually MERGED, including when a merge queue delays completion.
9. Transition the Jira item to done, synchronize the base, close the local session and select
   the next eligible item. Persist successful Jira closure before local cleanup so a failed
   pull does not repeat the closure. Parent completion remains based on all child statuses.

## Components and validation

- `commands/cycle.py`: configuration, process lock and resumable orchestration.
- `integrations/github_reviews.py`: GitHub reads, pagination, approval checks, merge and resolve.
- `integrations/jira_cycle.py`: scoped search, local connection context and verified transitions.
- `commands/jira.py`: automatic selection with the same ticket context as interactive mode.
- `commands/commit.py`: PR checkpoint and approval transition instead of premature closure.
- `commands/work.py`: require the dedicated branch before autonomous waves execute.

Tests cover labelled selection, unlabelled siblings, project isolation, pagination, stale
approval, drafts, failed/pending checks, merge queue acceptance, closed PRs, interrupted
closure, repair evidence, validation failure, resolve-after-push ordering, project locks and
multi-item sequencing. Existing interactive workflow tests remain part of the full suite.
No live Jira transitions, PR creation, merges or comment resolutions are performed by tests.

## Recovery and operating limits

Keep the terminal process running for continuous operation. Ctrl-C preserves local state;
resume with the same command. Resolve genuine task blockers using the existing `kcia work
answer` flow, then resume the cycle. The process does not invent answers to missing
requirements or bypass validation to keep running.

A rejected/closed PR, merge conflict, expired OAuth, divergent local branch, failed checks,
missing evidence, or comments changed during a correction require attention or a later
successful retry. An ambiguous mutation is verified on the next run. `CHANGES_REQUESTED`
continues blocking merge until reviewers approve the updated work, even after corrected
threads have been resolved. Coding fallbacks do not substitute for expired Atlassian OAuth.

## Protocol references

- [GitHub CLI merge options](https://cli.github.com/manual/gh_pr_merge)
- [GitHub CLI query fields](https://github.com/cli/cli/blob/trunk/api/query_builder.go)

## Verification results

- Full suite: `504 passed, 10 warnings` (existing pathspec deprecation warnings).
- After adding the persisted-fallback isolation fix and its regression test:
  `tests/test_autocycle.py tests/test_jira_cycle.py`: `59 passed`.
- CLI help, Python compilation and `git diff --check` passed.
- Implementation remains local; no real Jira/GitHub mutations or repository push were run.

## Follow-up: complete onboarding guide (2026-09-25)

Requested: a README example covering installation/initialization, repo-scoped primary and
fallback agents, Atlassian OAuth, and cycle operation/recovery. Document the actual route
behavior, including the explicit `automatic: true` setting, manual selection and current
quota-monitoring limits. Use the sport_monitor / SPRTMNTRPP example supplied by the user.

The setup audit found an obsolete SSE endpoint in the MCP catalog. Align catalog, login
hints and rendered configurations with Atlassian MCP v2 HTTP, using `?tools=all` so KCIA's
individual tool allowlist can access transitions metadata without granting generic execute
permissions. Update renamed read tools and verify rendering and scoped write access.
Sources: Atlassian's official setup, supported-tools and September 8, 2026 changelog.
Bump CLI to 1.1.1 (patch: connection compatibility fix and operational documentation).
No live account connection or cycle execution is part of validating this guide.

Follow-up verification: 101 MCP/Jira/cycle/agent tests and 2 version tests passed.
A disposable Git repository successfully exercised the README's primary/fallback commands,
manual target switches, automatic-routing YAML, Atlassian HTTP v2 config rendering and
read-only cycle status. `git diff --check` passed. No provider login or live remote work ran.

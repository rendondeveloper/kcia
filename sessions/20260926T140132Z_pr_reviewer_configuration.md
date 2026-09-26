# Configure GitHub reviewers for every PR-based flow

Status: implemented (2026-09-26).
Date: 2026-09-26.
Requested capability: let a user configure one or more GitHub reviewers with a
KCIA command, and apply those reviewers to pull requests created by both normal
task completion and Jira cycle/loop execution.

## 1. Goal

Add repository-level reviewer configuration to KCIA so that a user can run a
command such as:

```bash
kcia branch reviewer add octocat
kcia branch reviewer add my-org/backend-team
```

After that, every KCIA workflow configured to finish by opening a pull request
must request review from those GitHub users or teams. This includes:

1. A normal task completed with `kcia done` when Git flow uses `on_done: pr`.
2. A one-shot Jira cycle that opens a PR and exits while waiting for approval.
3. A continuous Jira cycle started with `kcia cycle jira --loop`.
4. A resumed task where KCIA finds an already-open PR for the task branch.
5. A Jira review-repair pass that pushes corrections and needs fresh approval.

GitHub remains the source of truth for review requests, notifications, review
decisions, branch protection, and merge permission. KCIA will not send email,
approve its own PR, or treat delivery of a GitHub notification as approval.

## 2. Current behavior and integration points

### 2.1 Repository Git-flow configuration

`cli/src/kcia/git/flow.py` defines the immutable `GitFlow` dataclass and reads
and writes `.ai/local/git.yaml`. Current fields are:

- `flow`
- `main_branch`
- `develop_branch`
- `base_branch`
- `on_done`
- runtime-only `configured`

The local file is intentionally gitignored, so reviewer selection is local to
the clone and will not unexpectedly change the settings of other developers.

### 2.2 Normal PR completion

`cli/src/kcia/commands/commit.py::_after_commit` reads the Git-flow setting. For
`gitflow` plus `on_done: pr`, it calls `_finish_gitflow_pr`, which pushes the
task branch and calls `_open_pr`.

`_open_pr` first searches for an open PR with the same head branch and expected
base. If it finds one, it returns that existing PR URL. Otherwise it runs
`gh pr create` and returns the new URL.

This shared create-or-find behavior is the correct place to support resumption.
Reviewer assignment should occur only after KCIA has a verified PR URL.

### 2.3 Jira cycle and loop

`cli/src/kcia/commands/cycle.py::_configure_gitflow_pr` configures the cycle to
use the same repository-level `GitFlow`. The cycle eventually calls the normal
`commit_command`, so initial PR creation already converges on the same
`_finish_gitflow_pr` path as an ordinary task.

After the PR URL is saved, `_complete_saved_done` sets
`awaiting_pr_approval: true`. The loop then uses `_handle_waiting_pr` to inspect
reviews, checks, review threads, and merge state. There must not be a separate
reviewer configuration inside `jira-autocycle.json`; duplicating it would allow
normal and loop behavior to drift.

### 2.4 Review correction inside a loop

When a reviewer requests changes, the cycle creates a review-repair pass. After
the corrections are committed and pushed, `_finish_review` resolves verified
threads and announces that it is waiting for fresh approval. The configured
reviewers should be re-requested at this point so they receive a new GitHub
review request for the corrected head commit.

## 3. Scope

### 3.1 Included

- Repository-local storage of multiple reviewer identifiers.
- Commands to add, remove, and list reviewers.
- GitHub users and organization teams.
- Normal PR completion.
- Jira one-shot cycles and continuous loops.
- Existing PR reuse and retry after remote failure.
- Fresh review requests after automated Jira review correction.
- Documentation, CLI help, migration behavior, and automated tests.

### 3.2 Explicitly excluded

- Direct Gmail or SMTP integration.
- Mapping email addresses to GitHub accounts.
- Automatically creating GitHub users or teams.
- Validating remote account existence while editing local configuration.
- Automatically granting repository access to a reviewer.
- Approving, dismissing reviews, bypassing branch protection, or using
  `--admin` during merge.
- A committed team-wide reviewer policy. `CODEOWNERS` remains the appropriate
  GitHub-native solution for a policy that must travel with the repository.
- Per-ticket, per-profile, or per-branch reviewer overrides in this release.
- Modifying reviewer configuration through `kcia init`; management will use the
  dedicated reviewer commands.

## 4. User experience

### 4.1 Command structure

Add a nested Typer application under the existing `kcia branch` group:

```text
kcia branch reviewer add HANDLE [HANDLE ...]
kcia branch reviewer remove HANDLE [HANDLE ...]
kcia branch reviewer list
```

Examples:

```bash
# Add one GitHub user.
kcia branch reviewer add octocat

# Add several users in one atomic local configuration update.
kcia branch reviewer add alice bob carol

# Add a GitHub organization team.
kcia branch reviewer add acme/mobile-reviewers

# Optional leading @ is accepted and removed before storage.
kcia branch reviewer add @alice

# Show the configured reviewers.
kcia branch reviewer list

# Remove one or several reviewers.
kcia branch reviewer remove alice acme/mobile-reviewers
```

The command group uses singular `reviewer` because sibling KCIA groups use
singular domain names (`branch`, `profile`, `agent`, and `session`).

### 4.2 Add behavior

`add` will:

1. Locate the repository with the same `load_repo()` behavior used by existing
   branch commands.
2. Require an initialized Git configuration. If none exists, exit with a clear
   instruction to run `kcia init` first.
3. Normalize and validate every supplied identifier before writing anything.
4. Merge new values with the existing ordered list.
5. De-duplicate identifiers case-insensitively.
6. Preserve the spelling of the first stored occurrence.
7. Write the configuration once, only after every argument is valid.
8. Exit successfully when a requested reviewer is already configured, making
   repeated setup scripts safe.
9. Explain when the current flow does not create PRs. The setting is preserved
   for a future switch to `on_done: pr`, but it has no effect in merge or
   current-branch mode.

Proposed output:

```text
Added PR reviewers:
  alice
  acme/mobile-reviewers
Already configured:
  bob
GitHub will request these reviewers when KCIA opens or resumes a PR.
```

### 4.3 Remove behavior

`remove` will:

1. Normalize and validate all arguments first.
2. Match configured values case-insensitively.
3. Remove all requested values in one write.
4. Fail without changing the file if any requested reviewer is unknown. This
   prevents a typo from appearing to update configuration successfully.
5. Affect future review requests only. It will not remove a reviewer from a PR
   that is already waiting for review, because the command's purpose is local
   flow configuration rather than unrequested remote mutation.

Proposed output:

```text
Removed PR reviewers:
  alice
Configured PR reviewers:
  acme/mobile-reviewers
```

### 4.4 List behavior

When values exist:

```text
Configured PR reviewers:
  alice
  acme/mobile-reviewers
Used by normal and Jira-cycle PR flows.
```

When the list is empty:

```text
No PR reviewers configured.
Add one with `kcia branch reviewer add <github-handle>`.
```

### 4.5 Existing `kcia branch config` output

Extend the existing configuration summary:

```text
git flow — every task branches off `develop`; kcia done opens a PR to `develop`.
  main:      main
  develop:   develop
  base:      develop
  on done:   pr
  reviewers: alice, acme/mobile-reviewers
```

If no reviewers are configured, print `reviewers: none` only for PR-based Git
flow so the missing setup is visible without adding noise to current-branch
repositories.

## 5. Configuration design

### 5.1 YAML representation

Increment the Git-flow file schema from 1 to 2 and add an ordered list:

```yaml
schema_version: 2
flow: gitflow
main_branch: main
develop_branch: develop
base_branch: develop
on_done: pr
reviewers:
  - alice
  - acme/mobile-reviewers
```

The field belongs to `GitFlow`, not `JiraCycleConfig`, because the same setting
must drive every PR produced by the repository.

### 5.2 In-memory representation

Add this field to the frozen dataclass:

```python
reviewers: tuple[str, ...] = ()
```

A tuple preserves immutability and order. YAML serialization will convert it to
a normal list. An empty tuple preserves all existing behavior.

### 5.3 Identifier normalization

Create one normalization function used by YAML loading and CLI mutations. It
will:

- Require a string.
- Trim surrounding whitespace.
- Remove one optional leading `@`.
- Reject an empty result.
- Reject whitespace, control characters, commas, or more than one `/`.
- Allow either `user` or `organization/team` form.
- Require both sides of a team identifier to be non-empty.
- Avoid overly strict assumptions about every future GitHub-valid character;
  GitHub remains responsible for verifying that the user/team exists and can
  review the repository.
- Compare normalized values with `casefold()` for duplicate detection.

### 5.4 Legacy and malformed configuration

- Schema 1 or older files without `reviewers` load with an empty tuple.
- A YAML `reviewers: null` value is treated as empty for compatibility.
- A valid YAML list is normalized in its existing order.
- Duplicate entries in manually edited YAML collapse to their first occurrence.
- A scalar, mapping, non-string list entry, or syntactically invalid identifier
  is considered a configuration error rather than silently disabling review
  requests. The CLI should show the path and a concise correction message.
- Unknown extra YAML fields retain the current loader behavior and are ignored.

### 5.5 Preservation across configuration rewrites

Several existing operations reconstruct `GitFlow`; every one must retain the
reviewer tuple unless a reviewer command intentionally changes it:

1. `save_base_branch` in `git/flow.py`.
2. Updating only `--on-done` in `commands/init.py`.
3. Full Git-flow reconfiguration in `commands/init.py`.
4. Switching temporarily between PR and merge behavior.
5. `_configure_gitflow_pr` in `commands/cycle.py` when `--base` is supplied.

Prefer `dataclasses.replace(existing, ...)` where it makes the preservation
rule obvious. Where a new `GitFlow` is necessary, pass
`reviewers=existing.reviewers` explicitly. Tests must guard each rewrite point
against future accidental data loss.

## 6. GitHub interaction design

### 6.1 One shared integration function

Add a public helper to `cli/src/kcia/integrations/github_reviews.py`:

```text
request_reviewers(repo, pr_url, reviewers)
```

The helper will:

1. Return immediately for an empty reviewer tuple.
2. Build a `gh pr edit <verified-url>` call.
3. Add one `--add-reviewer <identifier>` pair per configured reviewer.
4. Execute through the integration's existing GitHub command wrapper so errors
   consistently become `GitError`.
5. Use non-JSON output because `gh pr edit` does not need to return data.
6. Print a success message only after GitHub exits successfully.

Example command generated internally:

```bash
gh pr edit https://github.com/acme/app/pull/42 \
  --add-reviewer alice \
  --add-reviewer acme/mobile-reviewers
```

GitHub CLI documents `--add-reviewer` as adding or re-requesting review, which
supports both initial PR setup and fresh review after a correction.

### 6.2 Why reviewer assignment occurs after PR discovery

KCIA must first create or locate the PR and obtain its URL. Assignment by URL:

- Works for a newly created PR.
- Works when the previous process crashed after creating the PR.
- Works when `kcia done` is rerun and `_open_pr` finds the existing PR.
- Avoids relying on the currently checked-out branch after remote creation.
- Gives GitHub a precise repository and PR target.

### 6.3 Normal completion sequence

The required order is:

```text
validate local work
  -> commit
  -> push task branch
  -> find existing PR or create new PR
  -> verify and persist PR URL in done_checkpoint
  -> request configured reviewers
  -> mark git_finished
  -> continue Jira transition or close normal task
```

Persisting the URL before reviewer assignment is important. If GitHub rejects a
reviewer, `kcia cycle jira --status` can still display the PR and a retry can
operate on the same PR without recreating it.

To obtain this ordering, separate “open/find PR” from “request reviewers” in the
saved completion path. `_complete_saved_done` should save `pull_request_url`
immediately after `_after_commit` returns it, then call the integration helper,
then set `git_finished: true`.

For the less common completion path without an active session, the caller must
request reviewers after `_after_commit` returns the URL and before closing the
local branch cycle.

### 6.4 Normal flow behavior matrix

| Git mode | `on_done` | Reviewers | Result |
|---|---|---:|---|
| `gitflow` | `pr` | empty | Current PR behavior; no extra GitHub call. |
| `gitflow` | `pr` | configured | Create/find PR, then request reviewers. |
| `gitflow` | `merge` | any | Merge behavior; never request reviewers. |
| `current-branch` | n/a | any | Push behavior; never request reviewers. |

### 6.5 Jira loop initial PR

The Jira cycle must not add a second initial-reviewer implementation. It calls
the same saved completion path, so the reviewer request must succeed before the
checkpoint enters `awaiting_pr_approval` and before the cycle reports that it is
waiting for approval.

The Jira “Waiting for Approval” transition should remain after successful
reviewer assignment. If reviewer assignment fails, Jira must not falsely report
that a human has been asked to review when the request did not complete.

### 6.6 Jira review repair and fresh approval

In `_finish_review`, use this order:

1. Confirm the correction produced a new local and remote head commit.
2. Resolve only verified review threads, preserving the current safety checks.
3. Record handled observations.
4. Request all currently configured reviewers again.
5. Only after the request succeeds, set the repair phase to `complete`.
6. Print that corrections were pushed and fresh approval was requested.

If the request fails, keep the repair in its resumable `pushed` phase. On the
next loop iteration `_finish_review` will skip already persisted thread
resolutions and retry the reviewer request.

### 6.7 Changes made while a PR is already waiting

Configuration changes are not retroactively pushed to a PR that is already in
the passive `awaiting_pr_approval` state. This prevents every polling iteration
from re-requesting reviews and producing notification noise. New configuration
applies when:

- A PR is first opened or rediscovered during completion.
- A review-repair pass finishes and asks for fresh approval.
- The user explicitly runs `kcia done` for a new completion checkpoint.

A future explicit command such as `kcia branch reviewer sync` may be added if
remote reconciliation of an already-waiting PR becomes necessary; it is outside
this release.

## 7. Failure handling and recovery

### 7.1 Local configuration errors

| Failure | Behavior |
|---|---|
| No Git repository | Reuse the current branch-command error. |
| KCIA Git flow not initialized | Exit 1 and instruct `kcia init`. |
| Empty/invalid reviewer | Exit 1; do not modify YAML. |
| Duplicate add | Exit 0; report already configured. |
| Unknown remove target | Exit 1; do not remove any supplied value. |
| Malformed YAML reviewer field | Exit 1 with config path and expected form. |

### 7.2 GitHub errors

Possible remote failures include:

- Reviewer does not exist.
- Team slug does not exist.
- Reviewer lacks repository access.
- Authenticated GitHub account lacks permission to request review.
- `gh` authentication expired.
- Network or GitHub outage.

For any non-zero `gh pr edit` result:

1. Raise `GitError` with the sanitized stderr/stdout text.
2. Do not set `git_finished`.
3. Do not set `awaiting_pr_approval`.
4. Preserve `pull_request_url`, branch, commits, and the open PR.
5. Do not open another PR on retry.
6. Tell the user to correct configuration/auth/access and resume with the
   existing recovery command (`kcia done`, `kcia work`, or the Jira loop).

### 7.3 Partial remote success

A single `gh pr edit` call will include all reviewers. GitHub may theoretically
apply a subset before returning an error, and a network interruption can make
the final remote state ambiguous. KCIA must not claim exactly-once notification
delivery. Retrying review requests is safe for workflow state, but GitHub may
send another notification if it interprets the retry as a re-request.

No local secrets are introduced. Reviewer handles and PR URLs may be printed in
normal CLI output.

## 8. Data and state transitions

### 8.1 Initial PR success

```text
done_checkpoint.git_finished = false
  -> branch pushed
  -> PR URL created/found
  -> pull_request_url saved
  -> reviewers requested
  -> git_finished = true
  -> normal task closes OR Jira moves to approval state
```

### 8.2 Reviewer request failure

```text
pull_request_url saved
git_finished = false
awaiting_pr_approval absent/false
  -> user fixes handle, access, auth, or network
  -> resume
  -> KCIA finds the same PR
  -> retries reviewer request
```

### 8.3 Loop review repair

```text
awaiting_pr_approval
  -> observations found
  -> repair phase: working
  -> correction committed/pushed
  -> repair phase: pushed
  -> verified threads resolved
  -> configured reviewers re-requested
  -> repair phase: complete
  -> awaiting fresh approval
```

## 9. File-by-file implementation plan

### 9.1 `cli/src/kcia/git/flow.py`

- Change `SCHEMA_VERSION` from 1 to 2.
- Add `reviewers: tuple[str, ...] = ()` to `GitFlow`.
- Add reviewer normalization and list normalization helpers.
- Load missing reviewer data as empty.
- Add the YAML field to both current and legacy load branches.
- Serialize reviewers as a list.
- Preserve reviewers in `save_base_branch`.
- Add focused docstrings explaining that reviewers apply only to PR completion.

### 9.2 `cli/src/kcia/commands/branch.py`

- Import `replace`, `save_flow`, and reviewer normalization helpers.
- Define `reviewer_app = typer.Typer(...)`.
- Register it under `app` as `reviewer`.
- Implement `add`, `remove`, and `list`.
- Add a shared helper that loads an initialized flow and produces consistent
  configuration errors.
- Extend `branch config` output with reviewers.
- Keep all user-facing CLI strings in English, per repository policy.

### 9.3 `cli/src/kcia/commands/init.py`

- Preserve `existing.reviewers` when only `on_done` changes.
- Preserve reviewers during full reconfiguration, including switches between
  PR, merge, and current-branch modes.
- Do not add a new interactive init question; reviewer management remains an
  explicit command after initialization.

### 9.4 `cli/src/kcia/commands/cycle.py`

- Preserve reviewers in `_configure_gitflow_pr` when the cycle writes a base
  branch or forces `on_done: pr`.
- Re-request configured reviewers from `_finish_review` before marking a repair
  complete.
- Ensure exceptions leave the repair/checkpoint resumable.
- Update success text to distinguish “corrections pushed” from “fresh review
  requested.”

### 9.5 `cli/src/kcia/integrations/github_reviews.py`

- Add `request_reviewers` using the existing `gh` wrapper.
- Accept any iterable but materialize it once to preserve order.
- Return without invoking GitHub when empty.
- Use repeated `--add-reviewer` flags.
- Keep all failures as `GitError` so normal and cycle recovery paths handle them.

### 9.6 `cli/src/kcia/commands/commit.py`

- Keep `_open_pr` responsible only for finding/creating and returning the URL.
- Persist the URL before requesting reviewers in `_complete_saved_done`.
- Request reviewers before setting `git_finished`.
- Apply the same helper in completion without a session.
- Preserve existing push-step checkpoint behavior and PR reuse logic.
- Ensure no reviewer call occurs for merge/current-branch modes.

### 9.7 `README.md`

- Update the `.ai/local/git.yaml` example to schema 2 and include reviewers.
- Add command examples and explain user/team syntax.
- Explain that GitHub sends notifications according to each reviewer's settings.
- State the access requirements and that KCIA does not send Gmail directly.
- Explain normal flow, loop flow, existing-PR recovery, and correction re-request.
- Add troubleshooting for invalid reviewers, permissions, and authentication.

### 9.8 `cli/src/kcia/__init__.py`

- Bump `VERSION` from `1.1.1` to `1.2.0` after implementation and validation.
- This is a minor release because it adds backward-compatible commands,
  configuration, and runtime behavior.

### 9.9 Expected test files

- Extend `tests/test_gitflow_config.py` for config migration and preservation.
- Extend `tests/test_done_on_done.py` for normal PR reviewer behavior.
- Extend `tests/test_jira_cycle.py` for loop and repair behavior.
- Add `tests/test_branch_reviewers.py` if command coverage becomes clearer in a
  dedicated file instead of overloading Git-flow configuration tests.
- Update `tests/test_cli_help.py` if nested command discovery is asserted there.
- Update version expectations through the existing `tests/test_version.py` flow.

## 10. Detailed test plan

### 10.1 Configuration unit tests

1. Empty/default `GitFlow` has `reviewers == ()`.
2. Schema 1 config loads with no reviewers.
3. Schema 2 config round-trips one user.
4. Schema 2 config round-trips multiple users and teams in order.
5. Leading `@` is removed.
6. Surrounding whitespace is removed.
7. Duplicates collapse case-insensitively.
8. User and team identifiers remain distinguishable.
9. Empty, whitespace-only, comma-containing, and malformed team identifiers
   are rejected.
10. Non-list YAML is rejected with the file path in the error.
11. Non-string list entries are rejected.

### 10.2 Reviewer command tests

1. `add` writes a reviewer to `.ai/local/git.yaml`.
2. One `add` accepts several reviewers.
3. Repeated `add` is idempotent.
4. Case-insensitive duplicate add preserves the first spelling.
5. `remove` deletes a configured reviewer.
6. One `remove` accepts several reviewers.
7. Unknown remove fails without partially modifying the list.
8. `list` prints ordered reviewers.
9. Empty `list` prints the setup hint.
10. Command outside a Git repository fails consistently.
11. Command before `kcia init` provides an actionable error.
12. Merge/current-branch configuration prints the inactive-setting warning.
13. `kcia branch config` shows reviewer values.

### 10.3 Preservation regression tests

1. `save_base_branch` preserves reviewers.
2. `kcia init --on-done merge` preserves reviewers.
3. Switching back to `--on-done pr` preserves reviewers.
4. Full `kcia init` reconfiguration preserves reviewers.
5. `kcia cycle jira --base ...` preserves reviewers.
6. No existing reviewer configuration remains an empty list after rewrites.

### 10.4 Normal PR tests

1. New PR plus empty list never calls `gh pr edit`.
2. New PR plus one reviewer calls `gh pr edit URL --add-reviewer user`.
3. Multiple reviewers produce repeated flags in configured order.
4. Team reviewer is passed unchanged.
5. Existing PR is reused and receives configured reviewers.
6. Reviewer request happens after PR URL discovery.
7. Reviewer failure returns exit 1 and preserves the session/checkpoint.
8. Reviewer failure preserves the PR URL in the checkpoint.
9. Resume finds the existing PR and retries reviewer assignment.
10. Successful retry closes the normal task without a duplicate PR.
11. Merge mode never requests reviewers.
12. Current-branch mode never requests reviewers.
13. No-session completion still requests reviewers for a PR.

### 10.5 Jira cycle and loop tests

1. Initial autocycle PR requests configured reviewers.
2. Reviewer success precedes Jira's approval transition.
3. Reviewer failure prevents `awaiting_pr_approval`.
4. Reviewer failure does not transition Jira to approval.
5. Resume retries the existing PR rather than opening another.
6. Successful retry transitions Jira once and waits for approval.
7. A one-shot cycle and `--loop` use the same behavior.
8. Polling an unchanged waiting PR does not repeatedly request reviewers.
9. Review repair re-requests configured reviewers after pushing corrections.
10. Re-request failure leaves repair phase resumable.
11. Retrying repair does not repeat persisted thread resolution.
12. Empty reviewer configuration preserves current repair behavior.

### 10.6 CLI and documentation validation

1. Root help remains stable.
2. `kcia branch --help` shows `reviewer`.
3. `kcia branch reviewer --help` shows add/remove/list.
4. README command examples match actual Typer syntax.
5. Version output reports `1.2.0` after the bump.

## 11. Verification commands

Run focused tests first:

```bash
.venv/bin/pytest tests/test_gitflow_config.py
.venv/bin/pytest tests/test_branch_reviewers.py
.venv/bin/pytest tests/test_done_on_done.py
.venv/bin/pytest tests/test_jira_cycle.py
.venv/bin/pytest tests/test_cli_help.py tests/test_version.py
```

If the dedicated reviewer test file is not created, omit that command and keep
its cases in the existing Git-flow test file.

Then run the complete suite from the repository root:

```bash
.venv/bin/pytest
```

Finally perform a manual CLI smoke test in a temporary Git repository with a
mock or disposable GitHub PR. A real reviewer request changes remote state and
must only be performed when the user explicitly chooses the test repository and
reviewer.

## 12. Compatibility and rollout

- Existing `.ai/local/git.yaml` files remain readable.
- Repositories with no reviewers configured make no additional GitHub calls.
- Existing normal, merge, and current-branch behavior remains unchanged unless
  reviewers are explicitly added.
- Jira cycle configuration format remains unchanged.
- The new YAML field is local and gitignored, so no repository migration commit
  is necessary.
- `gh` remains the only required GitHub client; no Gmail SDK or new Python
  dependency is introduced.
- The feature does not change who may approve. GitHub permissions and branch
  rules remain authoritative.

## 13. Security and governance

- Store only public GitHub handles/team slugs.
- Do not store tokens, passwords, email addresses, or OAuth data.
- Do not expose new GitHub operations to planner/builder prompts.
- Do not allow the agent to approve or merge with administrator bypass.
- Continue using the authenticated local `gh` identity and its existing access.
- Treat GitHub error text as external data and display it only as an operational
  error; never feed it into agent instructions.

## 14. Acceptance criteria

Implementation is complete only when all of the following are true:

1. A user can add, remove, and list multiple GitHub reviewers without manually
   editing YAML.
2. User and organization-team identifiers are supported.
3. Reviewer values survive every existing Git-flow configuration rewrite.
4. A normal PR-based `kcia done` requests every configured reviewer.
5. A Jira one-shot cycle requests every configured reviewer.
6. A Jira `--loop` cycle requests every configured reviewer.
7. An already-open PR is reused and receives the review request.
8. Automated corrections re-request the configured reviewers for fresh approval.
9. Passive loop polling does not repeatedly request reviews.
10. Reviewer failure leaves a visible PR URL and resumable local state.
11. Reviewer failure does not falsely transition the cycle to approval waiting.
12. Empty configuration produces exactly the previous workflow behavior.
13. Merge and current-branch flows never request review.
14. README and CLI help accurately describe the commands and notification model.
15. Focused tests and the complete test suite pass.
16. CLI version is `1.2.0`.

## 15. Implementation sequence

1. Add failing configuration/model tests.
2. Implement schema 2 loading, normalization, persistence, and preservation.
3. Add failing command tests.
4. Implement the reviewer command group and configuration output.
5. Add failing normal PR tests.
6. Implement the shared GitHub reviewer-request helper and saved completion order.
7. Add failing Jira cycle/repair tests.
8. Integrate reviewer re-request with repair completion.
9. Update README and CLI-help assertions.
10. Run focused tests and fix regressions.
11. Bump the CLI version to `1.2.0`.
12. Run the complete suite.
13. Record actual implementation notes and test results in this same plan file.
14. Stage and commit only files named by this plan; leave unrelated untracked
    files untouched.
15. Do not push. Repository policy requires the user's literal `ok` after the
    implementation is complete and committed before any remote push.

## 17. Implementation notes

- Schema 2 `reviewers` on `GitFlow`; `kcia branch reviewer add|remove|list`; shared
  `github_reviews.request_reviewers`; ordering in `_complete_saved_done` (URL saved,
  reviewers requested, then `git_finished`); re-request after review repair in
  `_finish_review`; preservation across `init`, `save_base_branch`, and
  `_configure_gitflow_pr`.
- Version bump: **1.2.0** (minor — backward-compatible commands, config, and runtime).
- Tests: `519 passed` full suite from repo root after `pip install -e "./cli[dev]"`.

## 16. Open questions

None blocking. The plan intentionally chooses repository-level reviewers shared
by normal and Jira-loop PR flows, which directly matches the requested scope.

The following are documented future choices rather than blockers:

- Whether to add committed team-wide reviewer policy through `CODEOWNERS`.
- Whether to add per-ticket reviewer overrides.
- Whether to add an explicit command to synchronize new configuration into a PR
  that was already waiting before the local setting changed.

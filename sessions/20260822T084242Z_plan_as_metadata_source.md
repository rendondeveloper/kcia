# Plan: Canonical Plan as the source of truth for branch/commit/title/description/session metadata

## 1. Request summary

Today, the raw string the user types into `kcia work "<text>"` (or the raw ticket key for
`kcia work IP-116`) is reused verbatim, with only mechanical slugification, to build:

- the git branch name,
- the commit subject (plan commit and code commit),
- the task title,
- the session-history title,
- the commit-type inference (`feat`/`fix`/`docs`).

The request is to make the **canonical plan** (`.ai/context/plan.md`, produced by the
`analysis` wave after `understanding`) the single semantic source for all of the above,
so that branch/commit/title/description are consistent with what the planning waves
actually decided to do, not with the user's original wording. Real repository state
(git diff, commit SHA, test results) stays the source of truth for anything that is
*verifiable after the fact* rather than *decided during planning*.

## 2. Current architecture (as found)

### 2.1 Where the prompt is captured and reused

- `cli/src/kcia/commands/work.py` (`WorkGroup.resolve_command`, `_create_task`) captures
  the raw CLI text and calls `Session.create(repo, text=text, ...)`.
- `cli/src/kcia/waves/session.py:64-95` `classify_input()` decides `"ticket"` vs `"prompt"`
  mode by regex only (Jira key pattern), no LLM involved.
- `cli/src/kcia/waves/session.py:128-168` `Session.create()` stores:
  - `task["prompt"] = text` (prompt mode only, else `None`)
  - `task["title"] = title or text` — **`title` is never passed by the CLI, so today
    `task["title"]` is always just the raw prompt/ticket-key string.**
- This dict is persisted to `.ai/local/session.json`.

### 2.2 Waves (`control-plane/waves/waves.yaml`, run via `cli/src/kcia/waves/runner.py`)

Order: `understanding` → `analysis` → `documentation-init` → `implementation` →
`documentation-final`.

- `understanding` (planner, no edits) writes `.ai/context/task.md`.
- `analysis` (planner, no edits) writes `.ai/context/plan.md`. This is the wave we make
  canonical.
- `documentation-init` (planner, edit scope `.ai/**`) writes `.ai/context/current.md`,
  `.ai/context/decisions.md`.
- `implementation` (builder, edit scope `**`, **requires human approval, `approval_shows:
  plan.md`**) — this is the human plan-approval gate; it already treats `plan.md` as
  authoritative for the builder.
- `documentation-final` (builder, edit scope `.ai/**`) writes `.ai/context/milestones.md`.

`.ai/context/plan.md` today is rendered from `control-plane/templates/plan.md.j2`, a
trivial `# Plan\n\n{{ content }}` wrapper around whatever prose the `analysis` wave's
agent produced. The only structure currently imposed on that prose is an optional fenced
YAML block for `execution.profiles[]` (parsed by `cli/src/kcia/waves/plan_execution.py`,
used for multi-profile dependency checklists) — nothing about title/type/ticket/affected
files/flows is structured today.

### 2.3 Branch naming

- Automatic (`kcia work`): `cli/src/kcia/git/autobranch.py:79-151` `ensure_task_branch()`.
  Line 126: `subject = (task.get("title") or task.get("prompt") or "").strip()`, then
  `branch_name(infer_commit_type(subject, ["src"]), ticket=ticket, subject=subject or
  ticket or "task")`.
- Manual: `cli/src/kcia/commands/branch.py:143-216` `branch_start()` — same fallback
  chain (`subject or title`, where `title` is `task.get("title")`).
- Slug logic lives in `cli/src/kcia/git/flow.py`:
  - `slugify()` (224-227): tokenizes to `[a-z0-9]+`, keeps first `MAX_SLUG_WORDS=6`
    words, caps at `MAX_SLUG_LENGTH=48` chars.
  - `branch_name()` (230-236): `{prefix}/{TICKET-}{slug}`.
  - `infer_commit_type()` (`git/commit.py:84-95`): keyword-hint scan of the raw subject
    text (`_FIX_HINTS`, `_DOCS_HINTS`) plus whether code paths exist.

This is the main "violation": branch names are 6-word slugs of the **raw prompt**, e.g.
`fix/corrige-el-problema-de-que-el-token` — not of a plan-derived objective.

### 2.4 `kcia done` / commit generation

- `cli/src/kcia/commands/commit.py:240-349` `commit_command()`.
- Subject: `resolved_subject = subject or session.task.get("title")` (line 268) — CLI
  positional arg wins, else falls back to the raw-prompt-derived title.
- Type: `--type`/`-t` flag if given, else `infer_commit_type(subject, code_paths)`
  (`git/commit.py:84-95`, keyword scan again). README confirms: type is inferred from
  subject + changed files, and `--type` always wins.
- `plan_commits()` (`git/commit.py:98-154`) splits changed paths into plan paths
  (`.ai/**`, filtered) and code paths (`split_changes()`), builds:
  - plan commit: `docs: {ticket -} plan — {subject}`
  - code commit: `{type}: {ticket -} {subject}`
  via `build_message()` (`git/commit.py:41-55`).
- `_auto_log_session()` (`commit.py:90-122`) logs to session history on every `kcia
  done` with `title=resolved_subject`, **`summary=""` and `decisions=[]` always
  hard-coded empty** — these are only ever populated by a human manually running
  `kcia session log --summary ... --decision ...`.

### 2.5 Session history

- `cli/src/kcia/commands/session.py` `session_log()` — manual command, explicit
  `--title/--summary/--decision/--file/--commit` flags, non-English rejection.
- `cli/src/kcia/history/log.py` `entry_from_git()` builds the `SessionEntry`
  (`id`, `timestamp`, `title`, `summary`, `decisions`, `files`, `commit_sha`, `branch`,
  `task_id`), appended to `.ai/history/sessions.jsonl` (JSONL, intentionally not
  gitignored — durable shared record) and indexed in `.ai/local/history.sqlite3`.
- The auto-log path from `kcia done` is the one that matters in practice, and it never
  gets a real summary or decisions today.

### 2.6 Ticket handling

- `classify_input()` already separates ticket key from prompt text structurally:
  `task["ticket_key"]` vs `task["prompt"]`. This part of the architecture already matches
  what the request asks for — **no change needed to ticket classification**, only to how
  `title`/`subject` are derived once ticket mode is active (today the ticket-mode title
  still falls back to the raw ticket key, e.g. `"IP-116"`, which is not human-readable —
  a plan-derived title fixes this for free).

### 2.7 What already respects "no git in waves"

Confirmed: no wave (including `implementation`) runs git commands. `kcia done` is the
sole place commits are written, after showing the user the exact commit messages/files
and asking for confirmation. This constraint stays untouched by this plan.

## 3. Root cause / where the fix belongs

The prompt leaks into metadata because there is exactly one field, `task["title"]`
(always `title or text`, and `title` is never independently set), that every downstream
consumer (`autobranch.py`, `branch.py`, `commit.py`) treats as "the human-readable
description of the work." Fixing this means:

1. Giving `analysis` wave a **structured plan format** it must emit (still Markdown,
   still human-readable, with one small fenced metadata block — same pattern already
   used for `execution.profiles[]`).
2. Adding a **single parser** (`waves/plan_metadata.py`, new module, sibling to the
   existing `waves/plan_execution.py`) that reads `.ai/context/plan.md` and returns a
   typed `PlanMetadata` object: `title`, `change_type`, `ticket`, `objective`,
   `description`, `affected_files` (modify/create/delete/tests), `current_flow`,
   `modified_flow`, `acceptance_criteria`, `decisions`.
3. Making `autobranch.py`, `branch.py`, `commit.py`, and the `kcia done` auto-log call
   **that one parser** instead of reading `task["title"]`/`task["prompt"]` directly.
4. Falling back to today's behavior (raw title/prompt) when `plan.md` doesn't exist yet
   or has no parseable metadata block — this keeps `kcia branch start` usable before
   planning waves run, and keeps old/pre-existing sessions from breaking.

This is additive to the existing pipeline (`analysis` wave already exists and already
gates `implementation` on human approval of exactly this file), not a rewrite.

## 4. Proposed `.ai/context/plan.md` structure

Keep the file Markdown, human-first, agent-readable. Add one fenced YAML metadata block
at the top (same mechanism already used for `execution.profiles[]`, so
`plan_execution.py`'s existing YAML-fence parsing approach is reused, not duplicated),
followed by the existing free-form prose sections (Current flow / Modified flow /
rationale / etc. — agents already write flow narratives; we just standardize the
headings so the parser can also lift `current_flow`/`modified_flow` as text blocks if
present).

```markdown
# Plan

​```yaml
type: fix                # feat | fix | docs  (same enum as commit --type)
ticket: AUTH-123         # optional; omitted if no ticket
title: Prevent duplicate device token registration
summary: >
  Avoid sending the device-token registration request more than once per app start.
affected_files:
  modify:
    - src/auth/device_token.py
    - src/auth/client.py
  create:
    - tests/auth/test_device_token_dedup.py
  delete: []
acceptance_criteria:
  - App start sends the registration request at most once per unchanged token.
  - Existing tests for token refresh still pass.
​```

## Description
Prose: problem, proposed solution, scope. (existing free-form content — becomes the
commit/PR description body and session summary.)

## Current flow
...

## Modified flow
...
```

Rules:

- The YAML block is **required** for the plan to be usable as a metadata source; if
  absent, tooling falls back to legacy behavior (see §6) and logs (to stderr, not
  silently) that it did so — no hard failure, so this stays compatible with hand-edited
  or older plans.
- `execution.profiles[]` (multi-profile case) and this new metadata block coexist in the
  same file as separate top-level YAML fences, or are merged into one fence with both
  keys — **open question, see §8**.
- `title` in the YAML block is the single source for branch/commit/session title.
  `summary`/`## Description` together form the commit/PR/session description body.
- `affected_files` is the **planned** file list (traceable to the plan). It is never
  used to decide what actually gets committed — `git status`/`plan_commits()` already
  does that from real diff state, and that does not change. Its purpose is auditability
  (§7) and optional drift warnings.
- `decisions` is not a YAML key: it is read separately from `.ai/context/decisions.md`
  (per resolved question 2 in §8) and merged into `PlanMetadata.decisions` at load time.

## 5. Derivation model (single source, multiple consumers)

New module `cli/src/kcia/waves/plan_metadata.py`:

```python
@dataclass
class PlanMetadata:
    type: str | None               # feat | fix | docs
    ticket: str | None
    title: str | None
    summary: str | None            # YAML `summary` + "## Description" prose, combined
    affected_files: dict[str, list[str]]  # modify/create/delete/tests
    acceptance_criteria: list[str]
    current_flow: str | None
    modified_flow: str | None
    decisions: list[str]           # read from .ai/context/decisions.md

def load(plan_path: Path) -> PlanMetadata | None: ...
```

`load()` returns `None` (not a partially-filled object) if `plan.md` doesn't exist or has
no YAML fence — this makes "no plan yet" an explicit, checkable state rather than an
object full of `None`s that callers might silently accept.

Consumers change from "read `task.title`/`task.prompt`" to "try `PlanMetadata`, else
fall back to `task.title`/`task.prompt`":

| Consumer | Today | After |
|---|---|---|
| `git/autobranch.py:126` | `task.title or task.prompt` | `plan.title or task.title or task.prompt` |
| `commands/branch.py:170` | `subject or title` | `subject or plan.title or title` |
| `commands/commit.py:268` | `subject or task.title` | `subject or plan.title or task.title` |
| `git/commit.py` `infer_commit_type` | keyword-scan of subject | `plan.type`, only falling back to keyword-scan if plan has none; `--type` still overrides both (see §5.1) |
| `commit.py` `_auto_log_session` | `title=resolved_subject`, `summary=""`, `decisions=[]` | `title=plan.title or resolved_subject`, `summary=plan.summary or ""`, `decisions=plan.decisions or []` (`plan.decisions` sourced from `.ai/context/decisions.md`, per resolved question 2) |
| commit/plan subject text | raw prompt-derived | `plan.title` (+ ticket) |

`--type` precedence (explicit ask in the request): CLI `--type` > `plan.type` >
keyword inference from subject (today's `infer_commit_type`, kept as a tested backup
fallback for plans without a `type`, or for `kcia branch start`/`kcia done` runs with no
plan at all — confirmed in resolved question 3). This is a strict override chain, not a
merge — document it in the `--type` help string and README.

Note the request's own README addendum agrees with this and slightly refines "plan is
the only source": affected files *planned* come from the plan; affected files *actually
touched* stay sourced from git (`plan_commits()`/`split_changes()` — unchanged). This
plan follows that refinement, not a literal "plan is the only source of everything."

### 5.1 Ticket precedence (unchanged input, now consistently applied)

`ticket_key` continues to come from `classify_input()`/`session.task["ticket_key"]` or
an explicit `--ticket` flag — this was already structured data, untouched here. The only
change: `plan.ticket` (echoed by the analysis wave from `ticket_key` it was given, or
`.ai/context/ticket.md`) becomes an additional corroborating value; if it disagrees with
`session.task["ticket_key"]`, the session value wins (it's the one CLI/Jira integration
actually verified) and a one-line warning is printed — this should not silently happen.

## 6. Compatibility / fallback strategy

- Plans that predate this change, or that a human wrote by hand without the YAML fence,
  keep working exactly as today: `PlanMetadata.load()` returns `None`, every consumer's
  fallback chain lands on `task.title`/`task.prompt`/keyword inference — **zero behavior
  change** for repos that haven't adopted the new plan shape yet.
- `kcia branch start` before any wave has run (no `plan.md` at all) is unaffected — same
  fallback.
- No migration script needed for existing `.ai/context/plan.md` files; they simply don't
  get the new benefit until the `analysis` wave is re-run or a human adds the fence by
  hand.
- The `analysis` wave's prompt template (`control-plane/waves/prompts/analysis.md.j2`)
  needs to be updated to *instruct* the planner agent to emit this YAML block — this is
  a control-plane **data** change (per CLAUDE.md's "control-plane is data, not code"),
  not a Python change.

## 7. Deviation tracking (plan vs. real implementation)

Per the request: if `implementation` touches a file not listed in `affected_files`, that
must be traceable, not silently absorbed into "the plan said so."

Proposed minimal mechanism, reusing existing artifacts (no new file):

- `documentation-final` wave (already writes `.ai/context/milestones.md`, already runs
  after `implementation`) gets an additional instruction in its prompt template: if the
  actual changed files (visible to it via git status, which builder waves can already
  read-only inspect) differ from `plan.md`'s `affected_files`, append a `## Deviations`
  section to `milestones.md` listing the extra/missing files and a one-line reason.
- This is a **prompt-template change**, not new Python plumbing — `documentation-final`
  already has edit scope `.ai/**` and already runs post-implementation.
- `plan.md`'s `affected_files` is never silently rewritten by tooling — only a human or
  the `documentation-final` wave (writing to `milestones.md`, not `plan.md`) records the
  discrepancy. This satisfies "don't let the plan silently drift" without inventing a new
  state machine.
- Session history's `files` field already comes from real git state
  (`_files_from_planned(commits)` in `commit.py`), so session history already correctly
  reflects reality, not the plan — no change needed there, just confirmation it's already
  correct per the request's own "real results = source of truth for files" principle.

## 8. Open questions — RESOLVED

1. **YAML fence merging** — Confirmed: keep the plan-metadata YAML fence separate from
   the existing `execution.profiles[]` fence. Two small, independent parsers
   (`plan_execution.py` unchanged, new `plan_metadata.py` added).

2. **`decisions` source** — Confirmed: `plan_metadata.load()` reads
   `.ai/context/decisions.md` (already written by `documentation-init`) for the
   `decisions` field, rather than duplicating a `## Decisions` heading inside `plan.md`.

3. **Keyword inference (`_FIX_HINTS`/`_DOCS_HINTS`) as fallback** — Confirmed, with an
   explicit backup requirement: keep `infer_commit_type()`'s keyword scan as the
   last-resort fallback (used only when there is no plan / no `change_type` in the plan),
   so `kcia branch start`/`kcia done` keep working without having gone through waves.
   This fallback path must remain covered by tests (see §10) so it doesn't silently rot
   once the plan-derived path is the common case.

4. **YAML key names** — Use `type` (not `change_type`) and `summary` (not `objective`)
   in the plan metadata block, to match the vocabulary already used by `kcia done`'s
   `--type` flag and by session-log's `--summary` field. Final key set:
   `type`, `ticket`, `title`, `summary`, `affected_files` (`modify`/`create`/`delete`/
   `tests`), `acceptance_criteria`.

## 9. Concrete file changes (once confirmed)

- `control-plane/templates/plan.md.j2` — no change needed (still `# Plan\n\n{{content}}`;
  the YAML fence is part of `{{ content }}`, produced by the agent per the updated
  prompt instructions).
- `control-plane/waves/prompts/analysis.md.j2` — add instructions + example for the new
  YAML metadata block and `## Description`/`## Current flow`/`## Modified flow` headings.
- `control-plane/waves/prompts/documentation-final.md.j2` — add deviation-check
  instructions (§7).
- `cli/src/kcia/waves/plan_metadata.py` (new) — `PlanMetadata` dataclass + `load()`.
- `cli/src/kcia/git/autobranch.py` — use `plan_metadata.load()` before falling back to
  `task.title`/`task.prompt`.
- `cli/src/kcia/commands/branch.py` — same fallback change in `branch_start()`.
- `cli/src/kcia/commands/commit.py` — `resolved_subject`, `_auto_log_session()` updated
  to prefer `plan.title`/`plan.description`/`plan.decisions`.
- `cli/src/kcia/git/commit.py` — `infer_commit_type()` call sites gain a `plan_type`
  parameter checked before the keyword scan; `--type` still overrides everything.
- `cli/src/kcia/waves/session.py` — no change to `classify_input()`/ticket handling;
  only doc-comment noting `title` is now a fallback, not the primary source, for CLI
  consumers.
- README.md — update the `kcia done`/`kcia branch start`/session-history sections to
  document the new plan-metadata precedence chain and the `--type` override rule.

## 10. Tests to add/modify

- `tests/waves/test_plan_metadata.py` (new): parses a fixture `plan.md` with the YAML
  fence into `PlanMetadata`; asserts `None` return for a plan with no fence; asserts
  graceful handling of malformed YAML (falls back, doesn't crash).
- `cli/tests` (find current `test_autobranch.py`/equivalent, per repo convention) — add
  case: branch name comes from `plan.title`+`ticket` when a plan with metadata exists,
  and unchanged legacy behavior when it doesn't.
- Existing `commit.py`-related tests — add case: commit subject/type sourced from
  `plan.md` when present; `--type` overrides `plan.change_type`; `_auto_log_session`
  picks up `plan.description`/decisions instead of empty strings.
- `tests/test_task_answer.py` / session tests — verify `classify_input`/ticket handling
  unaffected (regression guard, since this plan explicitly does not change that code).

## 11. Risks

- Prompt-template changes (`analysis.md.j2`) are the least testable part of this change —
  they rely on the planner LLM actually emitting well-formed YAML. Mitigate with the
  fallback-to-legacy behavior (§6) so a malformed emission degrades gracefully instead of
  breaking `kcia done`.
- Two files (`decisions.md` + `plan.md`) as sources for one `PlanMetadata` object (if
  open question 2 is resolved that way) adds a small amount of coupling between waves 2
  and 3 — acceptable since `documentation-init` already requires `analysis` to have run.
- `README.md` example commit messages (`docs: IP-116 - plan — add the commit flow`)
  should stay valid after this change; the format string in `build_message()` is
  unchanged, only the *source* of `subject`/`type` changes.

## 12. Version bump

**Minor** bump: `0.14.0` → `0.15.0` (new capability — plan-derived metadata precedence —
fully backward compatible via fallback; no existing command signature or output format
breaks for repos not using the new plan shape). Note: `VERSION` was already at `0.14.0`
uncommitted in the working tree from unrelated in-progress work (an opencode provider
addition) present before this task started; this plan's change continues from that value
rather than resetting it.

## 13. Status — IMPLEMENTED

Implemented as planned, with no deviation from §4/§5/§9 beyond the trims noted below.

Files changed:
- `cli/src/kcia/waves/plan_metadata.py` (new) — `PlanMetadata` + `parse_plan_metadata()` +
  `load()`, decisions merged from `.ai/context/decisions.md`.
- `cli/src/kcia/git/autobranch.py`, `cli/src/kcia/commands/branch.py`,
  `cli/src/kcia/commands/commit.py`, `cli/src/kcia/git/commit.py` (`plan_commits` gained a
  `plan_type` parameter) — wired to `plan_metadata.load()` with fallback to
  `task.title`/`task.prompt`/keyword inference, per the precedence table in §5.
- `cli/src/kcia/commands/commit.py` `_auto_log_session()` — now takes `summary`/`decisions`
  from the plan instead of hardcoded empties; the English-only check now covers
  summary/decisions too, not just title.
- `control-plane/waves/prompts/analysis.md.j2` — analysis wave now emits the metadata
  block; kept intentionally terse to fit the token budget (see below).
- `control-plane/waves/prompts/documentation-final.md.j2` — deviation-check instruction
  (§7), also kept terse.
- `README.md` §7/§"Starting the branch by hand" — documented the precedence chain.
- `cli/src/kcia/__init__.py` — VERSION bump.
- Tests: `tests/test_plan_metadata.py` (new, unit coverage for the parser/loader),
  `tests/test_gitflow_config.py` (branch name from plan title/ticket, overriding a raw
  Spanish prompt), `tests/test_git_commit.py` (`plan_type` precedence vs `--type` vs
  inference; CLI-level test that `kcia done` pulls subject/type/summary/decisions from
  `plan.md`/`decisions.md`), `tests/test_optimization_budget.py` (budget ceiling raised
  13120 → 13420 with a documented reason, since the new prompt content is genuinely
  load-bearing and further trimming would have made it useless — matches the file's
  existing convention of raising the ceiling with an inline justification per change).

Deviation from the initial file-change list in §9: `cli/src/kcia/waves/session.py` needed
no change at all (not even a comment) — `classify_input()`/ticket handling were already
correct as identified in §2.5/analysis, and adding an unnecessary comment there would not
have served any reader.

Verification: `.venv/bin/pytest -q` — 310 passed. The only 3 failing tests
(`test_providers_opencode.py` x2, `test_version.py`) are pre-existing failures from
unrelated, already-uncommitted work-in-progress in this tree (confirmed via `git stash`
before starting this implementation) and a stale editable install; none are caused by, or
related to, this change.

**Not committed.** Per the repo's git-push/commit confirmation rule, staging and
committing (only the files listed above — nothing from the unrelated opencode
work-in-progress sitting in the same working tree) is pending explicit user instruction.

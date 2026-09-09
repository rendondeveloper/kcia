# Open questions as a real gate, and retry collapsed to two role commands

## Analysis

Three requests, one underlying cause. Taking them in order.

### 1. "Do not let `approve` proceed if the questions were not answered"

**There is no concept of an open question in kcia today.** Grepping the whole
tree, "open question" appears in exactly three places, none of them structural:

- `control-plane/waves/prompts/understanding.md.j2:9` — the wave is *told* to
  "produce a concise problem statement, affected areas, and open questions";
- `_blocked.md.j2:4` — prose telling the agent an answerable question is not a
  blocker;
- `waves/blocked.py:26` — a legacy regex for `Open questions (blocking):`,
  which only fires as a fallback shape of `BLOCKED:`.

So the understanding wave is asked to produce questions, writes them into
`.ai/context/task.md` as free prose, and **nothing ever reads them again**.
`kcia work` proceeds straight into `analysis`, the planner writes a plan on top
of its own unanswered assumptions, and `kcia work approve` (`commands/work.py:837`)
checks only `wave.requires_approval` and `session.is_approved(...)` — never
whether anything is outstanding.

That is the hole to close, and it explains a second thing: the plan reviewed in
`sessions/20260909T144207Z_close_the_builder_escape_hatch.md` opened with a
section titled *"Decisions on the understanding wave's open questions"* — the
planner had to answer its own questions inline, because kcia offered no
mechanism to route them to a human. The builder then re-asked two of them. The
whole failure chain starts here.

The only structured stop that exists is `BLOCKED:`, which is all-or-nothing:
the wave produces *one line and nothing else*, discarding its work. A question
that does not prevent finishing the wave has no way to be recorded.

### 2. "Answering should relaunch the flow automatically"

`kcia work answer` (`commands/work.py:739`) currently:

1. appends the text to `session.injections` (surfaced to the next prompt as
   `## Injected context`, `waves/prompts.py:196`),
2. looks for a **blocked** wave, and
3. retries **only that one wave** — or, if nothing is blocked, prints
   "injection recorded for the next wave that runs" and stops.

Two consequences. If the questions were merely *recorded* (not blocking), the
answer lands as loose context for whatever runs next, and the wave that asked is
never re-run — so `task.md` keeps its unanswered questions forever. And even in
the blocking case, only one wave re-runs: answering a question from
`understanding` does not re-run `analysis`, so the plan is never rebuilt on the
answer.

**Which flow should relaunch automatically** — the question asked directly: the
answer belongs to the wave that asked it, so re-running must start *there* and
carry forward through the rest of that role's waves. For a question from
`understanding` that means `understanding → analysis → documentation-init`,
which is exactly the planner's wave list. It is the same operation as request 3.
One mechanism serves both, and answering should trigger it automatically —
there is no case where a user answers a question and does *not* want the plan
rebuilt.

### 3. "Collapse retry to `retry planning` / `retry builder`"

`kcia work retry [wave_id]` (`commands/work.py:900`) retries a single wave;
`retry_wave` (`waves/runner.py:145`) resets that one wave's status to `pending`
and re-runs it. To rebuild a plan today the user must know the wave ids and run
three commands in the right order.

The grouping the user wants **already exists as data** —
`control-plane/agents/roles.yaml`:

```yaml
- id: planner
  waves: [understanding, analysis, documentation-init]
- id: builder
  waves: [implementation, documentation-final]
```

So `kcia work retry planner` and `kcia work retry builder` are not new concepts;
they are the existing role definition applied to retry. Consistent with
`kcia agent set planner|builder`, and no new list to maintain.

Two ordering rules are not optional:

- **Retrying the planner invalidates the approval.** The approval gate exists
  because a human read *that* plan (`requires_approval: true`,
  `approval_shows: plan.md`). A rebuilt plan has not been approved, and the
  builder waves that ran against the old one are stale. So `retry planner` must
  clear the approval and reset the builder waves to `pending`.
- **Retrying the builder must not re-run the planner.** It re-runs
  `implementation → documentation-final` on the existing approved plan, keeping
  the approval — which is the user's "reinicie el ciclo de `kcia work approve`".

On the prompt: the task statement is already stored in the session
(`_task_statement`, `waves/prompts.py:288`) and re-composed on every run, so
neither retry needs the original text re-typed. That part already works; the
plan only needs to not break it.

## Open questions

**Q1 — How are questions expressed?** A parsed `## Open questions` section in
`.ai/context/task.md` (one question per list item), or a separate machine-owned
file the wave writes?
Proposal: parse a `## Open questions` section out of `task.md`, mirroring how
`plan_metadata.py` parses the plan's YAML block out of `plan.md`. One artifact
per wave stays true to `writes:` in `waves.yaml`, and the human reads and edits
one file.
> Answer: Confirmed — proposal accepted. `## Open questions` in `task.md`, one `> Answer:` line per question.

**Q2 — What counts as answered?** The user answers via `kcia work answer`, or
by editing `task.md` directly (explicitly requested). Direct editing means kcia
must detect an answer in the file.
Proposal: each question carries an `> Answer:` line beneath it (the shape the
`sessions/` plan files already use in this repo). A question is answered when
that line is non-empty. `kcia work answer` fills in the *first* unanswered one,
or all of them from `--file`. Both routes converge on the same file, so there is
one source of truth.
> Answer: Confirmed — proposal accepted. Non-empty `> Answer:` means answered; `kcia work answer` and manual editing write to the same file.

**Q3 — Is the gate hard or soft?** Should `approve` refuse outright, or warn
and allow `--force`?
Proposal: refuse, with `--force` available and recorded in the approval note.
The user asked for a gate; an escape valve that leaves a trace is still a gate,
and one unanswerable question should not strand a task.
> Answer: Confirmed — proposal accepted. `approve` refuses; `--force` allowed and recorded in the approval note.

**Q4 — Should `kcia work` stop at the questions, or run the whole planner
first?** Stopping at `understanding` gets the human in earlier; running through
`documentation-init` gives them a draft plan to react to.
Proposal: run the planner through, then stop before the approval gate and
report the questions. The plan-with-assumptions is more useful to answer against
than bare questions, and it costs nothing extra — the planner waves run either
way.
> Answer: Confirmed — proposal accepted. Run all three planner waves, then stop before the approval gate and report the questions.

**Q5 — Does the planner get told its questions were answered?** On re-run, the
answers can be injected as loose context (today's `## Injected context`) or as a
structured "these were your questions, here are the answers, do not ask them
again" block.
Proposal: structured, and state explicitly that questions already answered must
not be re-asked. This is the same failure the builder had, one wave earlier.
> Answer: Confirmed — proposal accepted. Structured block, with an explicit instruction not to re-ask a settled question.

**Q6 — Naming.** `retry planner` / `retry builder` (role ids, consistent with
`kcia agent set`) versus the `retry planning` the request used.
Proposal: role ids as canonical, with `planning`/`building` accepted as
aliases. Keeping one vocabulary for roles across the CLI matters more than the
gerund.
> Answer: Confirmed — proposal accepted. `retry planner` / `retry builder` canonical, `planning`/`building` as aliases.

## Proposed plan

### Step 1 — Parse and track open questions

- New module `cli/src/kcia/waves/questions.py`: `parse_questions(task_text)` →
  ordered list of `Question(text, answer)`, per Q1/Q2. Pure text in, data out —
  same shape and testability as `waves/blocked.py`.
- `unanswered(questions)` helper used by every gate below.
- Record the count in the session on wave completion so `kcia work show` can
  report it without re-parsing.

### Step 2 — Gate the approval (`commands/work.py`, `waves/runner.py`)

- `require_approval` (`runner.py:225`) — or the `work_approve` command, per
  where the check reads best — refuses while questions are unanswered:

  ```
  Cannot approve: 2 open questions in .ai/context/task.md are unanswered.
    1. Should the DTO mappers move to the data layer, or stay in presentation?
    2. Is the 100% coverage rule binding on moved lines, or only new ones?
  Answer them:
    kcia work answer "<answer>"        # answers the first
    kcia work answer --file <path>     # or edit .ai/context/task.md directly
  Then:  kcia work retry planner
  ```

- Same check at the end of the planner run (Q4), so the user meets the
  questions before reaching for `approve`.
- `--force` per Q3, recorded in the approval note.

### Step 3 — Role-scoped retry (`waves/runner.py`, `commands/work.py`)

- `retry_role(session, role_id, ...)`: read the wave list from
  `control-plane/agents/roles.yaml` (already loaded for `AGENT_ROLES`), reset
  each of that role's waves to `pending`, and run them in `order`.
- `retry planner` additionally clears the approval and resets the builder's
  waves — a plan that changed was never approved.
- `retry builder` keeps the approval and re-runs only the builder's waves.
- `kcia work retry <wave_id>` keeps working unchanged; the argument is a role id
  when it matches one, a wave id otherwise. No removal, no breaking change.

### Step 4 — Answering triggers the rebuild (`commands/work.py:739`)

New `work_answer` behaviour, in order:

1. Write the answer into `task.md` under its question (Q2), instead of only
   appending a free-floating injection.
2. If a wave is blocked → retry that wave (today's behaviour, unchanged).
3. Else, if questions remain unanswered → say how many are left and stop.
4. Else → run `retry_role("planner")` automatically. This is the "one command"
   the request asks for: answer the last question and the plan rebuilds itself.

`--no-retry` keeps recording without running, for a user staging several
answers.

### Step 5 — Feed the answers back into the prompt (`waves/prompts.py`)

- New `answered-questions` section, rendered only when answers exist,
  containing each question with its answer and an instruction not to re-ask
  them (Q5). Positioned with the other task-context sections and registered in
  the budget `drop_order` — though it should be near-unsheddable: dropping the
  answers reintroduces the exact loop this plan closes.

### Step 6 — Template support (`control-plane/waves/prompts/understanding.md.j2`)

- Require the questions in a fixed `## Open questions` shape with an
  `> Answer:` line under each, so Step 1 has something deterministic to parse.
- State that a question with an answer already present is settled.
- Data edit; benefits every provider.

### Step 7 — Tests

- `tests/test_questions.py`: parsing, answered/unanswered detection, malformed
  sections, no-questions case.
- `tests/test_approval.py`: approve refused with unanswered questions; allowed
  with `--force`; allowed once answered.
- `tests/test_waves.py`: `retry planner` resets the three planner waves, clears
  the approval and resets the builder waves; `retry builder` keeps the approval
  and touches only its two.
- `tests/test_task_answer.py`: answering the last question triggers the planner
  retry; answering a non-final one does not.
- `tests/test_prompt_composition.py`: the answers section renders and carries
  the do-not-re-ask instruction.

### Step 8 — VERSION bump

**Minor.** New commands (`retry <role>`), a new gate, and a new artifact
contract in `task.md` — additive, with no `schema_version` change and no
command removed. `control-plane/VERSION` also moves, since
`understanding.md.j2` and the guidance change; note that it is still at `1.8.0`
while two commits have already touched `control-plane/`, so that bump is
overdue independently of this plan.

## Sequencing against the other open plans

Three plans are now open and they are not independent:

1. `20260909T144207Z_close_the_builder_escape_hatch.md` — the builder blocks
   instead of working. **Do this first**: it is the active failure, and its
   Step 0 (one command, `devstral` vs `qwen2.5-coder`) also tells us whether the
   Ollama tool loop works at all. Everything else is unverifiable until that is
   answered.
2. **This plan** — questions become real, retry collapses to two commands. It
   removes the reason the planner had to answer its own questions inline.
3. `20260909T085622Z_file_level_context_for_planner.md` — file-level inventory
   in the prompt. Last, because it improves plan quality rather than fixing a
   broken control flow, and it is the only one of the three that is purely
   additive.

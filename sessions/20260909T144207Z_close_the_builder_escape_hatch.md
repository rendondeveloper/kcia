# Stop the builder from blocking instead of working

> Recreated on 2026-09-09 after the original file was deleted from `sessions/`
> before being committed. Content is unchanged; the open questions now carry the
> user's answers.

## Analysis

### The evidence

```
implementation · builder · ollama/qwen2.5-coder:14b — completed (1m41s, 0 tool calls, 8.8k tokens)
Stopped at `implementation` — the agent cannot proceed.
  Confirm the DTO leakage approach and test gate expectations.
```

`.ai/local/runs/implementation-01.blocked.md` contains exactly one line:

```
BLOCKED: Confirm the DTO leakage approach and test gate expectations.
```

Three facts matter, in this order:

1. **0 tool calls.** The builder never read a file, never listed a directory,
   never grepped. It did no work at all.
2. **The plan already answers both questions**, by name, in a section titled
   *"Decisions on the understanding wave's open questions"*: item 1 is
   "DTO leakage — option (b), full boundary fix", item 2 is
   "Test gate — minimal net now", each with its rationale.
3. **8.8k tokens in 1m41s** for a task whose plan lists 38 files to create.

### Why the builder "had doubts": it did not

It echoed the plan's own two decision headings back as a question. That is not
confusion about content — it is pattern-matching on the words "open questions"
and taking the cheapest available exit.

The prompt offers that exit explicitly. `waves/prompts.py:206-210` injects
`_blocked.md.j2` into **every** wave, unconditionally:

```
## If you cannot proceed

If continuing would mean guessing, reply with one line — `BLOCKED: <question>` —
and nothing else. An open question you can work around is not a blocker.
```

Compare it with the entire `implementation` wave instruction
(`control-plane/waves/prompts/implementation.md.j2`), which is three lines:

```
## Wave: implementation

Implement the approved plan with minimal, tested changes.
```

So at the end of the prompt — the most salient position for any model — the
escape hatch is **longer, more concrete, and more actionable than the
instruction it is meant to be an exception to**. `BLOCKED: <question>` costs
about fifteen output tokens. Implementing the plan costs thousands of tokens
across dozens of tool calls. A frontier model resists that gradient because it
weighs the instruction's intent; a 14B model follows the gradient.

### This is an inconsistency in kcia, not only a model failure

`can_ask_questions` already exists as a per-wave field
(`waves/definitions.py:34`, default `False`) and only `understanding` sets it
`true` (`control-plane/waves/waves.yaml:13`). It is passed into the wave
template and `understanding.md.j2` honours it.

But `_blocked.md.j2` is added as its own section regardless of that flag. So
`implementation` — a wave that declares it cannot ask questions, and that only
runs *after* a human approved the plan (`requires_approval: true`) — is handed
the question protocol anyway. The wave says "no questions"; the prompt says
"here is how to ask one".

Worse, the block is honoured with no evidence of effort. `detect_blocked`
(`waves/blocked.py:38`) reads the output text alone. A run with
`tool_calls == 0` and no files read cannot have discovered a genuine blocker —
there was nothing to discover it from. kcia currently treats "I didn't look"
and "I looked and the plan is contradictory" identically.

### What is *not* the cause

**The plan.** It is close to the ceiling of what a plan can specify: 38 create
paths, 5 delete globs, per-file implementation order, current/modified flow,
a risks table with mitigations, an explicit validation gate, and even
pre-existing bugs marked "flag only, do not fix". Adding detail to it would not
have changed the outcome, because the builder read none of it carefully enough
to call a single tool. **Any effort spent making plans more detailed is wasted
until the escape hatch is closed.**

This is the opposite diagnosis from the file-level-context plan (planner starved
of file inventory), and both are true: the *planner* is under-informed, while
the *builder* was given an excellent plan and an easier way out.

### The model choice is real, though secondary

`qwen2.5-coder:14b` is a code-completion model, not an agentic one. It was
catalogued with exactly this caveat in the README: *"Expect weaker tool-call
discipline than devstral; it is a memory trade, not a free win."* This run is
that caveat happening.

The free builder that should work is **`devstral-small-2:24b`**, already pulled
— it is trained for agentic coding and multi-step tool calling, which is
precisely what the in-process loop depends on. Second choice `qwen3:14b`.

**Unverified and important:** there is no evidence yet that the Ollama tool loop
has *ever* produced a tool call. `tool_definitions(req)` is always passed
(`providers/ollama/tools.py:21`, `loop.py:55`), so the wiring looks right, but
"the model won't call tools" and "the loop's tool calls never register" produce
an identical `0 tool calls` line. That must be distinguished before any other
work, and it is one command (Step 0).

## Open questions

**Q1 — Should `implementation` be able to block at all?** After a human
approved the plan, is a mid-wave question ever legitimate — or should the wave
fail with its findings and let the user re-plan?
Proposal: keep blocking possible, but only after demonstrated work (Q2). A
genuinely contradictory plan is real and should not be forced into a bad
implementation.
> Answer: Confirmed — proposal accepted. Blocking stays possible, gated on evidence of real work.

**Q2 — Evidence gate for a block.** Require `tool_calls > 0` (or ≥ N files
read) before a `BLOCKED:` line is honoured; otherwise treat the attempt as
failed and retry with the reason injected?
Proposal: yes, `tool_calls > 0`. It is the smallest possible rule that
separates "I looked" from "I didn't", and it needs no model cooperation.
> Answer: Confirmed — proposal accepted. `tool_calls > 0` is the gate.

**Q3 — Retry budget.** How many times before giving up and surfacing to the
user? The retry must carry a pointed instruction ("the plan answers this at
*Decisions*, section N — read it and proceed"), not just a repeat of the prompt.
Proposal: one retry, then surface with both attempts recorded.
> Answer: Confirmed — proposal accepted. Exactly one retry, then surface with both attempts recorded.

**Q4 — Gate `_blocked.md.j2` on `can_ask_questions`?** Removing it entirely
from non-asking waves is the clean reading of the flag, but then a model with a
real blocker has no protocol and will improvise a shape `detect_blocked` may
not match.
Proposal: keep the section in every wave but make the text wave-aware — for
post-approval waves, state that the plan's decisions are final and that a block
is only for a contradiction discovered *while reading the code*.
> Answer: Confirmed — proposal accepted. Section stays in every wave; its text becomes wave-aware.

**Q5 — Should the builder be told which model produced the plan?**
> Answer: Out of scope, as proposed. Revisit after the escape hatch is closed.

## Proposed plan

### Step 0 — Distinguish the two causes (do this first, before any code)

Re-run the same wave with the agentic model, changing nothing else:

```bash
kcia agent set builder ollama --model devstral-small-2:24b
kcia work retry        # in the identaplusschool repo
```

- **Tool calls > 0** → the loop works; this is prompt shape + model choice, and
  Steps 1-4 are the fix.
- **Still 0 tool calls** → suspect the loop. Then the first bug to check is
  whether `_merge_tool_calls` (`providers/ollama/loop.py:177`) correctly
  assembles tool calls that arrive split across streamed chunks, since a
  mis-merge would silently drop every call. Steps 1-4 would still be worth
  doing, but they would not be the cause.

Record the outcome in this file before implementing.

### Step 1 — Evidence gate on blocking (`waves/runner.py`, `waves/blocked.py`)

- Extend the block check at the two call sites (`runner.py:332`, `runner.py:597`)
  to consider the run's `RunResult.tool_calls` alongside the text: a `BLOCKED:`
  line with `tool_calls == 0` is not honoured.
- Such an attempt becomes a failed attempt and is retried **once** (Q3), with a
  pointed instruction injected through the existing `validation_error` channel
  (`prompts.py:190-193`) — naming the plan section that already answers the
  question when one can be identified, and otherwise stating that no file was
  read. On the second failure, surface to the user with both attempts recorded.
- Keep `detect_blocked` itself a pure text function — it has focused tests and
  three other callers (`skills/result.py`, `integrations/tickets.py`). The gate
  belongs in the wave runner, which is the only place that knows about tools.

### Step 2 — Wave-aware blocked protocol (`control-plane/waves/prompts/_blocked.md.j2`)

- Make the template conditional on `can_ask_questions` (already available in the
  render context). For waves that cannot ask: state that the plan is
  human-approved, its decisions are final, and re-asking a question the plan
  already decided is not a blocker.
- Add the one instruction that would have prevented this exact run: *if the plan
  contains a section recording decisions, those decisions are the answer — do
  not ask for them to be confirmed.*
- Say that a block is only legitimate for a contradiction found **while reading
  the code**, which pairs the prompt with the Step 1 gate instead of leaving the
  rule implicit.

### Step 3 — Make the implementation instruction directive
(`control-plane/waves/prompts/implementation.md.j2`)

Currently three lines. It should tell the builder how to start, because a small
model will not infer it:

- The plan in `.ai/context/plan.md` is approved; follow its
  `## Implementation order` step by step.
- Begin by reading the files named in `affected_files.modify` and the paths you
  will delete — before writing anything.
- Definition of done is the plan's `## Validation` block; run it.
- Report what you could not do; do not stop before trying.

Data edit, benefits every provider.

### Step 4 — Surface the no-work case honestly (`commands/work.py`)

When a wave ends with 0 tool calls, `_render_blocked` (`work.py:278`) should say
so plainly — "the agent stopped without reading any file" — instead of
presenting the question as if it came from analysis. The current output invites
the user to answer a question that did not need asking.

### Step 5 — Tests

- `tests/test_blocked.py` / `tests/test_waves.py`: a `BLOCKED:` output with
  `tool_calls == 0` retries instead of blocking; the same output with tool calls
  blocks as today; a second consecutive no-work attempt surfaces to the user.
- `tests/test_prompt_composition.py`: the blocked section differs between a
  wave with `can_ask_questions: true` and one without.
- Regression: the three non-wave callers of `detect_blocked` are unaffected.

### Step 6 — VERSION bump

**Patch** (`0.20.0` → `0.20.1`, `control-plane` `1.8.0` → `1.8.1`): gating an
existing prompt section and adding a one-retry condition is behavioural, not a
new capability.

### Step 0 — outcome

Not run in this pass (requires the identaplusschool repo and a live Ollama
provider). Implement Steps 1–4 first; re-run Step 0 manually after deploy.

## Direct answers to the three questions asked

**Why did the builder have these doubts?** It didn't. It never read the code —
0 tool calls — and repeated the plan's own two decision headings back as a
question, because the prompt ends by offering a fifteen-token way out of a
thousand-token job.

**How do we leave it with no doubts?** Not by writing a more detailed plan; this
plan is already exceptional and it changed nothing. Close the exit (Steps 1-2),
tell the builder how to start (Step 3), and use a model trained to call tools
(Step 0).

**Which free model?** `devstral-small-2:24b`, already pulled. It is the only one
of the three local tags trained for agentic coding. `qwen2.5-coder:14b` should
be treated as a fallback for memory-constrained machines, not as a builder of
first choice — which is what its catalog entry and README row already say.

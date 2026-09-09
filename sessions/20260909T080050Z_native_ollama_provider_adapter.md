# Native Ollama provider (direct HTTP, in-process agent loop)

## Goal

Add `ollama` as a first-class kcia provider so roles can be configured as:

```
kcia agent set planner ollama --model qwen3:14b
kcia agent set builder ollama --model devstral-small-2:24b
```

Locally available models (confirmed by the user):

| model                  | id           | size  |
|------------------------|--------------|-------|
| `devstral-small-2:24b` | 24277f07f62d | 15 GB |
| `qwen3:14b`            | bdbd181c33f2 | 9.3 GB |

Explicit requirement from the user: connect **directly** to Ollama (no OpenCode
or other CLI in between) and keep the design **highly flexible** — adding or
swapping a local model must not require a code change.

## Analysis

### 1. The provider layer assumes a subprocess CLI

`providers/runner.py:run_provider` is the only execution engine. It:

1. calls `adapter.build_command(req)` to get an argv,
2. `subprocess.Popen`s it with piped stdio,
3. writes `req.prompt` to stdin,
4. reads stdout line by line and hands each line to
   `adapter.parse_stream_line(line, state)`,
5. turns the resulting `StreamEvent`s into a `RunResult`.

`ProviderAdapter` (`providers/base.py:70-88`) is a `Protocol` shaped entirely
around that: `build_command`, `parse_stream_line`, `new_session_id`.

Ollama is **not** a CLI agent. `ollama run <model>` is a chat REPL: no tool
calling, no file reads or writes, no session ids, no structured event stream.
The agentic behaviour kcia expects from a provider (`FileWrite`, `ToolCallStart`,
`allow_edits`) does not exist in the binary — it exists in the HTTP API
(`POST /api/chat` with `tools`, streaming NDJSON), but the *tool execution loop*
is the client's job.

So this feature is not "one more adapter". It is:

- **a second execution mode** in the provider layer (in-process instead of
  subprocess), and
- **kcia's own agent loop**: tool schemas, tool executors, and the read/write
  sandbox that the other three providers get for free from their CLI.

This is the real cost of route 2 and the reason it must be planned, not
improvised.

### 2. Dispatch: where the in-process runner hooks in

Every call site follows the same shape (`waves/runner.py:320,590,862`,
`ask/run.py:79`, `skills/runner.py:118`, `integrations/tickets.py:108`):

```python
runner = provider_runner or run_provider
result = call_provider(runner, adapter, req, on_event, should_cancel)
```

`call_provider` (`providers/runner.py:29-52`) already inspects the runner's
signature and forwards only the callbacks it accepts. That is the single
choke point: if `call_provider` prefers an adapter-supplied `run()` when the
caller did not inject an explicit `provider_runner`, **all six call sites work
unchanged** and the test-injected fake runners keep winning. One edit, no
churn in the wave runner.

### 3. `locate()` / "installed" is executable-shaped, and that is wrong here

- `registry.is_provider_installed` → `shutil.which(entry.executable)`.
- `doctor.py:122-125` treats a missing adapter as "not installed".
- `commands/agent.py:144-146` `_installed()` → `registry[id].locate() is not None`.

For Ollama the meaningful question is "is the daemon reachable at
`OLLAMA_HOST` (default `http://127.0.0.1:11434`)?" — the binary may be absent
while a remote host serves fine, and present while nothing is listening.
`locate()` must therefore report daemon reachability, not `which ollama`.

`check_auth` is trivially `AUTHENTICATED` when the daemon answers,
`NOT_INSTALLED` when it does not — there is no auth. `account()` can report the
host and the Ollama version from `/api/version`, which is the genuinely useful
line in `kcia doctor`.

### 4. Flexibility: the catalog is currently a hard allowlist

`config.set_agent` (`config.py:183-189`) rejects any model id not listed in
`catalog.yaml`, and `doctor`/`agent show` warn via `model_in_catalog`
(`config.py:156-166`). With a hard-coded model list, every `ollama pull` would
require editing `control-plane/providers/catalog.yaml` — directly against the
"highly flexible" requirement.

Ollama already exposes the ground truth at `GET /api/tags`. The adapter should
implement `discover_models()` (the same optional hook `commands/agent.py:172`
already looks for and `OpenCodeAdapter` already provides), and model validation
for this provider should consult the live list, with the catalog entry acting
as a documented default rather than a whitelist. See open question Q1 — this
touches shared validation code, so it needs a decision, not an assumption.

Note also `providers/opencode.py:27`: `_MODEL_ID = re.compile(r"^[\w.-]+/[\w.-]+$")`
does not accept `:`. Ollama tags always carry one (`qwen3:14b`). The Ollama
adapter must not reuse that pattern. (The OpenCode regex is out of scope here;
it only matters if someone routes Ollama through OpenCode, which this plan
replaces.)

### 5. `num_ctx` — the silent-failure trap

Ollama defaults a model's context window to a small value (commonly 4096
tokens) regardless of what the model supports. kcia's wave budget is
`max_prompt_tokens: 120000` (`control-plane/waves/waves.yaml:3`). Without an
explicit `options.num_ctx`, Ollama silently truncates the prompt and the run
produces plausible-looking garbage with no error anywhere. The adapter must
send `num_ctx` explicitly and it must be per-model configurable.

Practical ceiling on the user's machine: `devstral-small-2:24b` at 15 GB plus a
large KV cache will not hold 120k tokens comfortably. The realistic per-model
`num_ctx` (32k / 40k) and the interaction with the wave budget is open
question Q2.

### 6. Tool surface and the write sandbox

The other providers enforce edit permissions themselves. Here kcia must do it.
Minimum viable tool set for the two roles:

- read-only: `read_file`, `list_dir`, `grep`, `glob`
- write (only when `req.allow_edits`): `write_file`, `edit_file`
- `run_command` — needed for the `implementation` wave's validation
  (`validation: required`), but it is arbitrary code execution driven by a
  local model. See Q3.

Waves already declare `edit_scope` (`.ai/**` for `documentation-init` and
`documentation-final`, `**` for `implementation`), but **`edit_scope` is not
carried on `RunRequest`** — `waves/runner.py` passes `allow_edits` only, and
`allowed_tools`/`disallowed_tools` are passed as `None` at every site. For
CLI providers that gap is the CLI's problem; for an in-process loop kcia would
be the one silently ignoring the declared scope. See Q4.

Path confinement must follow the existing precedent in
`profiles/predicates.py::_safe_path` (resolve, then assert the path stays under
the root) rather than inventing a second scheme.

### 7. Model-side caveats

- **Tool calling**: both models support it, but small local models are
  materially worse at emitting well-formed tool calls than the hosted ones. The
  loop must tolerate malformed arguments (return a tool error message to the
  model and continue) rather than crash the wave — same spirit as
  `parse_stream_line` returning `[]` on garbage (`tests/test_agents.py:125`).
- **`qwen3:14b` thinking**: Ollama exposes `think: true/false`. This is the
  natural mapping for `RunRequest.effort` and for `supports_effort`. Thinking
  content must not leak into `TurnEnd.final_text`, or the planner's
  `.ai/context/plan.md` gets polluted with reasoning.
- **Sessions**: there is no server-side session. `supports_sessions` can only
  be true if kcia persists the message transcript itself (e.g. under
  `.ai/local/`, already gitignored). Q5.
- **Usage**: `/api/chat` returns `prompt_eval_count` / `eval_count`, which map
  cleanly onto `UsageUpdate(input_tokens, output_tokens, cached=0)`.
- **MCP**: `supports_mcp_config` is `False` for the first version. Bridging MCP
  servers into a hand-rolled tool loop is a separate feature; `OpenCodeAdapter`
  sets it `False` too, so there is precedent and the wave runner handles it.

## Open questions

**Q1 — Model validation.** Should `config.set_agent` accept any model id for
`ollama` (validated live against `/api/tags`, warning if absent but not
blocking), or should `catalog.yaml` stay the source of truth and require an
edit per pulled model?
Proposal: live validation with a soft warning — it is what "highly flexible"
asks for. Implemented as an opt-in flag on the catalog entry
(`model_source: live`) so `claude`/`cursor`/`opencode` behaviour is untouched.
> Answer: Accepted — live validation with soft warning via `model_source: live`.

**Q2 — `num_ctx` and the budget.** What context length per model, and should
`max_prompt_tokens` be lowered when the active provider is Ollama?
Proposal: per-model `num_ctx` in the catalog entry (`qwen3:14b` 32768,
`devstral-small-2:24b` 32768), and the adapter reports its effective context so
the budget can clamp rather than silently overflow.
> Answer: Accepted — 32768 per catalog model; adapter exposes `effective_context_tokens`.

**Q3 — `run_command`.** Include a shell tool in v1 (required for the
`implementation` wave to validate its own work), or ship read/write only and
have the builder report validation as a blocker?
Proposal: include it, gated on `allow_edits`, with the command echoed as a
`ToolCallStart` so it is visible in the stream.
> Answer: Accepted — included in v1, gated on `allow_edits`.

**Q4 — `edit_scope` enforcement.** Add `edit_scope: list[str] | None` to
`RunRequest` and have the Ollama loop enforce it with `pathspec` (the same
library `profiles/resolver` already uses), or ignore scope in v1 like the CLI
providers effectively do?
Proposal: add the field. It is additive, defaults to `None`, and without it a
local model can write outside `.ai/**` during a documentation wave.
> Answer: Accepted — `edit_scope` on `RunRequest`, enforced in Ollama write tools.

**Q5 — Sessions.** Persist transcripts under `.ai/local/` to support
`--resume`, or set `supports_sessions=False` in v1?
Proposal: `False` in v1; smaller surface, and resume is not needed for a first
working loop.
> Answer: Accepted — `supports_sessions=False` in v1.

**Q6 — Streaming granularity.** Emit `TextDelta` per token chunk (matches
`supports_streaming=True` and the existing progress UI), or buffer per message?
Proposal: per chunk.
> Answer: Accepted — per-chunk `TextDelta`.

## Proposed plan

Ordered; each step is independently testable.

### Step 1 — Adapter-owned execution (`providers/runner.py`, `providers/base.py`)

- Extend the `ProviderAdapter` protocol with an **optional** `run(req, *, on_event=None, should_cancel=None) -> RunResult`.
- In `call_provider`, when the caller passed the default `run_provider` *and*
  the adapter defines `run`, dispatch to `adapter.run` instead. An explicitly
  injected `provider_runner` always wins, so every test fake keeps working.
- Subprocess adapters (`claude`, `cursor`, `opencode`) are untouched.

### Step 2 — HTTP client (`providers/ollama/client.py`)

- `httpx` (already a dependency, `cli/pyproject.toml:16`).
- Host from `OLLAMA_HOST`, default `http://127.0.0.1:11434`.
- `version()` → `GET /api/version`; `tags()` → `GET /api/tags`;
  `chat(messages, tools, options, think, stream=True)` → `POST /api/chat`,
  yielding parsed NDJSON objects.
- Timeouts sized for local inference (a 24B model's first token is slow); no
  read timeout shorter than `RunnerLimits.idle_timeout_seconds` (180 s).

### Step 3 — Tool layer (`providers/ollama/tools.py`)

- JSON-schema tool definitions for the set agreed in Q3/Q4.
- Executors returning `(ok, content)`; every path resolved and confined to
  `req.cwd` via a `_safe_path`-style check.
- Write tools registered only when `req.allow_edits`; scope-checked per Q4.
- Malformed arguments produce a tool-error result fed back to the model, never
  an exception that kills the wave.

### Step 4 — Agent loop (`providers/ollama/loop.py`)

- Build messages from `req.prompt` (+ system framing).
- Loop: stream a chat response → emit `TextDelta` for content, `ToolCallStart`
  / `ToolCallEnd` / `FileRead` / `FileWrite` for tool activity → append tool
  results → repeat until the model stops requesting tools or a max-iteration
  cap is hit.
- Poll `should_cancel` between chunks and between iterations.
- Emit `UsageUpdate` from `prompt_eval_count` / `eval_count`, `TurnEnd` with
  the final assistant text (thinking stripped), `ProviderError(fatal=True)` on
  an unreachable daemon or unknown model.
- Populate the same `StreamState` fields the subprocess path does, so
  `RunResult` is identical in shape to the other providers.

### Step 5 — Adapter (`providers/ollama/adapter.py`)

- `id = "ollama"`, capabilities: streaming ✓, sessions per Q5, effort ✓
  (`think`), tool restriction ✓, mcp_config ✗.
- `locate()` → daemon reachability, not `shutil.which`.
- `check_auth()` → `AUTHENTICATED` / `NOT_INSTALLED`.
- `account()` → `"<host> (ollama <version>)"`.
- `discover_models()` / `list_models()` → `/api/tags`, catalog as fallback.
- `build_command` / `parse_stream_line` raise `NotImplementedError` with a
  message pointing at `run()` (the protocol still declares them).
- Register in `registry._BUILTIN_ADAPTERS`.

### Step 6 — Control-plane data (`control-plane/providers/catalog.yaml`)

```yaml
ollama:
  display_name: "Ollama (local)"
  executable: ollama
  install_hint: "brew install ollama  then  ollama serve"
  auth: none
  auth_hint: "No auth. Ensure the daemon is running (`ollama serve`) and OLLAMA_HOST points at it."
  models:
    - { id: qwen3:14b, tier: balanced, best_for: [planning, analysis] }
    - { id: devstral-small-2:24b, tier: max, best_for: [implementation, refactor] }
  default_model: devstral-small-2:24b
```

Plus `model_source: live` and per-model `num_ctx` per Q1/Q2. Data edit only —
no Python change is needed to add a third local model later.

### Step 7 — `doctor` / `agent` adjustments

- `doctor`: report `ollama: authenticated as <host> (ollama x.y.z)` and, when
  unreachable, `not installed` with the `install_hint`. Verify the existing
  branch at `doctor.py:120-140` reads correctly for a provider with no auth.
- `commands/agent.py`: `_installed()` already delegates to `locate()`, so it
  follows automatically once Step 5 lands.

### Step 8 — Tests (`tests/test_providers_ollama.py`)

New file, mirroring `tests/test_providers_opencode.py`:

- fixture NDJSON transcript → expected event sequence (content, tool call,
  usage, turn end);
- tool executors: path escape rejected, write refused when `allow_edits=False`,
  scope violation refused (Q4);
- malformed tool arguments → tool-error result, loop continues;
- `discover_models` parses `/api/tags` (with `:` in the id);
- `check_auth` when the daemon is unreachable;
- `call_provider` prefers `adapter.run` but yields to an injected
  `provider_runner` (guards Step 1 against regressing every existing runner
  test).

All HTTP mocked via `httpx.MockTransport`; no test may contact a real daemon.

### Step 9 — VERSION bump

`cli/src/kcia/__init__.py`: `0.19.5` → **`0.20.0`** (minor).

Rationale: a new provider and a new adapter-owned execution mode are new
capability, additive only — no existing provider, command, or config file
changes meaning. Not major: nothing breaks for current users. Not patch: this
is well beyond a fix.

**Implemented:** `0.19.5` → `0.20.0` (minor).

## Out of scope

- MCP bridging into the Ollama tool loop.
- Cross-provider mixing inside a single wave.
- Changing `_MODEL_ID` in `providers/opencode.py`.
- Any prompt-template change to accommodate smaller models — if the wave
  prompts turn out to be too dense for a 14B model, that is a follow-up
  driven by evidence from an actual run.

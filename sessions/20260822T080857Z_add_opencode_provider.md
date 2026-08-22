# Add OpenCode as a third provider (parity with Claude Code and Cursor)

## Request

Add `opencode` ([https://opencode.ai](https://opencode.ai), `sst/opencode`) as a provider so it behaves "just like" `claude` and `cursor`: discoverable via the provider catalog, runnable as an agent (`kcia agent run` / whatever drives sessions), and receiving init-time adapter files the way Claude Code gets `CLAUDE.md` and Cursor gets `.cursor/rules/`.

## Analysis

kcia has **two separate, disconnected mechanisms** that both call themselves "providers," and a new provider must satisfy both to have real parity:

### A. Init-time adapter file rendering (`kcia init`) — hardcoded Python, not catalog-driven

`cli/src/kcia/commands/init.py`, function `_write_adapters`:

- Renders `CLAUDE.md.j2` and `AGENTS.md.j2` unconditionally on every init, regardless of what's in `providers/catalog.yaml`.
- Separately, cursor gets its own hardcoded branch: writes `.cursor/rules/00-core.mdc` from `cursor/00-core.mdc.j2`, then a `NN-<profile-id>.mdc` per detected profile from `cursor/profile.mdc.j2`, driven by `resolved.adapters.get("cursor")` (globs) read off each profile spec.
- `GITIGNORE_ENTRIES` (top of `init.py`) is a hardcoded tuple: `"CLAUDE.md"`, `"AGENTS.md"`, `.cursor/rules/`, `.cursor/mcp.json`, etc. Nothing here is generated from a provider list.
- Templates live under `control-plane/templates/adapters/` by filename convention (`CLAUDE.md.j2`, `AGENTS.md.j2`, `cursor/*.j2`) — there is no registry mapping provider id → template.
- Contract test: `tests/test_init.py::test_init_writes_manifest_bundles_and_adapters` asserts the exact file set produced (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/00-core.mdc`, `.cursor/rules/*-backend-dart.mdc`).

**Implication:** adding opencode's init-time output means editing `_write_adapters` and `GITIGNORE_ENTRIES` in Python — it is not achievable by data/template files alone with the current design.

### B. Runtime provider adapters (agent execution) — catalog + Python plugin registry

- `control-plane/providers/catalog.yaml` — pure data (`claude`, `cursor` entries: `display_name`, `executable`, `install_hint`, `auth`, `auth_hint`, `models[]`, `default_model`). Loaded by `cli/src/kcia/providers/catalog.py` into `ProviderCatalogEntry`. This part *is* fully data-driven — a new `opencode:` block here is sufficient for anything that just reads the catalog (e.g. `kcia agent set`/`kcia doctor`, if implemented).
- `cli/src/kcia/providers/registry.py` — `_BUILTIN_ADAPTERS = {"claude": ClaudeAdapter, "cursor": CursorAdapter}`. `build_registry()` only instantiates adapters that exist in **both** this dict and the catalog. There's also an `importlib.metadata` entry-point group `"kcia.providers"` for adapters shipped as separate installable packages — a possible alternative to editing `registry.py` directly, but adds packaging overhead not otherwise used in this repo.
- `cli/src/kcia/providers/base.py` — the `ProviderAdapter` Protocol every adapter implements: `locate()`, `list_models()`, `check_auth()`, `build_command(req)`, `parse_stream_line(line, state)`, `new_session_id()`, plus a `ProviderCapabilities` flags struct (streaming, sessions, effort, tool_restriction, mcp_config).
- `cli/src/kcia/providers/claude.py` / `cursor.py` are the concrete adapters to model a new `opencode.py` on.

**Implication:** a real `OpenCodeAdapter` requires knowing OpenCode's actual CLI invocation and streaming output shape. Follow-up research (same day, still plan-only) closed that gap enough to specify a runtime adapter without a local `opencode` binary — see "Runtime CLI (now confirmed)" below. Whether this plan includes that adapter is still question 1.

### Runtime CLI (now confirmed from [https://opencode.ai/docs/cli/](https://opencode.ai/docs/cli/))

Headless invocation is `opencode run [message..]`. Relevant flags for a `ProviderAdapter`:

- `--format json` — NDJSON (one JSON object per stdout line), not Claude's `stream-json`.
- `-m, --model` — `provider/model` (e.g. `anthropic/claude-sonnet-4-5`).
- `--variant` — provider-specific reasoning effort (`high`, `max`, `minimal`). Closest analog to Claude `--effort`.
- `-s, --session` / `-c, --continue` — resume a session. OpenCode assigns `sessionID` (camelCase, `ses_…`); there is no `--session-id` for a pre-chosen uuid. `new_session_id()` should return `None` (same idea as "let the CLI mint it").
- `--dir` — working directory.
- `--auto` — auto-approve permissions that are not explicitly denied. Analog to Cursor `--force` / Claude `bypassPermissions` for `allow_edits=True`.
- No `--mcp-config` / per-run MCP file. MCP is read from `opencode.json` (project or global), same class of limitation as Cursor's `.cursor/mcp.json`.
- Auth: `opencode auth list` / `opencode auth login`. Credentials live in `~/.local/share/opencode/auth.json`. Auth is per *AI provider* (Anthropic, OpenAI, …), not a single kcia-style subscription. `check_auth` will have to treat "at least one provider listed" as authenticated, or `UNKNOWN` if the list format is not JSON.
- Live models: `opencode models` prints `provider/model` ids (`--verbose` for metadata). Same role as Cursor's `--list-models`.

NDJSON event shape (from third-party runners that already wrap this CLI; not an official schema page):

- `step_start` — `sessionID` on every line → store `state.session_id`
- `text` — `part.text` → `TextDelta`
- `tool_use` — `part.tool`, `part.state.input` / `output`; `status` is typically `"completed"` in one event → `ToolCallStart` + `ToolCallEnd`; map `read`/`write`/`edit` paths to `FileRead`/`FileWrite`
- `step_finish` — `part.tokens.{input,output,cache.read}` → `UsageUpdate` + `TurnEnd` when `part.reason == "stop"`
- `error` — `error.data.message` → `ProviderError(fatal=True)`

Gotcha: some failures print a non-JSON stack trace even with `--format json`. Parser must ignore non-JSON lines the way Claude/Cursor already do.

Edit gating is weaker than Claude: there is no `--disallowed-tools`. Planner waves (`allow_edits=False`) cannot be hard-blocked from Write/Edit via argv. Closest levers: omit `--auto` (permissions prompt, which hangs `--print`-style runs) or a project `opencode.json` `permission` map. This is a documented limitation to accept, not a blocker for shipping the adapter — Cursor already cannot restrict tools (`supports_tool_restriction=False`).

### Gitignore precedent for question 2 (now confirmed)

`GITIGNORE_ENTRIES` already includes `.cursor/mcp.json` with the comment "May carry per-server headers; it is regenerated by `kcia mcp add`." `tests/test_mcp.py` asserts that path is in the tuple. README says both `.ai/mcp.yaml` and `.cursor/mcp.json` are gitignored because a server entry may carry `headers` with a token.

If this plan generates `opencode.json` *and* puts MCP servers (with optional headers) in it, that file must join `GITIGNORE_ENTRIES` for the same reason. OpenCode's own docs say a project `opencode.json` is "safe to be checked into Git" — that applies to a user-authored config, not to a kcia-rendered file that may embed headers. Do not commit a kcia-generated MCP config.

`kcia mcp add` / `remove` currently calls `render_cursor_config` only. MCP parity for OpenCode means a sibling `render_opencode_config` wired into those same commands, not only into `kcia init`.

### OpenCode's actual file formats (confirmed from [https://opencode.ai/docs/](https://opencode.ai/docs/))

- **Project instructions**: plain `AGENTS.md` at repo root, resolved by upward directory search, same convention Claude Code also honors. **kcia already generates** `AGENTS.md` **unconditionally today** — this channel needs zero new template work; the existing generated file already satisfies OpenCode's primary instructions mechanism.
- **Config file**: `opencode.json` / `opencode.jsonc` at repo root (schema `$schema: https://opencode.ai/config.json`). Relevant keys: `instructions` (extra glob/path/URL list beyond AGENTS.md), `model` / `small_model` (`"anthropic/claude-sonnet-4-5"` style), `mcp` (server map, `local`/`remote` types), `default_agent`, `tools`, `server`.
- **MCP servers**: under `mcp` in `opencode.json` — `"type": "local"` (`command`, `cwd`, `environment`, `enabled`, `timeout`) or `"type": "remote"` (`url`, `headers`, `oauth`, `enabled`, `timeout`). This is the closest analog to `.cursor/mcp.json`.
- **CLI**: executable `opencode`; install via `curl -fsSL https://opencode.ai/install | bash`, `npm install -g opencode-ai`, Homebrew, or Docker.
- **Model ids** (provider-agnostic `provider/model[#effort]`): `anthropic/claude-sonnet-4-5`, `anthropic/claude-haiku-4-5`, `openai/gpt-5.2`, `openai/gpt-5.2#high`, `openrouter/anthropic/claude-sonnet-4.5#high`.
- There is **no OpenCode equivalent of Cursor's per-profile** `.mdc` **glob-scoped rule files** — the nearest analog is the `instructions` array in `opencode.json` (a flat list of extra paths/globs), not a directory of per-profile files.



## Open questions

1. **Scope of this change** — full parity means both layer A (init-time files) and layer B (runtime adapter for actually running `opencode` as an agent). Do you want both in one plan, or should this plan cover only the catalog + `AGENTS.md` reuse + optional `opencode.json`, with the runtime `OpenCodeAdapter` as a separate follow-up? The CLI and NDJSON shape are now documented above, so both scopes are plannable.
   - Answer: Both layers, in this single plan.
2. **`opencode.json` generation** — do you want kcia to render an `opencode.json` (needed for MCP parity with `.cursor/mcp.json`), or is the already-generated `AGENTS.md` enough for now? If yes: gitignore it. Precedent is settled — `.cursor/mcp.json` is in `GITIGNORE_ENTRIES` because it may carry `headers` with a token and is regenerated by `kcia mcp add`. A kcia-rendered `opencode.json` with an `mcp` block is the same class of file.
   - Answer: Yes, generate it — needed for MCP server support.
3. **Per-profile rule scoping** — Cursor gets one `.mdc` file per detected profile via `resolved.adapters.get("cursor")`. OpenCode has no per-profile file mechanism (only a flat `instructions` list in `opencode.json`). Is a single root `AGENTS.md` (already generated) considered sufficient parity, or do you want profile-specific content surfaced into `opencode.json`'s `instructions` array too?
   - Answer: Go ahead with the `opencode.json` `instructions`-array mechanism for per-profile content.
4. **Model catalog entries** — which OpenCode models should populate `providers/catalog.yaml`'s `opencode:` block (mirroring the `tier`/`best_for` shape used for `claude`/`cursor`)? Proposed default based on research: `anthropic/claude-opus-5` (max), `anthropic/claude-sonnet-5` (balanced, default), `anthropic/claude-haiku-4-5` (fast) — but OpenCode is provider-agnostic, so this is a judgment call, not a fixed answer.
   - Answer: Use OpenCode's free-tier models: MiMo V2.5 Free, Hy3 Free, Nemotron 3 Ultra Free, Nemotron 3.5 Lightning Free, Muse Spark 1.2 Free, 0x Alpha Free, Big Pickle.
   - Note: these are display names, not confirmed `provider/model` catalog ids. `opencode models --verbose` must be run at implementation time to resolve each display name to its real id before writing `providers/catalog.yaml` — do not guess ids. Default model (`default_model` field) should be the balanced/general-purpose one of this set unless the implementer's `opencode models` output suggests otherwise.

## Plan

### Layer A — init-time adapter files (`kcia init`)

1. **`control-plane/providers/catalog.yaml`**: add an `opencode:` entry (`display_name: OpenCode`, `executable: opencode`, `install_hint` from `https://opencode.ai/install`, `auth`/`auth_hint` describing `opencode auth login` / `opencode auth list`, `models[]` populated from the resolved free-tier ids (see open question 4), `default_model`).
2. **New template** `control-plane/templates/adapters/opencode/opencode.json.j2`: renders `$schema`, an `instructions` array seeded from each detected profile's `resolved.adapters.get("opencode")` content (mirroring how `cursor/profile.mdc.j2` consumes `resolved.adapters.get("cursor")` globs — profile specs will need an `adapters.opencode` block analogous to `adapters.cursor`), and an empty/placeholder `mcp` object for `kcia mcp add` to populate later.
3. **`cli/src/kcia/commands/init.py` `_write_adapters`**: add an `opencode` branch alongside the existing `cursor` branch — render `opencode.json` from the new template when an `opencode` provider/profile adapter block is present, write it to the repo root.
4. **`GITIGNORE_ENTRIES`** (top of `init.py`): add `"opencode.json"` with a comment matching the `.cursor/mcp.json` precedent ("regenerated by `kcia mcp add`; may carry MCP server headers").
5. **`tests/test_init.py::test_init_writes_manifest_bundles_and_adapters`**: extend the expected file set to include `opencode.json`, and extend/add profile fixtures with an `adapters.opencode` block so the instructions-array rendering has real input to assert on.

### Layer B — runtime provider adapter (agent execution)

6. **New `cli/src/kcia/providers/opencode.py`** implementing the `ProviderAdapter` protocol, modeled on `claude.py` / `cursor.py`:
   - `locate()` — find the `opencode` executable.
   - `list_models()` — parse `opencode models --verbose`.
   - `check_auth()` — parse `opencode auth list`; treat "at least one provider listed" as authenticated, `UNKNOWN` on unparseable output.
   - `build_command(req)` — `opencode run --format json -m <provider/model> [--variant <effort>] [-s <session_id> | -c] [--dir <cwd>] [--auto if allow_edits]`.
   - `parse_stream_line(line, state)` — NDJSON per event shape confirmed in Analysis (`step_start`, `text`, `tool_use`, `step_finish`, `error`); ignore non-JSON lines (stack-trace gotcha).
   - `new_session_id()` — returns `None` (OpenCode mints its own `ses_…` id).
   - `ProviderCapabilities`: `streaming=True`, `sessions=True`, `effort=True`, `tool_restriction=False`, `mcp_config=False` (no per-run MCP flag — MCP lives in `opencode.json`, same limitation class as Cursor).
7. **`cli/src/kcia/providers/registry.py`**: add `"opencode": OpenCodeAdapter` to `_BUILTIN_ADAPTERS`.
8. **New `tests/test_providers_opencode.py`**: mirror existing adapter test coverage — `build_command` argv shape, `parse_stream_line` for each event type including the malformed-line/error cases, `check_auth`, `list_models`.

### MCP parity (`kcia mcp add` / `kcia mcp remove`)

9. Add a sibling `render_opencode_config` next to the existing `render_cursor_config` (same module) — parses any existing `opencode.json`, deep-merges the `mcp` block (`local`/`remote` server types per OpenCode's schema), preserves other top-level keys (`instructions`, `model`, etc.), writes back.
10. Wire `render_opencode_config` into the `kcia mcp add` / `kcia mcp remove` command bodies alongside the existing `render_cursor_config` call.
11. **`tests/test_mcp.py`**: extend for the `opencode.json` gitignore entry and for `render_opencode_config` add/remove/merge behavior (including header-carrying remote servers, matching the `.cursor/mcp.json` precedent).

### Out of scope / accepted limitations

- No hard edit-gating for `allow_edits=False` waves against OpenCode (no `--disallowed-tools` analog) — documented limitation, same class as Cursor's `supports_tool_restriction=False`.
- No per-profile `.mdc`-style directory of files for OpenCode — profile content is folded into the single `opencode.json` `instructions` array instead (per answer to question 3).

## Version bump

Minor (`cli/src/kcia/__init__.py:VERSION`) — this adds a new provider (catalog entry, runtime adapter, init-time file generation, MCP config rendering), which is new capability, not a breaking change or a pure fix. To be applied after implementation, not in this plan-only document.
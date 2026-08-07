# kcia

**Control plane + CLI** that drives your existing agent tools (Claude Code, Cursor) through a
structured, auditable pipeline — without calling any LLM API directly.

## Objective

kcia exists to give coding agents **the right guidance at the right moment**, while keeping
prompts as small as possible.

The guiding principle:

> **Python resolves what has a single verifiable answer** — which files exist, which profile
> owns a path, which command runs where, whether a test passed.
> **The model decides what depends on the concrete problem** — what is relevant, how to
> design the solution.

Concretely, kcia:

1. **Detects** the technologies in your repository and maps them to **profiles** (YAML +
   Markdown packs — no Python per technology).
2. **Composes** a prompt per pipeline step (**wave**) from guardrails, profile references,
   project context, and task state — filtering and budgeting what goes in so the model is
   not flooded with guidance it does not need yet.
3. **Runs** the provider CLI you already pay for (`claude`, `cursor-agent`) as a subprocess,
   with real permission restrictions and per-profile validation after implementation.
4. **Persists** everything on disk — prompts, outputs, token counts — so every step is
   inspectable and the planner → builder handoff is a file, not a conversation thread.

You install kcia **once** on your machine. Each project only gets a `.ai/` directory
(gitignored) when you run `kcia init`.

## Concepts

| Concept | What it is |
|---|---|
| **Profile** | A technology pack: detection rules, shell commands, coding references, boolean rules. |
| **Agent** | One of two roles — `planner` or `builder` — each mapped to a `(provider, model)` pair. |
| **Wave** | One of five sequential pipeline steps, from understanding through documentation. |
| **Task** | A unit of work started with `kcia task init`, tracked in `.ai/local/session.json`. |

## Requirements

- Python 3.11+
- `git`
- At least one provider CLI: `claude` (Claude Code) or `cursor-agent` (Cursor)

## Install

kcia is installed **from a git clone**, not with `pipx` — the CLI needs `control-plane/` on
disk next to `cli/` (see [Why not `pipx install`](#why-not-pipx-install)).

### Step 1 — Clone

```bash
git clone https://github.com/rendondeveloper/kcia.git ~/tools/kcia
cd ~/tools/kcia
```

Keep this directory. It is your distribution copy, not a disposable checkout.

### Step 2 — Virtualenv and editable install

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
```

### Step 3 — Add to PATH

```bash
echo 'export PATH="$HOME/tools/kcia/.venv/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

On bash, append the same line to `~/.bashrc` instead.

### Step 4 — Verify

```bash
kcia --version        # e.g. kcia 0.1.0
kcia profile list     # backend-dart, mobile-flutter, web-flutter
```

If `profile list` is empty, the CLI cannot find `control-plane/`. You are probably running
a broken install outside the clone — repeat steps 2–3 from inside `~/tools/kcia`.

### Step 5 — Configure agents (once)

```bash
kcia agent set planner claude --model claude-opus-5
kcia agent set builder cursor --model claude-sonnet-5
kcia agent show
```

Preferences are stored in `~/.config/kcia/config.yaml` and apply to every project unless
overridden per repo with `--scope repo`.

### Why not `pipx install`

`pipx install ./cli` and
`pipx install "git+https://github.com/rendondeveloper/kcia.git#subdirectory=cli"` both
install the CLI but produce a **broken runtime**. `control-plane/` lives outside `cli/`, so
no Python build backend can bundle it into the wheel; `control_plane_root()` then resolves
to a path that does not exist and every command that needs profiles, waves, roles,
guardrails, or the provider catalog comes up empty.

Packaged installs will be supported once `kcia sync` lands (see [Status](#status)).

## Updating

New versions land on `master`. Update **in your clone** (`~/tools/kcia`), never inside the
projects you work on.

### Routine update

```bash
cd ~/tools/kcia

# 1. Take master exactly as published (discards local changes in the clone).
git fetch origin
git reset --hard origin/master
git clean -fd

# 2. Reinstall so the venv picks up new code.
.venv/bin/pip install -e "./cli[dev]" --force-reinstall --no-deps

# 3. Confirm.
kcia --version
kcia profile list
```

`git reset --hard` is intentional: the clone is a distribution copy, not a place to edit.
Your own projects are untouched.

### After updating — refresh your projects

Read `CHANGELOG.md` first. Two versions move independently:

| Version | Command | What it tracks |
|---|---|---|
| CLI | `kcia --version` | Python code, commands, runner |
| Control plane | `cat control-plane/VERSION` | Profiles, waves, guardrails, templates |

If the control plane version changed, regenerate guidance in each project:

```bash
cd /path/to/your/project
kcia init --yes          # idempotent — rewrites only what changed
```

Or, if you only need to refresh detection:

```bash
kcia profile detect
```

### Full reset (if something still looks wrong)

Rebuild the kcia environment from scratch:

```bash
cd ~/tools/kcia
find . -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
kcia --version
```

In a project where generated output looks stale:

```bash
cd /path/to/your/project
rm -rf .ai/generated .ai/cache
kcia init --yes
```

Never delete `.ai/manifest.yaml` or `.ai/profiles/` — those are yours.

### Uninstall

```bash
rm -rf ~/tools/kcia                  # the clone and its venv
rm -rf ~/.config/kcia                # agent preferences
rm -rf ~/.local/share/kcia           # installed profile packs
# then remove the PATH line from ~/.zshrc
```

Publishing a new version (maintainers only): [RELEASING.md](RELEASING.md).

## Where kcia lives

| | Location | Committed? |
|---|---|---|
| The CLI and control plane | your clone, e.g. `~/tools/kcia` | separate repo |
| Agent preferences (provider, model, effort) | `~/.config/kcia/config.yaml` | no — global to you |
| Installed profile packs | `~/.local/share/kcia/packs/` | no |
| Per-repo state | `<your project>/.ai/` | partly — see below |

Inside a project you work on, **nothing kcia writes is committed**. `kcia init` adds it
all to that project's `.gitignore` for you:

```gitignore
# kcia — generated, do not commit
.ai/
CLAUDE.md
AGENTS.md
.cursor/rules/
```

So a teammate cloning your project sees no kcia files at all; they run `kcia init` once
and get their own. Nothing to configure, nothing to keep in sync by hand.

This includes `.ai/profiles/`. Profiles you write there are local to your working copy
and do not travel with the project — to share a profile with your team, publish it as a
profile pack and install it with `kcia profile add`.

Nothing from kcia's own dependency tree is installed into your project, and your project
needs no Python.

## Quickstart — first task in a project

Run these from **inside the repository** you want kcia to work on:

```bash
cd /path/to/your/project

# 1. Detect technologies, write manifest, generate adapters (idempotent).
kcia init --yes

# 2. Start a task.
kcia task init "fix the overflow on the profile screen"

# Optional: limit which packages drive active profiles.
kcia task init "fix the API" --scope packages/api

# 3. Inspect the pipeline.
kcia wave list

# 4. Run waves one at a time (or omit the wave id to run the next pending).
kcia wave run
kcia wave run understanding
kcia wave run --until analysis

# 5. Inspect progress and token usage.
kcia task show
kcia wave logs understanding
```

`kcia init` writes `.ai/manifest.yaml`, composed profile bundles, provider adapters
(`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`), and adds all generated paths to
`.gitignore`. Run it again after updating kcia — it only rewrites what changed
(`Already up to date` when nothing moved).

Use `--yes` in CI or non-interactive shells. Use `--no-gitignore` if you manage ignore
rules yourself.

Agent configuration (`kcia agent set`) is done once on your machine — see
[Install, step 5](#step-5--configure-agents-once). Use `--scope repo` to override per
repository (written to `.ai/local/agents.yaml`, gitignored).

## How it works

kcia never talks to an LLM API. It drives the **CLI you already have installed and pay
for** — `claude` or `cursor-agent` — as a subprocess: it composes a prompt on disk, pipes
it into the CLI's stdin, and parses the CLI's structured stdout back into normalized
events. Your subscription, your models, your machine.

```mermaid
flowchart TD
    A["kcia wave run"] --> B["Session<br/>.ai/local/session.json"]
    B --> C["Resolve agent<br/>planner or builder → provider + model"]
    C --> D["Compose prompt<br/>filter refs by wave · apply budget · repo map"]
    R["Role"] --> D
    G["Guardrails"] --> D
    M["Repository map"] --> D
    P["Profile bundles<br/>references + rules"] --> D
    X["Context<br/>.ai/context/*.md"] --> D
    T["Wave instruction"] --> D
    D --> E["Write prompt to disk<br/>.ai/local/runs/&lt;wave&gt;-NN.prompt.md"]
    E --> F["Subprocess — prompt via stdin"]
    F --> H["claude / cursor-agent<br/>stream-json stdout"]
    H --> I["Parse → StreamEvents"]
    I --> J["Write wave output<br/>task.md, plan.md, …"]
    J --> K{"validation<br/>required?"}
    K -->|yes| L["Run test/lint per profile root"]
    K -->|no| N["Mark completed"]
    L -->|failed| D
    L -->|passed| N
```

### End-to-end flow

```
your project/          ~/tools/kcia/              provider CLI
─────────────          ─────────────              ────────────
kcia init         →    detect + control-plane  →  (no model yet)
kcia task init    →    session.json
kcia wave run     →    compose prompt        →    claude / cursor-agent
                       write prompt.md            ↓
                       parse stdout         ←    stream-json events
                       write plan.md
                       run dart test / flutter test
```

### 1. Profiles decide which guidance applies where

A **profile** is a data package for one technology: detection rules, shell commands,
coding references, and boolean rules. `kcia init` runs detection over the repository and
records the result in `.ai/manifest.yaml`, mapping each profile to the paths it owns:

```yaml
profiles:
  - id: backend-dart
    roots: ["packages/api/**", "packages/shared/**"]
  - id: mobile-flutter
    roots: ["packages/app_mobile/**"]
```

Several profiles can be active at once, and a file can match more than one — all matches
apply, deliberately. Profiles inherit through `extends` (max 3 levels): `mobile-flutter`
extends `_dart-core`, so it gets the shared Dart guidance plus its own delta. References
concatenate parent-first; commands and rules merge with the child winning.

Adding a technology is a directory of YAML and Markdown. No Python. Detection is written
in a small declarative DSL — 14 leaf predicates (`file_exists`, `yaml_any_key`,
`file_contains`, …) plus `all` / `any` / `not`:

```yaml
detect:
  - when:
      all:
        - file_exists: "pubspec.yaml"
        - yaml_absent: { file: "pubspec.yaml", path: "dependencies.flutter" }
    confidence: high
    evidence: "pubspec.yaml with no Flutter SDK dependency"
```

### 2. What `kcia init` puts in your project

| Path | Contents |
|---|---|
| `.ai/manifest.yaml` | active profiles, their roots, detection evidence |
| `.ai/generated/profiles/<id>/references.md` | the profile's references, concatenated parent-first with the declaring profile noted |
| `.ai/generated/profiles/<id>/profile.md` | commands, rules, inheritance chain, validation requirements |
| `.ai/context/project.md` | project description injected into every prompt |
| `CLAUDE.md`, `AGENTS.md` | adapters listing active profiles and authority order |
| `.cursor/rules/NN-<id>.mdc` | one Cursor rule per profile, with `globs` scoped to that profile's roots |

All of it is gitignored and regenerable — rerun `kcia init` any time.

### 3. A task moves through five waves

`kcia task init "<prompt or ticket>"` creates `.ai/local/session.json`, holding the task,
the active profiles, and per-wave state (`pending`, `running`, `completed`, `failed`,
`skipped`, `awaiting_input`). The waves are **data**, declared in
`control-plane/waves/waves.yaml`:

| # | Wave | Agent | Edits | Writes |
|---|---|---|---|---|
| 1 | `understanding` | planner | no | `.ai/context/task.md` |
| 2 | `analysis` | planner | no | `.ai/context/plan.md` |
| 3 | `documentation-init` | planner | `.ai/**` | `current.md`, `decisions.md` |
| 4 | `implementation` | builder | `**` | your code — validation **required** |
| 5 | `documentation-final` | builder | `.ai/**` | `milestones.md` |

Each wave declares `requires`, so `kcia wave run analysis` fails if `understanding` is not
completed (override with `--force`). A machine-wide lock keyed on pid ensures one wave at a
time; a stale lock left by a dead process is cleared automatically.

The **planner → builder handoff is a file**: the planner writes `plan.md`, and the builder's
prompt includes it verbatim. The two roles can run on different providers — plan with
Claude Opus, implement with Cursor — because the contract between them is on disk, not in a
conversation.

### 4. How the prompt is composed

`waves/prompts.py` assembles one string in a **fixed order**. Nothing is hidden — the exact
text sent is written to `.ai/local/runs/<wave>-NN.prompt.md` on every run, including
retries. Token usage per section is tracked internally for budgeting decisions.

| # | Section | Source |
|---|---|---|
| 1 | Role | `control-plane/agents/roles.yaml` |
| 2 | Guardrails | `control-plane/guardrails/*` (+ `reasoning-limits.md` on `implementation` only) |
| 3 | Project context | `.ai/context/project.md` |
| 4 | Repository map | precomputed from `manifest.yaml` — packages, profiles, test/lint commands |
| 5 | Profile bundles | references (filtered) + boolean rules, per active profile |
| 6 | Task context | `.ai/context/task.md` (+ `ticket.md` in ticket mode) |
| 7 | Previous wave output | `plan.md` on `implementation` and `documentation-final` |
| 8 | Validation error | injected on retry with the failing command's real output |
| 9 | Injections | anything added with `kcia task inject "<text>"` |
| 10 | Wave instruction | `control-plane/waves/prompts/<wave>.md.j2` |
| 11 | Output format | fixed footer |
| 12 | Context budget | only when references were dropped to fit the budget |

**Reference filtering.** Each reference file carries tags (`coding`, `testing`,
`architecture`, …). Each wave declares which tags it wants via `reference_tags` in
`waves.yaml`. The `understanding` wave requests `[coding, monorepo]`; `implementation`
requests `[coding, testing, validation, api, data, web, accessibility]`. Waves with
`reference_tags: []` inject rules but no reference files.

**Context budget.** When the composed prompt would exceed `budget.max_prompt_tokens`
(default 120 000, overridable in `~/.config/kcia/config.yaml` → `preferences.max_prompt_tokens`),
whole reference files are dropped tag-by-tag following `budget.drop_order`. A
`## Context budget` footer lists what was omitted. Guardrails are never truncated.

**Task scope.** `kcia task init --scope packages/api` limits which manifest roots drive
profile resolution, so a task that only touches the API package does not pull in Flutter
profile bundles.

Measured on `tests/fixtures/repos/melos_mono` with one active profile (`backend-dart`):

| Metric | Before optimization | After |
|---|---|---|
| Tokens per task (5 waves) | ~14 834 | ~12 655 (~15% lower) |
| `understanding` wave | ~2 958 | ~2 482 (~16% lower) |

Guardrails are plain Markdown in the control plane — edit them and the next `kcia wave run`
picks up the change with no reinstall.

### 5. How Python talks to the model CLIs

`providers/runner.py` is provider-agnostic. It asks the adapter to build an argv, spawns it
with `subprocess.Popen`, writes the prompt to **stdin** (never as an argument — prompts are
tens of kilobytes), and reads stdout line by line:

```python
cmd = adapter.build_command(req)          # provider-specific argv
process = subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd=req.cwd)
process.stdin.write(req.prompt); process.stdin.close()
for line in process.stdout:               # streamed, not buffered to completion
    events = adapter.parse_stream_line(line, state)
```

`providers/claude.py` builds:

```
claude --print --output-format stream-json --model <model>
       --permission-mode <mode> --verbose --include-partial-messages
       --add-dir <repo> [--effort low|medium|max]
```

`providers/cursor.py` builds the `cursor-agent --print --output-format stream-json`
equivalent. Each adapter translates its own JSON dialect into the **same eight events** —
`TextDelta`, `ToolCallStart`, `ToolCallEnd`, `FileRead`, `FileWrite`, `UsageUpdate`,
`TurnEnd`, `ProviderError`. The wave runner only ever sees those, so there is no
`if provider == "claude"` anywhere outside `providers/`. Adding a provider is a new adapter
module plus a catalog entry; the registry also discovers third-party adapters through the
`kcia.providers` entry point group.

The runner supervises the subprocess while it works:

- **stderr on a separate thread**, so a chatty CLI cannot deadlock the pipe
- **idle timeout** (default 180s without a line) kills the process
- **stuck warning** at 90s of silence
- **reasoning guardrails** — optional caps on tool calls and files read; exceeding one kills
  the run and reports `cancel_reason`
- malformed or non-JSON lines return `[]` rather than raising, so a stray banner cannot
  crash a wave

Tokens, tool-call counts, and the files read and written are accumulated from the events and
persisted to the session.

### 6. Permissions

The wave's `allow_edits` becomes a real restriction on the provider CLI, not a polite
request in the prompt:

| `allow_edits` | Claude Code invocation |
|---|---|
| `false` (planner waves) | `--permission-mode default --disallowed-tools Edit Write NotebookEdit` |
| `true` | `--permission-mode bypassPermissions` |
| `true` with an explicit tool allowlist | `--permission-mode acceptEdits --allowed-tools …` |

So the planner **cannot** modify your code even if the model decides to try.

### 7. Validation is per profile, never global

After `implementation`, kcia builds a validation plan:

1. take the session's active profiles, or resolve them from the touched paths against the
   manifest `roots`
2. expand through the manifest's `dependencies` — touching `packages/shared/**` can require
   validating every profile that consumes it
3. take each profile's `validation.required_commands` and resolve the actual command
   (profile → `command_overrides` → manifest overrides)
4. deduplicate by `(cwd, command)` and order lint before test before build

Each command runs **in its own profile's root**. This is the point: `flutter test` in a pure
Dart package fails, so the test command is resolved per profile and never assumed globally.
On failure only the failing profile is retried, up to 3 times, with the command's real
output injected back into the builder's prompt. Profiles that already passed are never
re-run.

### 8. Everything is auditable

| File | What it tells you |
|---|---|
| `.ai/local/runs/<wave>-NN.prompt.md` | the exact prompt sent, including on each retry |
| `.ai/local/session.json` | per-wave status, agent, provider, model, tokens, tool calls |
| `.ai/context/*.md` | the artifacts the waves produced |
| `.ai/generated/profiles/` | the guidance that was in play, with each piece attributed |

### Token usage

Token counts come from the provider's own `UsageUpdate` events and are accumulated per
wave — including across validation retries, which invoke the provider more than once.

```
$ kcia wave list
1. understanding	completed	planner (claude/claude-opus-5)	18.4k tokens
2. analysis	completed	planner (claude/claude-opus-5)	22.0k tokens
3. documentation-init	pending	planner (claude/claude-opus-5)

total: 40.4k tokens

$ kcia task show
tokens: 40.4k
  input:  36.0k
  output: 4.0k
  cached: 20.0k (read from cache)
tool calls: 43
provider calls: 2
```

kcia does **not** report cost, and cannot report how much of your subscription quota is
left. Neither `claude` nor `cursor-agent` exposes quota or usage limits in a scriptable
way — Claude Code shows it only inside the interactive TUI, and `cursor-agent about`
reports the subscription tier but no consumption. Token counts are the honest ceiling of
what kcia can tell you from headless invocations.

### Known gaps

Being honest about what the code does not yet do:

- **`edit_scope` is not enforced.** Waves declare it (`documentation-init` is meant to be
  confined to `.ai/**`), but the runner does not translate it into a tool restriction — a
  wave with `allow_edits: true` currently gets full write access.
- **Workflow files are not injected** into prompts yet, only references and rules.
- **Sessions are not resumed** across waves — every wave is a fresh invocation, and the
  handoff happens through files.
- **The runner does not track which files the wave actually changed.** It passes the
  repository root as the touched path, so validation falls back to the session's active
  profiles rather than narrowing to the edited ones. The per-path resolution above works
  and is tested; the runner just does not feed it a precise list yet.

## Status

**Implemented and usable**

| Area | Commands / features |
|---|---|
| Project setup | `kcia init` — detection, manifest, bundles, adapters, gitignore |
| Profiles | `profile list/show/detect/validate/scaffold`, inheritance, packs, resolution |
| Agents | `agent set/show/swap` — Claude and Cursor adapters |
| Tasks | `task init/show/inject/abort` — with `--scope` for path-limited profiles |
| Waves | `wave list/run/retry/skip/logs` — session, lock, prompt composition, validation |

**Not yet implemented** — these commands exit 1: `kcia doctor`, `kcia sync`, `kcia ask`,
`kcia branch`, `kcia auth`, `kcia mcp`.

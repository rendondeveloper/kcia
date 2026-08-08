# kcia

**Control plane + CLI** that drives your existing agent tools (Claude Code, Cursor) through a
structured, auditable pipeline — without calling any LLM API directly.

## Requirements

- Python 3.11+
- `git` — required. kcia branches and commits through the `git` binary in your repository.
- `gh` — **optional**, only for `kcia commit --pr` (install: <https://cli.github.com>).
- A provider CLI, **installed and logged in**, for each role you use

kcia never calls an LLM API. It shells out to the CLI you already have, so that CLI — and
its session — is a hard requirement, not an optional integration:

| Provider | Binary | Install | Log in | Check |
|---|---|---|---|---|
| Claude Code | `claude` | `npm i -g @anthropic-ai/claude-code` or `brew install --cask claude-code` | `claude auth login` | `claude auth status` |
| Cursor | `cursor-agent` | install Cursor, enable the CLI from the command palette | `cursor-agent login` | `cursor-agent status` |

The default setup uses **both**: `planner` on Claude Code and `builder` on Cursor. One CLI is
enough only if you point both roles at the same provider (`kcia agent set builder claude …`).
Billing is whatever those subscriptions already cost you — kcia adds none of its own.

**`kcia doctor` answers all of this for your machine**: Python and git, whether each provider
is installed and authenticated (and as whom), whether your configured agents can actually
run, and whether the repository is initialized. Run it first when anything looks wrong.

```
Providers
  ✓ claude: authenticated as you@example.com (pro)
  ! cursor: not installed
      Instala Cursor y habilita el CLI desde la paleta de comandos.

Agents
  ✓ planner: claude/claude-opus-5 (global)
  ✗ builder: cursor/composer-2.5 (global)
      `cursor` is not installed, so this role cannot run.
```

You do not need to remember to run it: `kcia wave run` performs the same check for the
configured roles **before the first wave**, and refuses to start with the install or login
hint. That matters because the builder's provider is not exercised until the fourth wave —
without the upfront check, three planner waves would burn tokens before a missing
`cursor-agent` surfaced.

## Install

One line:

```bash
curl -fsSL https://raw.githubusercontent.com/rendondeveloper/kcia/master/scripts/install.sh | bash
```

That clones kcia to `~/tools/kcia`, builds its virtualenv, installs the CLI, and links a
`kcia` shim into `~/.local/bin`. kcia is installed **from a git clone**, not with `pipx` —
the CLI needs `control-plane/` on disk next to `cli/` (see
[Why not `pipx install`](#why-not-pipx-install)). Keep `~/tools/kcia`: it is your
distribution copy, not a disposable checkout.

Override the defaults with environment variables if you want it elsewhere:

```bash
KCIA_HOME=~/src/kcia KCIA_BIN=~/bin curl -fsSL .../install.sh | bash
```

### Verify

```bash
kcia --version        # e.g. kcia 0.1.0
kcia profile list     # backend-dart, mobile-flutter, web-flutter
```

If `profile list` is empty, the CLI cannot find `control-plane/` — you are running a
`kcia` from somewhere else. Check `which kcia`; it should point at `~/.local/bin/kcia`.
If `~/.local/bin` was not already on your `PATH`, the installer appends it to your shell
rc and tells you to `source` it.

### Configure agents (once)

See what each provider offers, then pick:

```bash
kcia agent models              # every provider, its models, tier and what each is best for
kcia agent models claude       # one provider
kcia agent models --json       # same data, scriptable
kcia agent models --live       # ask the installed CLI and flag stale catalog entries

kcia agent set planner claude --model claude-opus-5
kcia agent set builder cursor --model composer-2.5
kcia agent show
```

Do this **before `kcia init`** — agents are what execute the waves, and `init` closes by
reporting which ones it will use (or how to pick them, when none are set).

Preferences are stored in `~/.config/kcia/config.yaml` and apply to every project unless
overridden per repo — see [Per-project models](#per-project-models).

Note that **Cursor uses its own model ids**, not Anthropic's: `composer-2.5`,
`claude-sonnet-5-thinking-high`, `auto`, and so on — plain `claude-sonnet-5` is not one of
them. `auto` is Cursor's default and lets it pick per request. The catalog in
`control-plane/providers/catalog.yaml` is curated by hand, so `kcia agent models --live`
compares it against `cursor-agent --list-models` and exits non-zero on drift.

### Per-project models

To use different agents in one repository, set them with `--scope repo` from inside it:

```bash
cd /path/to/your/project
kcia agent set planner claude --model claude-opus-5 --scope repo
kcia agent show                # the `origin` column shows repo / global / default
```

Resolution order, highest first:

| Origin | Where |
|---|---|
| `flag` | a `--provider` / `--model` flag on the command being run |
| `repo` | `<project>/.ai/local/agents.yaml` |
| `global` | `~/.config/kcia/config.yaml` |
| `default` | the provider catalog |

Note that `.ai/local/` is gitignored, so a repo-scoped choice is **yours on this machine**
— it does not travel with the repository. There is currently no committed, team-wide way to
pin models for a project; each person runs the `--scope repo` command in their own clone.

## Quickstart — first task in a project

> **Set your agents first.** Steps 1 and 2 are in that order on purpose: the agents are what
> actually run every wave, and they are *not* part of `kcia init`. Skip step 1 and the
> pipeline silently falls back to catalog defaults — you can burn a full run on a model you
> never chose. `kcia init` closes by printing the agents it will use, so check that line
> before starting a task. Already installed? `kcia agent show` tells you where you stand.

Run these from **inside the repository** you want kcia to work on:

```bash
cd /path/to/your/project

# 1. Choose who runs the waves — do this BEFORE init.
kcia agent models                # see what each provider offers
kcia agent set planner claude --model claude-opus-5
kcia agent set builder cursor --model composer-2.5

# 2. Detect technologies, write manifest, generate adapters (idempotent).
kcia init --yes                  # closes by reporting the agents it will use

# 3. Start a task — from a prompt, or from a Jira issue.
kcia task init "fix the overflow on the profile screen"
kcia task init PROJ-123           # fetches the issue; see MCP servers

# Optional: limit which packages drive active profiles.
kcia task init "fix the API" --scope packages/api

# 4. Create the git-flow branch for it (asks for the base branch if it is not obvious).
kcia branch start

# 5. Inspect the pipeline.
kcia wave list

# 6. Run waves one at a time (or omit the wave id to run the next pending).
kcia wave run
kcia wave run understanding
kcia wave run --until analysis
kcia wave run --quiet            # no live progress line (CI, logs)

# 7. Review the plan, then let the build run.
kcia wave approve

# 8. Inspect progress and token usage.
kcia task show
kcia wave logs understanding

# 9. Review the diff, then close the task. Nothing is committed until you confirm.
git diff
kcia commit
```

### When the agent is blocked

Every wave prompt carries a protocol: if continuing would mean guessing, the agent replies
with one line, `BLOCKED: <question>`, and nothing else. The run stops there:

```
Stopped at `understanding` — the agent cannot proceed.

  What is the change request?

Full response: .ai/local/runs/understanding-01.blocked.md
Answer it, then resume:
  kcia task answer "<your answer>"
  kcia wave retry understanding
```

This exists because the alternative is worse than a crash. Without it a planner that says
"I don't have a task to plan yet" is recorded as **completed**, and the remaining four waves
reason on top of the same gap — a measured run burned 12.7k tokens and 14 minutes that way
and touched two unrelated files.

A blocked wave is not a failure: its status is `blocked`, no `error` is recorded, and the
work is kept. The response is written to `.ai/local/runs/` and deliberately **not** to the
wave's context file — a question is not the artifact the wave produces, and putting
`BLOCKED: …` into `task.md` would feed the gap to every later wave. `kcia wave run` refuses
to move on while a wave is blocked, rather than skipping to the next pending one.

Detection is intentionally narrow, since a false positive halts a healthy run: the protocol
marker, plus two shapes agents produced before the protocol existed. Mentioning an open
question, or the word `UNKNOWN` in passing, does not trigger it.

### The approval gate

The three planning waves run unattended, but `kcia wave run` **stops before
`implementation`** — the first wave allowed to touch code outside `.ai/`. It points at the
plan and waits:

```
Paused before `implementation` — the first wave that can change your code.

  Plan: /path/to/your/project/.ai/context/plan.md  (58 lines)

Open it and edit it directly if something is wrong — your changes go
into the builder's prompt. Then:
  kcia wave plan               print it here
  kcia wave approve            approve and continue
  kcia task answer "..."       add context, then re-run the planning wave
  kcia task abort              stop here
```

The plan is plain Markdown at `.ai/context/plan.md`. **Editing it during the pause works**:
prompts are composed when a wave runs, not when the plan was written, so whatever the file
says at `kcia wave approve` time is what the builder gets. That makes correcting a plan
cheaper than re-running the planning waves.

`kcia wave approve` records the decision **and continues the run** — one command, not two.
Use `--no-run` to only record it, `--note` to attach a reason, and `kcia wave plan` to
reprint the plan at any time. The paused run exits with code `2`, distinct from a real
failure (`1`), so scripts can tell "waiting for a human" from "broken".

For unattended runs, `kcia wave run --yes` skips the gate.

The gate is declarative, not hardcoded. It comes from `requires_approval` in
`control-plane/waves/waves.yaml`, so moving it — or adding a second one — is a data edit:

```yaml
  - id: implementation
    requires_approval: true
    approval_shows: plan.md
```

While a wave runs, a live status line reports which agent is working, on which provider and
model, and what it is doing right now:

```
⠹ implementation · builder · cursor/composer-2.5 — writing lib/device_list.dart · 2m14s · 7 tools · 12k tok
implementation · builder · cursor/composer-2.5 — completed (10m12s, 7 tool calls, 2 files written, 13k tokens)
```

`kcia wave run` closes with the total (`All waves completed in 14m35s.`), and `kcia task show`
breaks the time down per wave alongside the tokens.

It is drawn on stderr and refreshed in place, so piping or redirecting stdout is unaffected.
Off a TTY it degrades to one plain line per wave, and `--quiet` turns it off entirely.

`kcia init` writes `.ai/manifest.yaml`, composed profile bundles, provider adapters
(`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`), and adds all generated paths to
`.gitignore`. Run it again after updating kcia — it only rewrites what changed
(`Already up to date` when nothing moved).

Use `--yes` in CI or non-interactive shells. Use `--no-gitignore` if you manage ignore
rules yourself.

Agent configuration (`kcia agent set`) is done once on your machine — see
[Configure agents](#configure-agents-once). Use `--scope repo` to override per repository
(written to `.ai/local/agents.yaml`, gitignored) — see
[Per-project models](#per-project-models).

### Stopping a run

**Ctrl-C.** It stops the provider that is running, not just kcia:

```
⠹ implementation · builder · cursor/composer-2.5 — stopping… · 3m02s · 5 tools · 9k tok

Stopped `implementation`. The provider was terminated and nothing was written.
It is pending again — `kcia wave run` starts it from the top.
```

The wave goes back to **`pending`**, not `failed`: a wave writes its output only after the
full response arrives, so an interrupted one simply never started. `kcia wave run` picks it
up again, and the session lock is released, so nothing is left stuck. The exit code is `130`,
the usual one for "interrupted".

A **second Ctrl-C exits immediately**, even if the provider refuses to die — you are never
trapped waiting for it.

This is polled by the loop reading the provider's output, which is where a run spends
essentially all its time. A provider that streams nothing at all is just as cancellable: its
output is read on a separate thread, so neither the cancel nor the idle timeout can be
blocked by a silent CLI. `kcia task init` on a Jira issue is interruptible the same way.

To throw the whole task away instead of just the running wave:

```bash
kcia task abort        # deletes the session; the files it wrote stay on disk
```

## Git flow: branching and closing a task

**No wave ever runs `git`.** The guardrails block `checkout -b`, `commit` and `push` for the
agents, and leave them read-only (`status`, `diff`, `log`). Branching and committing are two
explicit commands you run, and both stop for your confirmation. That is the point: the run
ends with the changes in your worktree and the decision to keep them still yours.

### What kcia needs to reach *your* git, per project

Nothing to configure, and no credential to hand over. kcia shells out to the `git` binary
**inside that repository**, so the remote, the credential helper, the SSH key, the signing
key and the committer identity are exactly the ones that repo already uses — `git push` from
kcia and `git push` from your shell do the same thing. Concretely, per project you need:

| For | Requirement |
|---|---|
| `kcia branch start`, `kcia commit` | a git worktree (`git init` / a clone) and `user.name` + `user.email` set |
| `kcia commit --push` | a remote (`git remote add origin <url>`) your git can already authenticate to |
| `kcia commit --pr` | the above, plus `gh` installed and `gh auth login` done once |

kcia stores no token and reads no credential. `kcia doctor` reports the branch, the detected
base branch and whether a remote exists.

### Starting the branch

```bash
kcia branch start                    # name and type come from the active task
kcia branch start "add the loader" --type feat
kcia branch start --base develop     # skip the base-branch question
kcia branch base                     # just show what it would branch from
```

Names follow git flow, and carry the Jira key when the task has one:

```
feature/IP-116-add-the-commit-flow
fix/overflow-en-el-header            # no ticket: no key in the name
docs/IP-200-jira-guide
```

**The base branch is asked for, never guessed.** It is the one thing the worktree cannot
tell us — a repo that merges into `develop` looks identical to one that merges into `main`.
The rule:

1. An answer already recorded in `.ai/local/git.yaml` is reused.
2. If you are **on** `develop` / `main` / `master`, that is the base.
3. If exactly one of those exists in the repo, that is the base.
4. Otherwise — several conventions coexist, or none does and you are on a feature branch —
   kcia asks, offering the current branch and each convention it found:

```
Cannot tell which branch to start from — the current branch is not a base branch
and the repository has more than one candidate.

  1. develop
  2. main
  3. feature/x  (current)

Start from which branch? (number, or type a branch name) [1]:
```

The answer is remembered for that repository, so it is asked once. Off a TTY (CI) it does
not guess: it exits and tells you to pass `--base`.

### Closing the task

```bash
kcia commit                  # show the commits, then confirm
kcia commit --dry-run        # show them and stop
kcia commit "subject" --type fix
kcia commit --single         # one commit instead of two
kcia commit --push --pr      # push, then open the PR with gh
```

It prints exactly what it is about to write — messages **and** the files in each commit —
and writes nothing until you answer `y`:

```
On branch feature/IP-116-add-the-commit-flow:

  Commit 1 (plan)
    docs: IP-116 - plan — add the commit flow
      .ai/context/plan.md
      .ai/context/decisions.md

  Commit 2 (code)
    feat: IP-116 - add the commit flow
      cli/src/kcia/git/commit.py
      tests/test_git_commit.py

Commit this? [y/N]:
```

**The plan gets its own commit, first.** It is the record of *why* the code changed;
keeping it in a separate `docs:` commit means a reviewer can read the intent without digging
it out of the diff. `--single` collapses both into one.

Message format — type, the issue key when there is one, then the subject:

```
feat: IP-116 - add the commit flow
fix: header overflow                  # no ticket: nothing is prefixed
docs: IP-200 - jira guide
```

The types are `feat`, `fix` and `docs`, and nothing else. In ticket mode the key comes from
the task, so you never retype it; `--ticket IP-9` overrides it and `--no-ticket` drops it.
When there is no ticket **no placeholder is invented** — an issue key that does not exist is
worse than no key at all. The type is inferred from the subject and the changed files, and
`--type` always wins.

Two things never reach a commit: `.ai/local/`, `.ai/cache/` and `.ai/generated/` (regenerable
output, gitignored), and whatever you happened to have staged for an unrelated reason — kcia
resets the index and stages each commit's files explicitly.

## Updating

New versions land on `master`. Update **in your clone** (`~/tools/kcia`), never inside the
projects you work on.

### Routine update

Same one line as the install:

```bash
~/tools/kcia/scripts/install.sh update
```

Or, if the clone is gone or broken, from the network again:

```bash
curl -fsSL https://raw.githubusercontent.com/rendondeveloper/kcia/master/scripts/install.sh | bash -s update
```

The updater does a `git reset --hard origin/master` in the clone. That is intentional: the
clone is a distribution copy, not a place to edit — local changes there are discarded. Your
own projects are untouched.

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
rm -rf ~/tools/kcia/.venv
~/tools/kcia/scripts/install.sh update
```

The installer rebuilds the virtualenv when it is missing.

In a project where generated output looks stale:

```bash
cd /path/to/your/project
rm -rf .ai/generated .ai/cache
kcia init --yes
```

Never delete `.ai/manifest.yaml` or `.ai/profiles/` — those are yours.

## MCP servers

Enable an MCP server per repository. kcia declares it and hands it to the right agent; the
**provider CLI owns the login** — kcia never stores credentials.

```bash
kcia mcp catalog                 # servers kcia knows about
kcia mcp add atlassian           # enable it here
kcia mcp list --role builder     # what one role can actually see
kcia mcp remove atlassian
```

`kcia mcp add` prints the login command for that server.

### Adding Jira, end to end

Requires **Atlassian Cloud** (`*.atlassian.net`). The official remote MCP server does not
support Server or Data Center; for those you would need a third-party MCP with an API token.

```bash
# 1. Enable it for this repository.
cd /path/to/your/project
kcia mcp add atlassian

# 2. Log in — kcia stores no credentials, the provider CLI owns the session.
claude mcp add --transport sse atlassian https://mcp.atlassian.com/v1/sse
cursor-agent mcp login atlassian          # only if a role runs on Cursor

# 3. Verify the server answers before relying on it.
claude mcp list
cursor-agent mcp list-tools atlassian

# 4. Confirm what each role will see.
kcia mcp list --role planner              # atlassian
kcia mcp list --role builder              # hidden from builder
```

Step 3 matters: the Atlassian remote server is still evolving and its URL has changed
before. If `claude mcp list` does not show it as connected, check Atlassian's current
documentation and update `url` in `control-plane/mcp/catalog.yaml` — it is a one-line data
edit, not a code change.

#### Working from a ticket instead of a prompt

Once Jira is connected, an issue key replaces the prompt text and the rest of the pipeline
is identical:

```bash
kcia task init PROJ-123          # instead of: kcia task init "fix the overflow"
kcia wave run
```

`task init` fetches the issue and writes it to `.ai/context/ticket.md`, which is injected
into every wave of a ticket task:

```
Task t_47ca41f5c049 initialized in ticket mode.
Fetching PROJ-123…
Wrote /path/to/your/project/.ai/context/ticket.md
```

From there the waves receive the real request — summary, description, acceptance criteria —
not just the key, so `understanding` and `analysis` work exactly as they do from a prompt.

| Command | What it does |
|---|---|
| `kcia task init PROJ-123` | start from an issue and fetch its body |
| `kcia task init PROJ-123 --no-fetch` | start from the key alone, no provider call |
| `kcia task fetch` | re-fetch the current task's issue after it changed in Jira |

The fetch is one short read-only invocation of the **planner's** provider CLI with the
Atlassian MCP attached — kcia still never calls the Atlassian API itself, and the
credentials stay with that CLI. It cannot edit your repository.

If the fetch fails — no server enabled, no access to the issue, provider down — the task is
still created and kcia says so rather than continuing quietly:

```
warning: could not fetch PROJ-123 — issue not found
The task is still created. Paste the issue into .ai/context/ticket.md,
or the waves will only receive the key.
```

Writing `.ai/context/ticket.md` by hand is always a valid substitute; the fetch is a
convenience, not a dependency.

##### Turning ticket mode on

`kcia mcp add atlassian` is enough: with the server enabled, an argument shaped like an
issue key (`IP-116`) is read as a ticket. `--prompt` and `--ticket` force either mode.

To restrict which keys count as tickets, or to use ticket mode without the MCP, declare
them in `.ai/manifest.yaml`:

```yaml
integrations:
  jira:
    enabled: true
    base_url: https://your-site.atlassian.net
    project_keys: [PROJ, INFRA]
```

Those edits survive `kcia init` — the manifest keeps whatever `integrations` block it
already had. With it enabled, an argument matching a declared project key is classified as
a ticket rather than a free-form prompt, and the task statement records the key.

This only controls *classification*: with `project_keys: [PROJ]`, a bare `PROJ-123` is read
as an issue rather than as prompt text. You can always force either mode with `--ticket` or
`--prompt`.

#### What the agent may do with Jira

The guardrails shipped in `control-plane/guardrails/policies.yaml` allow reading issues and
comments, and forbid commenting and transitioning:

```yaml
jira:
  allow_read: true
  allow_read_comments: true
  allow_comment: false
  allow_transition: false
```

On Claude Code these are **enforced**, not just requested. The catalog lists the exact
read-only tools kcia pre-approves, and Claude denies every MCP call that is not on that
list. Granting the server as a whole (`mcp__atlassian`) would have pulled in
`createJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue` and `transitionJiraIssue`, so
the entries are enumerated one by one and a test fails if a write tool ever appears among
them.

The allowlist is also what makes MCP work at all: in `--print` mode Claude denies every
MCP call unless the tool is named, which is why an agent with the server attached but no
allowlist reports that it "was not granted Jira access".

On Cursor there is no equivalent per-run restriction, so there the guardrails remain
policy and the Atlassian account's own permissions are the real boundary.

### Per-role gating, and where it is real

The catalog declares which agent roles may use each server. Atlassian is `planner` only:
reading issues belongs to understanding and planning, not to the wave that writes code.

How well that is enforced differs by provider, and the difference is worth knowing:

| Provider | Mechanism | Gating |
|---|---|---|
| Claude Code | `--mcp-config <file>` per invocation, plus `--strict-mcp-config` | **Enforced** — each wave gets a config containing only its role's servers, and globally registered servers are ignored |
| Cursor | reads `.cursor/mcp.json` for the whole repository | **Declarative only** — there is no per-run override, so a builder wave on Cursor can still reach an enabled server |

So a planner-only server is genuinely unreachable from the builder when the builder runs on
Claude Code, and is only a convention when it runs on Cursor. Treat the catalog's `roles`
as a real boundary on Claude and as documentation on Cursor.

Enablement lives in `.ai/mcp.yaml` and, for Cursor, `.cursor/mcp.json`. Both are gitignored
— `.cursor/mcp.json` because a server entry may carry `headers` with a token — so enabling a
server is per-machine, like authentication itself.

## Uninstall

```bash
~/tools/kcia/scripts/install.sh uninstall
```

That removes the clone and its venv, the `kcia` shim, `~/.config/kcia` (agent preferences),
`~/.local/share/kcia` (installed profile packs), and the `PATH` line it added. The `.ai/`
directories in your projects are left alone.

Publishing a new version (maintainers only): [RELEASING.md](RELEASING.md).

## Why not `pipx install`

`pipx install ./cli` and
`pipx install "git+https://github.com/rendondeveloper/kcia.git#subdirectory=cli"` both
install the CLI but produce a **broken runtime**. `control-plane/` lives outside `cli/`, so
no Python build backend can bundle it into the wheel; `control_plane_root()` then resolves
to a path that does not exist and every command that needs profiles, waves, roles,
guardrails, or the provider catalog comes up empty.

Packaged installs will be supported once `kcia sync` lands (see [Status](#status)).

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
| `.ai/context/project.md` | project facts injected into every prompt — **yours to edit** |
| `CLAUDE.md`, `AGENTS.md` | adapters listing active profiles and authority order |
| `.cursor/rules/NN-<id>.mdc` | one Cursor rule per profile, with `globs` scoped to that profile's roots |

All of it is gitignored and regenerable — rerun `kcia init` any time.

`project.md` is the one exception to "regenerable": `init` seeds it with facts it can verify
— stack and SDK, platforms, key dependencies, entry point and source directories (or, for a
workspace, its member packages) — and then **never overwrites it**, because it is the place
to add what only you know. `kcia init --refresh-context` regenerates it, discarding your
edits.

It is deliberately small (capped at ~400 tokens) and deliberately factual. Interpretation —
what the domain is, what each file does — is left out: it ages badly, nobody updates it, and
the agent reads the code anyway. The generated block for a real single-package Flutter app
costs about 70 tokens:

```markdown
## Summary
ReaderGps — single Flutter project, Dart SDK ^3.9.2.
Platforms: linux, macos, web, windows.

## Key dependencies
flutter_libserialport, permission_handler, device_info_plus

## Source layout
- `lib/main.dart` — entry point
- `lib/`: models, services, widgets
- `test/` — tests
```

It is injected into **all five waves** rather than filtered per wave. That is on purpose:
measured on a real run, the planner waves share a ~2.070-token prompt prefix and the builder
waves share ~1.588, and `project.md` sits inside both. Varying it per wave would break that
shared prefix — costing more in lost prompt caching than the omitted text would save. If it
ever grows past its budget, the right move is to split it into tagged references and reuse
the existing `reference_tags` filtering, not to invent a second mechanism.

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
| 9 | Injections | anything added with `kcia task answer "<text>"` |
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
| Tasks | `task init/show/fetch/answer/abort` — `--scope` for path-limited profiles, Jira issues fetched into context |
| Waves | `wave list/run/approve/plan/retry/skip/logs` — session, lock, prompt composition, validation |
| Diagnostics | `doctor` — toolchain, provider install and auth, agent and repo readiness |
| MCP | `mcp catalog/add/remove/list` — per-repo servers with per-role gating |
| Git | `branch start/base`, `commit` — git-flow branching and the confirmed commits that close a task |

**Not yet implemented** — these commands exit 1: `kcia sync`, `kcia ask`, `kcia auth`.

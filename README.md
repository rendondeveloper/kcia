# kcia

Control plane and CLI for development agents (Claude Code, Cursor, and future providers).

## Concepts

- **Profiles** — extensible data packages that declare detection rules, commands, and coding guidance per technology.
- **Agents** — two roles (`planner`, `builder`), each mapped to a `(provider, model)` pair.
- **Waves** — five sequential pipeline steps from understanding through documentation.

## Requirements

- Python 3.11+
- `git`
- At least one provider CLI: `claude` (Claude Code) or `cursor-agent` (Cursor)

## Install

You install kcia **once, globally**. You do not install it into each project you work
on — see [Where kcia lives](#where-kcia-lives) below.

**1. Clone the repository.** Keep the clone; it is not disposable (see
[Why not `pipx install`](#why-not-pipx-install)).

```bash
git clone https://github.com/rendondeveloper/kcia.git ~/tools/kcia
cd ~/tools/kcia
```

**2. Create the virtualenv and install the CLI in editable mode.**

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
```

**3. Put the CLI on your PATH.**

```bash
echo 'export PATH="$HOME/tools/kcia/.venv/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**4. Verify.**

```bash
kcia --version        # kcia 0.1.0
kcia profile list     # backend-dart / mobile-flutter / web-flutter
```

If `profile list` prints nothing, the CLI cannot find `control-plane/` — you are running
a copy installed outside the clone. Redo step 2 from inside the clone.

### Why not `pipx install`

`pipx install ./cli` and `pipx install "git+https://github.com/rendondeveloper/kcia.git#subdirectory=cli"`
both install the CLI but produce a **broken runtime**. `control-plane/` lives outside
`cli/`, so no Python build backend can bundle it into the wheel; `control_plane_root()`
then resolves to a path that does not exist and every command that needs profiles,
waves, roles, guardrails, or the provider catalog comes up empty.

Keep the clone. The editable install above keeps `control-plane/` on disk where the CLI
can find it. Packaged installs will be supported once `kcia sync` lands (see Status).

## Updating

New versions are published to `master`. Updating is done **in your clone**, never in the
projects you use kcia on.

```bash
cd ~/tools/kcia

# 1. Take master exactly as published, discarding local changes.
git fetch origin
git reset --hard origin/master
git clean -fd

# 2. Reinstall so the CLI picks up the new code.
.venv/bin/pip install -e "./cli[dev]" --force-reinstall --no-deps

# 3. Confirm.
kcia --version
kcia profile list
```

`git reset --hard` discards anything you changed inside the clone. That is intended: the
clone is a distribution copy, not a place to edit. Your own projects are untouched.

Check `CHANGELOG.md` after updating. Two versions move independently: the CLI version
(`kcia --version`) and the control plane (`control-plane/VERSION` — profiles, waves,
templates). If the control plane changed, re-run detection in each project to pick up the
new guidance:

```bash
cd /path/to/your/project
kcia profile detect
```

### Full reset

If the CLI still misbehaves after updating, rebuild the environment from scratch:

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
kcia profile detect
```

Never delete `.ai/manifest.yaml` or `.ai/profiles/` — those are yours and are committed.

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

Inside a project you work on, everything kcia generates is **regenerable output and is
not committed**. `kcia init` adds it to that project's `.gitignore` for you:

```gitignore
# kcia — generated, do not commit
.ai/*
!.ai/profiles/
CLAUDE.md
AGENTS.md
.cursor/rules/
```

So a teammate cloning your project sees no kcia files at all; they run `kcia init` once
and get their own. Nothing to configure, nothing to keep in sync by hand.

The single exception is `.ai/profiles/` — profiles you write for that repo are *source*,
not output, so they stay tracked and travel with the project. That is what the `!` line
is for.

Nothing from kcia's own dependency tree is installed into your project, and your project
needs no Python.

## Quickstart

Run kcia from inside the project you want it to work on:

```bash
cd /path/to/your/project

kcia init                                        # detect, generate, gitignore
kcia agent set planner claude --model claude-opus-5
kcia agent set builder cursor --model claude-sonnet-5
kcia agent show

kcia task init "fix the overflow on the profile screen"
kcia wave list
kcia wave run                                    # runs the next pending wave
```

`kcia init` detects the technologies in the repository, writes `.ai/manifest.yaml`, the
composed profile bundles, and the provider adapters (`CLAUDE.md`, `AGENTS.md`,
`.cursor/rules/*.mdc`), and adds all of it to `.gitignore`. It is idempotent: run it again
after updating kcia and it rewrites only what changed. Use `--yes` in CI or non-interactive
shells, and `--no-gitignore` if you would rather manage the ignore rules yourself.

`kcia agent set` without `--scope repo` writes to `~/.config/kcia/config.yaml` and applies
to every project. Use `--scope repo` to override the choice for one repository only
(written to `.ai/local/agents.yaml`, gitignored).

## Status

Implemented and usable: `kcia init` (detection, manifest, bundles, adapters, gitignore),
profiles (detection, inheritance, packs, resolution), providers (Claude and Cursor
adapters, agent configuration), and the wave engine (session, lock, prompt composition,
multi-profile validation).

Not yet implemented — these commands exit 1: `kcia doctor`, `kcia sync`, `kcia ask`,
`kcia branch`, `kcia auth`, `kcia mcp`.

# kcia

Control plane and CLI for development agents (Claude Code, Cursor, and future providers).

## Concepts

- **Profiles** — extensible data packages that declare detection rules, commands, and coding guidance per technology.
- **Agents** — two roles (`planner`, `builder`), each mapped to a `(provider, model)` pair.
- **Waves** — five sequential pipeline steps from understanding through documentation.

## Install

You install kcia **once, globally**. You do not install it into each project you work
on — see [Where kcia lives](#where-kcia-lives) below.

```bash
git clone https://github.com/<owner>/kcia.git ~/tools/kcia
cd ~/tools/kcia
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
```

Put the CLI on your PATH:

```bash
# ~/.zshrc
export PATH="$HOME/tools/kcia/.venv/bin:$PATH"
```

Verify:

```bash
kcia --version   # kcia 0.1.0
```

### Why not `pipx install`

`pipx install ./cli` and `pipx install "git+https://github.com/<owner>/kcia.git#subdirectory=cli"`
both install the CLI but produce a **broken runtime**. `control-plane/` lives outside
`cli/`, so no Python build backend can bundle it into the wheel; `control_plane_root()`
then resolves to a path that does not exist and every command that needs profiles,
waves, roles, guardrails, or the provider catalog comes up empty.

Keep the clone. The editable install above keeps `control-plane/` on disk where the CLI
can find it. Packaged installs will be supported once `kcia sync` lands (see Status).

## Updating

New versions are published to `master`. Update by resetting your clone to it:

```bash
cd ~/tools/kcia
git fetch origin
git reset --hard origin/master
.venv/bin/pip install -e "./cli[dev]" --force-reinstall --no-deps
kcia --version
```

Nothing to do in the projects you use kcia on. If the control plane changed (profiles,
waves, templates), re-run `kcia profile detect` there to pick it up.

Full reset instructions and the maintainer publish steps: [RELEASING.md](RELEASING.md).

## Where kcia lives

| | Location | Committed? |
|---|---|---|
| The CLI and control plane | your clone, e.g. `~/tools/kcia` | separate repo |
| Agent preferences (provider, model, effort) | `~/.config/kcia/config.yaml` | no — global to you |
| Installed profile packs | `~/.local/share/kcia/packs/` | no |
| Per-repo state | `<your project>/.ai/` | partly — see below |

Inside a project you work on, kcia writes:

- `.ai/manifest.yaml` — active profiles and their roots. **Commit this.**
- `.ai/profiles/` — profiles specific to that repo. **Commit this.**
- `.ai/local/` — session state and repo-scoped agent overrides. **Gitignore this.**
- `.ai/generated/`, `.ai/cache/` — regenerable output. **Gitignore these.**

Nothing from kcia's own dependency tree is installed into your project, and your project
needs no Python.

## Quickstart

Run kcia from inside the project you want it to work on:

```bash
cd /path/to/your/project

kcia profile detect                              # what technologies are here
kcia agent set planner claude --model claude-opus-5
kcia agent set builder cursor --model claude-sonnet-5
kcia agent show

kcia task init "fix the overflow on the profile screen"
kcia wave list
kcia wave run                                    # runs the next pending wave
```

`kcia agent set` without `--scope repo` writes to `~/.config/kcia/config.yaml` and applies
to every project. Use `--scope repo` to override the choice for one repository only
(written to `.ai/local/agents.yaml`, gitignored).

## Status

Implemented and usable: profiles (detection, inheritance, packs, resolution), providers
(Claude and Cursor adapters, agent configuration), and the wave engine (session, lock,
prompt composition, multi-profile validation).

Not yet implemented — these commands exit 1:

- `kcia init` — so `.ai/manifest.yaml` must be written by hand for now. Without it,
  `kcia profile explain` and the `implementation` wave's validation step will not run.
- `kcia doctor`, `kcia sync`, `kcia ask`, `kcia branch`, `kcia auth`, `kcia mcp`.

No adapters (`CLAUDE.md`, `.cursor/rules/*.mdc`) are generated yet; the templates exist
but nothing renders them.

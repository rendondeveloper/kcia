# Local skill catalog (commons + per-profile) and `kcia skill`

Follow-up to the previous draft. Invocation is **not** `kcia backend --deploy`. Everything goes through **`kcia skill`**, with the profile as a flag, because the same shortcut can exist on mobile and on backend.

## Decisions recorded from the user

These replace earlier guesses. They are binding for the plan.

1. **Catalog file + gitignore.** New files that belong in gitignore follow the **same pattern as `.ai/manifest.yaml`**: a named file under `.ai/`, listed in `GITIGNORE_ENTRIES` in `cli/src/kcia/commands/init.py`, written into the managed gitignore block by `kcia init`, asserted in `tests/test_init.py` the same way as the manifest. Path: **`.ai/skills.yaml`**. Not under `.ai/local/` (that is already a blanket ignore). Not committed. A teammate clones, runs `kcia init`, then registers their own skills.
2. **One command family.** All register/run traffic is `kcia skill …`. It is **not** wired to `kcia work` / `kcia done` and has **no required order**. It can run whenever the terminal is free and the project has the skill registered.
3. **Profile is a flag on `kcia skill`.** Required when the shortcut exists in more than one catalog: `kcia skill --backend --deploy` vs `kcia skill --mobile --deploy`.
4. **Unique shortcut may omit the profile.** If `mote_kill` (or any shortcut) is registered in only one namespace, `kcia skill --mote_kill` is enough. If the same shortcut exists on two namespaces, omitting the profile is an error: print the collision and tell the user to pass `--backend` / `--mobile` / `--commons`.
5. **Register with `--path` or a name**, always scoped by profile when adding to a profile catalog:
   - `kcia skill --backend --path <file-or-dir>`
   - `kcia skill --backend <name>`
   Search the project (and only add if found). Path missing or name not found → notify, do not write.
   **Both name and shortcut may be passed together** (e.g. `kcia skill --backend deploy_staging_services --deploy` or `kcia skill --backend --path … --deploy`).
6. **Overwrite is an error.** Registering onto an existing namespace+shortcut does not replace; print that it is already registered and exit 1. Use remove, then add.
7. **`kcia skill --backend` with no other skill args lists** the shortcuts registered under that namespace (v1).
8. **Remove is in v1.** Same command family: `kcia skill --backend --deploy --remove` (reserved flag `--remove`). Unknown shortcut → error, catalog unchanged.
9. **Every catalog entry always stores three fields:** `shortcut`, `name`, `path`. The path is what is persisted (project path or an explicit personal `--path`). Name search that misses in the project but hits `~/.cursor/skills/<name>` **hints that path** and does not add until the user passes `--path`.
10. **Builder policy.** `allow_edits: true` (same as implementation). Secrets: inherit the user environment; kcia does not inject secrets.
11. **Extra argv on run is forwarded** into the job prompt: `kcia skill --backend --deploy -- production`.
12. **Result of a run (Q5 = B).** Always print builder stdout (and keep the run log under `.ai/local/runs/`). Parse that stdout:
    - `BLOCKED: <reason>` → exit **2** (same detector as waves).
    - `SKILL_OK:` present, provider exit 0, no `BLOCKED:` → success, exit **0**.
    - Provider exit 0 but **no** `SKILL_OK:` → failure, exit **1** (the model talked but did not confirm).
    - Provider non-zero → failure, exit **1**.
    Canonical positive marker is **`SKILL_OK:`** (not `DEPLOY_OK:`). The job prompt must tell the builder to emit exactly one of `SKILL_OK:` or `BLOCKED:`. Do not copy output into `.ai/context/`.

## Analysis

### What already exists (unchanged facts)

| Piece | Today |
|---|---|
| Control-plane skill catalog | `control-plane/skills/catalog.yaml` — wave-prompt skills only. Do not put user skills there. |
| Profiles in this pack | `backend-dart`, `mobile-flutter`, `web-flutter` (concrete); `_dart-core` (abstract). |
| CLI | Fixed Typer commands in `cli/src/kcia/main.py`. **No** `skill` command yet. |
| Progress | `WaveProgress` for provider runs; `StepProgress` for named CLI steps. |
| Gitignore pattern | `GITIGNORE_ENTRIES` includes `.ai/manifest.yaml`. `_update_gitignore` rewrites the managed block. Tests import the tuple and assert the written `.gitignore` contains it. |

### Catalog file (decided)

`.ai/skills.yaml`, schema versioned, gitignored like the manifest.

```yaml
schema_version: 1
commons:
  generate_code:
    name: generate_code_app_mobile_android
    path: .cursor/skills/generate_code_app_mobile_android/SKILL.md
profiles:
  backend:
    deploy:
      name: deploy_staging_services
      path: .cursor/skills/deploy_staging_services/SKILL.md
    mote_kill:
      name: mote_kill
      path: .cursor/skills/mote_kill/SKILL.md
  mobile:
    generate_code:
      name: generate_code_app_mobile_android
      path: .cursor/skills/generate_code_app_mobile_android/SKILL.md
```

Map key = **shortcut**. Each value always has **name** + **path**. No entry without a path.

- **commons** — skills that are not tied to a profile (`kcia skill --commons --generate_code`).
- **profiles.&lt;family&gt;** — `backend` / `mobile` / `web` from the first segment of the concrete profile id (`backend-dart` → `backend`).
- Shortcut keys must match `^[a-z][a-z0-9_]*$` so they are legal flags (`--deploy`, `--mote_kill`). Click/Typer also accept `--mote-kill`.

`kcia init` adds `.ai/skills.yaml` to `GITIGNORE_ENTRIES`. It does **not** need to create an empty file; first successful `kcia skill --backend --path …` creates it.

Reserved flags that must not be shortcuts: `backend`, `mobile`, `web`, `commons`, `path`, `remove`, `help`, plus any real Typer options added later (`force`, `yes`, …). Namespace flags, `--path`, and `--remove` always win over a skill of the same name.

### CLI shape (decided)

One Typer command `skill` with `allow_extra_args` / a custom parser (same spirit as `WorkGroup`), because shortcuts are **dynamic flags** from the catalog, not compile-time options.

**Register (write the catalog):**

```text
kcia skill --backend --path .cursor/skills/deploy_staging_services --deploy
kcia skill --backend deploy_staging_services --deploy
kcia skill --backend --path .cursor/skills/deploy_staging_services
kcia skill --backend deploy
kcia skill --commons --path .cursor/skills/generate_code_app_mobile_android --generate_code
```

- `--backend` / `--mobile` / `--web` / `--commons` selects the namespace. For register, the profile (or `commons`) is **required**.
- `--path` → resolve file or directory containing `SKILL.md`; `StepProgress` while validating. Missing → message, exit 1, no write.
- Positional **name** (no `--path`) → search the project for that skill name (`StepProgress`). Not found → notify, do not add. If `~/.cursor/skills/<name>` exists, **print that path as a hint** and still do not add until `--path` is passed. Several project hits → list them, require `--path`.
- **Shortcut and name are both allowed.** If only a path/name is given and it is already a legal short token, that token is the shortcut **and** the name. If the name is long, pass the shortcut flag too (`--deploy`). If only `--deploy` is passed with `--path`, shortcut is `deploy` and name is taken from the directory / frontmatter.
- Same namespace+shortcut already present → **error**, do not overwrite.

**List:**

```text
kcia skill --backend
kcia skill --commons
kcia skill
```

- `--backend` / `--mobile` / `--web` / `--commons` alone → print shortcut, name, path for that namespace.
- `kcia skill` with no flags → print every namespace that has entries.

**Remove:**

```text
kcia skill --backend --deploy --remove
kcia skill --mote_kill --remove
```

Unique shortcut may omit the profile, same rule as run. Missing entry → error.

**Run (builder job, spinner, wait):**

```text
kcia skill --backend --deploy
kcia skill --mobile --generate_code
kcia skill --mote_kill                  # only when that shortcut exists in exactly one namespace
```

- Resolve shortcut → catalog entry → skill file.
- If the shortcut exists in **more than one** namespace and no `--backend`/`--mobile`/`--web`/`--commons` was passed: exit 1, name the collisions, do not call the builder.
- If the shortcut is unknown: exit 1, do not call the builder.
- Namespace not in this repo's manifest (e.g. `--backend` on a mobile-only app): exit 1 for both register and run.
- Independent of waves: **does not** require an active task, completed waves, or `kcia done`. **Does** need a free terminal: if the session lock is held (a wave is using the terminal), refuse with the same "another wave is running" style message; otherwise run now.
- `check_agents_ready` for **builder** only.
- `WaveProgress` (`skill:backend/deploy`), wait until the provider exits. Remaining argv after the shortcut is appended to the job prompt.
- Always print stdout. Then parse markers: `BLOCKED:` → exit 2; `SKILL_OK:` + exit 0 → success; missing `SKILL_OK:` or provider non-zero → exit 1. Job prompt requires the builder to emit `SKILL_OK:` or `BLOCKED:`.

Not a wave: not in `kcia work list`, no approve gate.

### Search (register by name)

Roots, project first:

1. `<repo>/.cursor/skills/<name>/SKILL.md`
2. `<repo>/.cursor/skills/<name>.md`
3. `<repo>/.claude/skills/<name>/SKILL.md` if that layout exists

`--path` may point at a personal skill (`~/.cursor/skills/…`) if the user passes it explicitly; that absolute path is then stored. Name search does **not** silently register a personal skill; on a project miss it may **hint** the home path.

### Distinguishing register vs run

Same command, different argv shape:

| Argv | Action |
|---|---|
| `--path` present (and not `--remove`) | **register** that path into the given namespace |
| positional token that is not a flag | **register** by name (search) |
| `--<shortcut>` and `--remove` | **remove** that catalog entry |
| `--<shortcut>` in the catalog, no `--path`/`--remove` | **run** |
| `--<shortcut>` not in the catalog, no `--path`/name | error: unknown skill; hint register |
| namespace flag only, or no flags | **list** |

### Backend workflow hint

Optional one-liner only in `control-plane/profiles/backend-dart/workflows/`: register with `kcia skill --backend --path …` and run with `kcia skill --backend --deploy`. Not in mobile/web/`_dart-core`. Not mentioned in `kcia work` prompts (user: not linked to other commands).

### What this is not

- Not `kcia backend --deploy` (superseded).
- Not a sixth wave, not a third agent.
- Not `control-plane/skills/catalog.yaml`.
- Not ordered after `kcia work` / `kcia done`.

## Open questions

None. Q5 = **B**: print stdout; require `SKILL_OK:`; `BLOCKED:` → exit 2; missing `SKILL_OK:` is failure.

## Proposed plan

Do not implement until this plan is confirmed.

1. **Gitignore**
   - Add `.ai/skills.yaml` to `GITIGNORE_ENTRIES` next to `.ai/manifest.yaml`.
   - Extend `test_init_gitignores_everything_it_generates` (tuple subset, same pattern as the manifest).
2. **Catalog module** (`cli/src/kcia/skills/`)
   - Pydantic schema, load/save `.ai/skills.yaml`, family from profile id, shortcut uniqueness across namespaces, reserved-flag checks.
   - Finder with `StepProgress`; never add on miss.
3. **CLI `kcia skill`**
   - Register in `main.py`. Custom parsing: namespace flags, `--path`, positional name, dynamic shortcut flags.
   - Register / list / remove / run as in the table above.
   - Unique shortcut → run/remove without profile; collision → require `--backend` / `--mobile` / …
   - Duplicate namespace+shortcut on register → error.
   - English messages; spinner on search and on builder.
4. **Builder job**
   - Reuse `RunRequest` / `call_provider`; `WaveProgress`; wait.
   - Prompt: execute the skill file; extra argv; end with `SKILL_OK:` or `BLOCKED:`.
   - Print stdout. `detect_blocked` first; else require `SKILL_OK:`; else fail.
   - Independent of session waves except the lock (terminal busy).
5. **Backend workflow hint (data only)** — `kcia skill --backend …` wording; no other profiles.
6. **Tests**
   - Help lists `skill`.
   - Gitignore contains `.ai/skills.yaml`.
   - Register by path found / missing; register by name found / missing.
   - `--backend` on a repo without backend → exit 1.
   - Run `kcia skill --backend --deploy` → builder + progress; extra argv in prompt.
   - Stdout with `SKILL_OK:` → exit 0; `BLOCKED:` → exit 2; neither → exit 1.
   - Unique `--mote_kill` without profile → runs that one entry.
   - Same shortcut on backend and mobile without profile → exit 1, no provider.
   - Re-register same shortcut → exit 1, catalog unchanged.
   - Remove existing / missing.
   - List `--backend` and bare `kcia skill`.
   - Lock held → refuse.
7. **Docs** — README: register, run with and without profile, gitignore like the manifest, not a wave.
8. **Version** — **minor** `0.17.0` → `0.18.0`.

## Semver (mandatory after implementation)

- Current before implementation: `0.17.0`
- Implemented: **minor** `0.18.0` — new `kcia skill` catalog and command; the five-wave pipeline is unchanged.

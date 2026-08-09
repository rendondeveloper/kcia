# Publishing a new version

Maintainers only. Everything ships from `master`: there is no build, no package
registry, no tags to manage — you push, users pull.

Users install and update from the [README](README.md#install); do not duplicate those
steps here.

```bash
# 1. Bump the version in ONE place:
#      cli/src/kcia/__init__.py     VERSION = "X.Y.Z"
#    cli/pyproject.toml derives from it (dynamic version).
#
#    Changed only control-plane/ ? Bump control-plane/VERSION instead.

# 2. Add an entry at the top of CHANGELOG.md.

# 3. Verify.
.venv/bin/pytest
.venv/bin/kcia --version

# 4. Push to master.
git add -A
git commit -m "release: X.Y.Z"
git push origin master
```

That is the whole release. `master` is the version users get.

## Which digit to bump

- **patch** — bug fix, no behavior change for existing manifests or profile packs.
- **minor** — new command, new predicate, new provider, new builtin profile.
- **major** — a `schema_version` changes (profile, pack, manifest, or session), or a
  command is removed or changes its output contract.

## Compatibility

- **Never lower** `kcia_min_version` in `control-plane/profiles/pack.yaml`. Raise it in
  the same commit that introduces the CLI feature the pack relies on, so an old CLI
  refuses the pack with a clear message instead of failing obscurely
  (`profiles/loader.py` enforces this).
- Renaming or removing a profile id is breaking: existing `.ai/manifest.yaml` files
  reference ids by name. Say so in the CHANGELOG and give the migration path.

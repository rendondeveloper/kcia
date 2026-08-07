# Publishing a new version

Everything ships from `master`. There is no build, no package registry, no tags to
manage — you push, the user pulls.

## Maintainer: publish

```bash
# 1. Bump the version in ONE place:
#      cli/src/kcia/__init__.py     VERSION = "X.Y.Z"
#    pyproject.toml derives from it (dynamic version).
#
#    Changed only control-plane/ ? Bump control-plane/VERSION instead.

# 2. Add a line at the top of CHANGELOG.md.

# 3. Verify.
.venv/bin/pytest
.venv/bin/kcia --version

# 4. Push to master.
git add -A
git commit -m "release: X.Y.Z"
git push origin master
```

That is the whole release. `master` is the version users get.

## User: update and reset the CLI

Run this in your kcia clone whenever a new version is published:

```bash
cd ~/tools/kcia

# 1. Discard local changes and take master exactly as published.
git fetch origin
git reset --hard origin/master
git clean -fd

# 2. Reinstall the CLI so it picks up the new code.
.venv/bin/pip install -e "./cli[dev]" --force-reinstall --no-deps

# 3. Confirm.
kcia --version
kcia profile list
```

`git reset --hard` throws away anything you changed inside the clone. That is intended:
the clone is a distribution copy, not a place to edit. Your own projects are untouched.

### If it still misbehaves

Clear the stale caches and reinstall from scratch:

```bash
cd ~/tools/kcia
find . -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -e "./cli[dev]"
kcia --version
```

In a project where generated output looks out of date:

```bash
cd /path/to/your/project
rm -rf .ai/generated .ai/cache
kcia profile detect
```

Never delete `.ai/manifest.yaml` or `.ai/profiles/` — those are yours and are committed.

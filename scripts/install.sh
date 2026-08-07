#!/usr/bin/env bash
#
# kcia installer / updater / uninstaller.
#
#   install:    curl -fsSL <raw-url>/scripts/install.sh | bash
#   update:     curl -fsSL <raw-url>/scripts/install.sh | bash -s update
#   uninstall:  curl -fsSL <raw-url>/scripts/install.sh | bash -s uninstall
#
# From an existing clone the same script works directly:
#   ~/tools/kcia/scripts/install.sh [install|update|uninstall]
#
# Environment overrides:
#   KCIA_HOME  clone location          (default: ~/tools/kcia)
#   KCIA_BIN   shim directory          (default: ~/.local/bin)
#   KCIA_REPO  git URL to clone from
#   KCIA_REF   branch/tag to track     (default: master)

set -euo pipefail

KCIA_HOME="${KCIA_HOME:-$HOME/tools/kcia}"
KCIA_BIN="${KCIA_BIN:-$HOME/.local/bin}"
KCIA_REPO="${KCIA_REPO:-https://github.com/rendondeveloper/kcia.git}"
KCIA_REF="${KCIA_REF:-master}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
SHIM="$KCIA_BIN/kcia"
PATH_MARKER="# added by kcia installer"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed."; }

check_python() {
  require "$PYTHON_BIN"
  "$PYTHON_BIN" - <<'PY' || die "Python 3.11+ is required."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
}

sync_clone() {
  if [ -d "$KCIA_HOME/.git" ]; then
    info "Updating clone at $KCIA_HOME"
    git -C "$KCIA_HOME" fetch --quiet origin "$KCIA_REF"
    # The clone is a distribution copy, not a place to edit: take the ref as published.
    git -C "$KCIA_HOME" reset --quiet --hard "origin/$KCIA_REF"
    git -C "$KCIA_HOME" clean -qfd
  elif [ -e "$KCIA_HOME" ]; then
    die "$KCIA_HOME exists but is not a git clone. Move it aside and rerun."
  else
    info "Cloning kcia into $KCIA_HOME"
    git clone --quiet --branch "$KCIA_REF" "$KCIA_REPO" "$KCIA_HOME"
  fi
}

build_venv() {
  if [ ! -x "$KCIA_HOME/.venv/bin/python" ]; then
    info "Creating virtualenv"
    "$PYTHON_BIN" -m venv "$KCIA_HOME/.venv"
  fi
  info "Installing the CLI"
  "$KCIA_HOME/.venv/bin/pip" install --quiet --upgrade pip
  "$KCIA_HOME/.venv/bin/pip" install --quiet -e "$KCIA_HOME/cli"
}

link_shim() {
  mkdir -p "$KCIA_BIN"
  ln -sf "$KCIA_HOME/.venv/bin/kcia" "$SHIM"
  case ":$PATH:" in
    *":$KCIA_BIN:"*) ;;
    *)
      local rc="$HOME/.zshrc"
      [ -n "${BASH_VERSION:-}" ] && [ -f "$HOME/.bashrc" ] && rc="$HOME/.bashrc"
      printf '\nexport PATH="%s:$PATH"  %s\n' "$KCIA_BIN" "$PATH_MARKER" >> "$rc"
      warn "$KCIA_BIN was not on PATH; added it to $rc. Run: source $rc"
      ;;
  esac
}

do_install() {
  require git
  check_python
  sync_clone
  build_venv
  link_shim
  info "Installed $("$SHIM" --version 2>/dev/null || echo kcia)"
  info "Next: kcia agent set planner claude && cd <your-project> && kcia init --yes"
}

do_uninstall() {
  info "Removing $KCIA_HOME"
  rm -rf "$KCIA_HOME"
  rm -f "$SHIM"
  info "Removing user configuration and installed packs"
  rm -rf "$HOME/.config/kcia" "$HOME/.local/share/kcia"
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ -f "$rc" ] || continue
    if grep -q "$PATH_MARKER" "$rc" 2>/dev/null; then
      # grep -v exits 1 when nothing is left, which is a valid result here.
      { grep -v "$PATH_MARKER" "$rc" || true; } > "$rc.kcia-tmp"
      mv "$rc.kcia-tmp" "$rc"
      info "Cleaned the PATH entry from $rc"
    fi
    if grep -q "tools/kcia/.venv/bin" "$rc" 2>/dev/null; then
      warn "$rc still has a manual kcia PATH line; remove it by hand."
    fi
  done
  info "Done. Your projects' .ai/ directories were left untouched."
}

case "${1:-install}" in
  install|update) do_install ;;
  uninstall)      do_uninstall ;;
  *)              die "Unknown command '${1}'. Use: install | update | uninstall" ;;
esac

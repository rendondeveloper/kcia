"""Set integrated-terminal titles so IDEs show `kcia`, not `Python`."""

from __future__ import annotations

import sys
from typing import TextIO


def label_from_argv(argv: list[str] | None = None) -> str:
    """Build a short terminal label from argv (for example `kcia work approve`)."""
    args = list(argv or sys.argv[1:])
    if args and args[0] in {"-V", "--version"}:
        return "kcia"

    parts: list[str] = ["kcia"]
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {
            "--path",
            "--file",
            "-f",
            "--until",
            "--wave",
            "--model",
            "--scope",
            "--profile",
        }:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        if len(arg) > 48 or "/" in arg or "\n" in arg:
            break
        parts.append(arg)
        if len(parts) >= 3:
            break
    return " ".join(parts)


def set_terminal_title(
    title: str,
    *,
    stream: TextIO | None = None,
) -> None:
    """Publish a terminal title for VS Code, Cursor, and other xterm hosts.

    Integrated terminals often label Python CLIs as ``Python`` because the
    foreground process is the interpreter. OSC sequences let hosts show a
    meaningful name when ``terminal.integrated.tabs.title`` includes
    ``${sequence}`` (Cursor/VS Code default for agent CLIs).
    """
    target = stream or sys.stdout
    if not hasattr(target, "isatty") or not target.isatty():
        return
    if not title:
        return

    safe = title.replace("\x1b", "").replace("\x07", "")
    try:
        # xterm window + icon title
        target.write(f"\033]0;{safe}\007")
        target.write(f"\033]2;{safe}\007")
        # VS Code / Cursor shell integration: explicit command line for tab naming
        target.write(f"\033]633;E;{safe}\033\\")
        target.flush()
    except OSError:
        return


def set_kcia_terminal_title(argv: list[str] | None = None) -> str:
    """Set the terminal title from the current kcia invocation."""
    title = label_from_argv(argv)
    set_terminal_title(title)
    return title

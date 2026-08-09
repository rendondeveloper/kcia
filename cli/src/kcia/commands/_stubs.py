"""Shared helpers for command stubs."""

from __future__ import annotations

import typer


def not_implemented(command_name: str) -> None:
    typer.echo(f"NOT IMPLEMENTED: {command_name}")
    raise typer.Exit(code=1)

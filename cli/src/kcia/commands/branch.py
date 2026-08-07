import typer

from kcia.commands._stubs import not_implemented

app = typer.Typer(help="Git branch and commit helpers.")


@app.callback(invoke_without_command=True)
def branch() -> None:
    not_implemented("branch")

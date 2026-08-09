import typer

from kcia.commands._stubs import not_implemented

app = typer.Typer(help="Authentication for optional integrations.")


@app.callback(invoke_without_command=True)
def auth() -> None:
    not_implemented("auth")

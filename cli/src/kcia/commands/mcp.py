import typer

from kcia.commands._stubs import not_implemented

app = typer.Typer(help="Register and manage MCP servers.")


@app.callback(invoke_without_command=True)
def mcp() -> None:
    not_implemented("mcp")

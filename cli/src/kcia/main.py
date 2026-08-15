import typer

from kcia import VERSION
from kcia.commands.agent import app as agent_app
from kcia.commands.ask import ask
from kcia.commands.auth import app as auth_app
from kcia.commands.branch import app as branch_app
from kcia.commands.commit import commit_command
from kcia.commands.doctor import doctor
from kcia.commands.init import init
from kcia.commands.mcp import app as mcp_app
from kcia.commands.profile import app as profile_app
from kcia.commands.session import app as session_app
from kcia.commands.sync import sync
from kcia.commands.work import app as work_app

ROOT_HELP = """kcia: control plane CLI for development agents

Common commands:

  kcia init
  kcia profile list
  kcia agent show
  kcia work "fix the overflow"
  kcia branch start
  kcia done
  kcia doctor
  kcia ask "which profiles apply to lib/main.dart?"
  kcia session log --title "..."
"""

app = typer.Typer(help=ROOT_HELP, no_args_is_help=True)

app.command("init", help="Initialize `.ai/` and generated adapters in the current repository.")(init)
app.add_typer(work_app, name="work")
app.add_typer(agent_app, name="agent")
app.add_typer(profile_app, name="profile")
app.add_typer(session_app, name="session")
app.add_typer(auth_app, name="auth")
app.add_typer(branch_app, name="branch")
app.add_typer(mcp_app, name="mcp")

app.command(
    "done",
    help="Review and write the commits that close the task; session history is saved automatically.",
)(commit_command)
app.command("doctor", help="Validate local setup and repository configuration.")(doctor)
app.command("ask", help="Run a direct query without creating a task.")(ask)
app.command("sync", help="Sync the control plane and re-render adapters.")(sync)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"kcia {VERSION}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the kcia version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """kcia control plane CLI."""

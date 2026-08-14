from typer.testing import CliRunner

from kcia.main import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    for command in [
        "init",
        "work",
        "agent",
        "profile",
        "session",
        "doctor",
        "ask",
        "sync",
        "branch",
        "auth",
        "mcp",
    ]:
        assert command in output

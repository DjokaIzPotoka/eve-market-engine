from typer.testing import CliRunner

from eve.cli import app

runner = CliRunner()


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "ingest",
        "features",
        "mm",
        "haul",
        "ingest-all",
        "ingest-history-all",
        "health",
        "log-trade",
        "report",
    ):
        assert command in result.output

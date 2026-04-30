from __future__ import annotations

from typer.testing import CliRunner

from koala_strategy.cli import app


def test_structured_pipeline_commands_are_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "extract-self-review-features" in result.output
    assert "extract-review-evaluator-features" in result.output
    assert "train-structured-verdict-model" in result.output

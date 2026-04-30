from __future__ import annotations

from typer.testing import CliRunner

from koala_strategy.cli import app


def test_structured_pipeline_commands_are_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "extract-self-review-features" in result.output
    assert "extract-review-evaluator-features" in result.output
    assert "run-deepseek-pipeline" in result.output
    assert "train-structured-verdict-model" in result.output


def test_structured_extract_commands_expose_workers_option() -> None:
    runner = CliRunner()

    self_result = runner.invoke(app, ["extract-self-review-features", "--help"])
    external_result = runner.invoke(app, ["extract-review-evaluator-features", "--help"])

    assert self_result.exit_code == 0
    assert external_result.exit_code == 0
    assert "--workers" in self_result.output
    assert "--workers" in external_result.output

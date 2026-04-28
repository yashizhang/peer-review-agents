from __future__ import annotations

from typing import Literal

import typer

from koala_strategy.models.advanced_fulltext_experiments import run_advanced_fulltext_experiments, train_fast_text_evidence_model


def main(
    train_limit: int = 1200,
    include_diagnostics: bool = False,
    profile: Literal["suite", "production"] = "production",
) -> None:
    if profile == "production":
        print(train_fast_text_evidence_model(train_limit=train_limit))
    else:
        print(run_advanced_fulltext_experiments(train_limit=train_limit, include_diagnostics=include_diagnostics))


if __name__ == "__main__":
    typer.run(main)

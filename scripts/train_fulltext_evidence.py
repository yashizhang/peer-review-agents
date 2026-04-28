from __future__ import annotations

import typer

from koala_strategy.models.fulltext_evidence_model import train_and_evaluate_fulltext


def main(train_limit: int = 1200, model_type: str = "hgb") -> None:
    print(train_and_evaluate_fulltext(train_limit=train_limit, model_type=model_type))


if __name__ == "__main__":
    typer.run(main)


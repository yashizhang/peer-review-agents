from __future__ import annotations

import typer

from koala_strategy.models.fulltext_evidence_model import parse_pdf_corpus


def main(train_limit: int = 1200, workers: int = 6, force: bool = False) -> None:
    print(parse_pdf_corpus(train_limit=train_limit, test_all=True, workers=workers, force=force))


if __name__ == "__main__":
    typer.run(main)


from __future__ import annotations

import typer

from koala_strategy.models.experiment_suite import run_experiment_suite


def main(train_limit: int = 1200) -> None:
    print(run_experiment_suite(train_limit=train_limit))


if __name__ == "__main__":
    typer.run(main)


from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich import print

from koala_strategy.agent.scheduler import dry_run_one_paper, dry_run_verdict, run_agent_once, run_verdicts_once
from koala_strategy.config import load_config, resolve_path
from koala_strategy.data.dataset_builder import build_iclr_dataset
from koala_strategy.data.iclr_loader import load_public_papers
from koala_strategy.models.export_bundle import generate_prediction_bundle
from koala_strategy.models.predict_paper_only import load_model_artifacts
from koala_strategy.models.train_all import evaluate_global_test, train_all as train_all_models
from koala_strategy.models.fulltext_evidence_model import parse_pdf_corpus, train_and_evaluate_fulltext
from koala_strategy.models.advanced_fulltext_experiments import run_advanced_fulltext_experiments, train_fast_text_evidence_model
from koala_strategy.models.experiment_suite import run_experiment_suite
from koala_strategy.models.llm_judge_experiment import run_llm_judge_subset_experiment
from koala_strategy.llm.review_judge import gated_llm_blend, run_llm_review_judge
from koala_strategy.models.train_model_a import train_model_a
from koala_strategy.models.train_model_b import train_model_b
from koala_strategy.models.train_model_c import train_model_c
from koala_strategy.utils import dump_json

app = typer.Typer(no_args_is_help=True)


@app.command("build-iclr-dataset")
def build_iclr_dataset_cmd() -> None:
    print(build_iclr_dataset())


@app.command("train-model-a")
def train_model_a_cmd() -> None:
    print(train_model_a()["metrics"])


@app.command("train-model-b")
def train_model_b_cmd() -> None:
    print(train_model_b()["metrics"])


@app.command("train-model-c")
def train_model_c_cmd() -> None:
    cfg = load_config()
    import numpy as np
    from koala_strategy.data.iclr_loader import load_iclr_examples

    examples = load_iclr_examples(config=cfg)
    y = np.asarray([1 if ex.decision == "accept" else 0 for ex in examples], dtype=int)
    p_path = resolve_path(cfg, "model_dir") / "paper_only_oof_predictions.npy"
    if not p_path.exists():
        raise typer.BadParameter("Run train-all first so paper_only_oof_predictions.npy exists.")
    print(train_model_c(examples, y, np.load(p_path), cfg)["metrics"])


@app.command("train-all")
def train_all_cmd(evaluate: bool = True) -> None:
    metrics = train_all_models(evaluate=evaluate)
    print(metrics.get("global_test", metrics.get("stacker")))


@app.command("evaluate-test")
def evaluate_test_cmd() -> None:
    print(evaluate_global_test())


@app.command("parse-pdfs")
def parse_pdfs_cmd(train_limit: int = 1200, workers: int = 6, force: bool = False) -> None:
    print(parse_pdf_corpus(train_limit=train_limit, workers=workers, force=force))


@app.command("train-fulltext-evidence")
def train_fulltext_evidence_cmd(train_limit: int = 1200, model_type: str = "hgb") -> None:
    print(train_and_evaluate_fulltext(train_limit=train_limit, model_type=model_type))


@app.command("advanced-fulltext-experiments")
def advanced_fulltext_experiments_cmd(train_limit: int = 1200, include_diagnostics: bool = False) -> None:
    print(run_advanced_fulltext_experiments(train_limit=train_limit, include_diagnostics=include_diagnostics))


@app.command("train-fast-text-evidence")
def train_fast_text_evidence_cmd(train_limit: int = 1200, mode: str = "evidence_tables_safe") -> None:
    print(train_fast_text_evidence_model(train_limit=train_limit, mode=mode))


@app.command("run-experiment-suite")
def run_experiment_suite_cmd(train_limit: int = 1200) -> None:
    print(run_experiment_suite(train_limit=train_limit))


@app.command("llm-judge-subset")
def llm_judge_subset_cmd(
    limit: int = 24,
    selection: str = "balanced_uncertain",
    force: bool = False,
    seed: int = 42,
) -> None:
    print(run_llm_judge_subset_experiment(limit=limit, selection=selection, force=force, seed=seed))


@app.command("score-paper")
def score_paper(paper_id: str) -> None:
    cfg = load_config()
    artifacts = load_model_artifacts(cfg)
    papers = load_public_papers(config=cfg)
    for paper in papers:
        if paper.paper_id == paper_id:
            bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
            typer.echo(bundle.model_dump_json(indent=2))
            return
    raise typer.BadParameter(f"Unknown paper_id {paper_id}")


@app.command("score-paper-llm")
def score_paper_llm(paper_id: str, force: bool = False) -> None:
    cfg = load_config()
    artifacts = load_model_artifacts(cfg)
    papers = load_public_papers(config=cfg)
    for paper in papers:
        if paper.paper_id == paper_id:
            bundle = generate_prediction_bundle(paper, artifacts, cfg, save=True)
            judge = run_llm_review_judge(paper, bundle, config=cfg, force=force)
            update = gated_llm_blend(float(bundle.paper_only.get("p_accept", 0.5)), judge)
            typer.echo(
                json.dumps(
                    {
                        "paper_id": paper_id,
                        "prediction": update,
                        "llm_judge": judge,
                        "bundle": json.loads(bundle.model_dump_json()),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return
    raise typer.BadParameter(f"Unknown paper_id {paper_id}")


@app.command("dry-run-one-paper")
def dry_run_one_paper_cmd(paper_id: str, agent: str = "review_director") -> None:
    print(dry_run_one_paper(paper_id, agent))


@app.command("dry-run-verdict")
def dry_run_verdict_cmd(paper_id: str, agent: str = "review_director") -> None:
    print(dry_run_verdict(paper_id, agent))


@app.command("run-agent")
def run_agent_cmd(agent: str = "review_director", dry_run: bool = True, limit: int = 5) -> None:
    print(run_agent_once(agent, dry_run=dry_run, limit=limit))


@app.command("run-verdicts")
def run_verdicts_cmd(agent: str = "review_director", dry_run: bool = True, limit: int = 5) -> None:
    print(run_verdicts_once(agent, dry_run=dry_run, limit=limit))


@app.command("run-all-agents")
def run_all_agents_cmd(dry_run: bool = True, limit: int = 2) -> None:
    for agent in ["review_director"]:
        print({agent: run_agent_once(agent, dry_run=dry_run, limit=limit)})


@app.command("dashboard")
def dashboard_cmd() -> None:
    from scripts.dashboard import main

    main()


if __name__ == "__main__":
    app()

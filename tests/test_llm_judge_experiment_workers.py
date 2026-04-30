from types import SimpleNamespace

from koala_strategy.models.llm_judge_experiment import _run_judge_tasks


def test_run_judge_tasks_preserves_selection_order_with_workers() -> None:
    papers = [SimpleNamespace(paper_id="slow"), SimpleNamespace(paper_id="fast")]
    bundles = [SimpleNamespace(), SimpleNamespace()]
    tasks = [(1, 0, papers[0], bundles[0]), (2, 1, papers[1], bundles[1])]

    def fake_runner(paper, bundle, config, force):
        return {
            "paper_id": paper.paper_id,
            "fallback": False,
            "accept_probability": 0.5,
            "verdict_score": 5.0,
            "confidence": 0.5,
        }

    results = _run_judge_tasks(
        tasks,
        total=2,
        config={},
        force=False,
        workers=2,
        judge_runner=fake_runner,
    )

    assert [result.paper.paper_id for result in results] == ["slow", "fast"]
    assert [result.position for result in results] == [1, 2]

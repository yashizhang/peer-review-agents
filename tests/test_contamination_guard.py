from koala_strategy.data.contamination_guard import is_near_duplicate
from koala_strategy.schemas import ICLRTrainingExample, PaperRecord


def _train(title="A Great Paper", abstract="This abstract explains a new method for learning."):
    return ICLRTrainingExample(paper_id="t1", title=title, abstract=abstract, decision="accept")


def test_identical_title_returns_true():
    paper = PaperRecord(paper_id="p1", title="A Great Paper", abstract="different")
    assert is_near_duplicate(paper, _train())


def test_modified_title_same_abstract_returns_true():
    abstract = "This abstract explains a new method for learning with strong empirical evidence and theory."
    paper = PaperRecord(paper_id="p1", title="A Pretty Great Paper", abstract=abstract)
    assert is_near_duplicate(paper, _train("A Great Paper Revised", abstract))


def test_unrelated_returns_false():
    paper = PaperRecord(paper_id="p1", title="Optimization for Gaussian Processes", abstract="Bayesian optimization for black-box objectives.")
    assert not is_near_duplicate(paper, _train("Language Models for Search", "Reinforcement learning improves web search agents."))


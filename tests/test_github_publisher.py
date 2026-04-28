from koala_strategy.agent import github_publisher
from koala_strategy.agent.github_publisher import build_github_file_url, is_allowed_github_file_url, reasoning_branch_name, verify_github_url
from koala_strategy.agent.reasoning_writer import write_reasoning_file


def test_branch_name_generated_correctly():
    assert reasoning_branch_name("agent", "paper/id") == "agent-reasoning/agent/paper-id"


def test_url_verifier_rejects_404(monkeypatch):
    class Response:
        status_code = 404

    monkeypatch.setattr(github_publisher.requests, "get", lambda *args, **kwargs: Response())
    assert not verify_github_url("https://example.test/missing")


def test_reasoning_file_contains_hash_and_content(tmp_path):
    cfg = {"paths": {"reasoning_dir": str(tmp_path)}, "models": {}, "github": {}, "online_policy": {}, "agents": {}, "competition": {}}
    path = write_reasoning_file("a", "p", "comment", "hello world", {"positive_evidence": ["x"]}, cfg)
    text = path.read_text()
    assert "Content hash:" in text
    assert "hello world" in text


def test_builds_blob_url_for_reasoning_branch():
    url = build_github_file_url("https://github.com/o/r", "agent-reasoning/a/p", "reasoning/a/p/x.md")
    assert url == "https://github.com/o/r/blob/agent-reasoning/a/p/reasoning/a/p/x.md"
    assert is_allowed_github_file_url(url)

from __future__ import annotations

import pytest

from koala_strategy.llm import providers


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


def test_deepseek_provider_posts_chat_completion(monkeypatch) -> None:
    captured = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-token")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example/")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(providers.requests, "post", fake_post)

    provider = providers.DeepSeekTextProvider(timeout_seconds=12)

    assert provider.generate("Return JSON.", temperature=0.3) == "{\"ok\": true}"
    assert captured["url"] == "https://deepseek.example/chat/completions"
    assert captured["timeout"] == 12
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"] == {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "thinking": {"type": "disabled"},
        "stream": False,
        "temperature": 0.3,
    }


def test_deepseek_provider_allows_model_override(monkeypatch) -> None:
    captured = {}

    def fake_post(url: str, **kwargs):
        captured.update(kwargs)
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-token")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setattr(providers.requests, "post", fake_post)

    provider = providers.DeepSeekTextProvider()

    assert provider.generate("Hi", model="deepseek-v4-flash") == "done"
    assert captured["json"]["model"] == "deepseek-v4-flash"


def test_deepseek_provider_retries_rate_limit(monkeypatch) -> None:
    calls = []
    sleeps = []

    def fake_post(url: str, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return FakeResponse({"error": "rate limited"}, status_code=429)
        return FakeResponse({"choices": [{"message": {"content": "done"}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-token")
    monkeypatch.setattr(providers.requests, "post", fake_post)
    monkeypatch.setattr(providers.time, "sleep", sleeps.append)

    provider = providers.DeepSeekTextProvider(max_retries=2, retry_backoff_seconds=0.5)

    assert provider.generate("Hi") == "done"
    assert len(calls) == 2
    assert sleeps == [0.5]


def test_deepseek_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    provider = providers.DeepSeekTextProvider()

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        provider.generate("Hi")


def test_get_text_provider_selects_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("KOALA_LLM_PROVIDER", "deepseek")

    provider = providers.get_text_provider({"models": {"llm_provider": "heuristic"}})

    assert isinstance(provider, providers.DeepSeekTextProvider)

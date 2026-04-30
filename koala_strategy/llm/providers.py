from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

import requests

from koala_strategy.config import load_config, project_root


class TextProvider(Protocol):
    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        ...


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> Any:
        ...


class HeuristicTextProvider:
    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        return "{}"


class OpenAITextProvider:
    def __init__(self, model: str = "gpt-5.4-mini"):
        from openai import OpenAI  # type: ignore

        self.client = OpenAI()
        self.model = model

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        response = self.client.responses.create(
            model=model or self.model,
            input=prompt,
            temperature=temperature,
        )
        return response.output_text


class DeepSeekTextProvider:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 180,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
    ):
        self.model = model or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-pro"
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.timeout_seconds = int(timeout_seconds)
        self.max_retries = int(max_retries if max_retries is not None else os.getenv("DEEPSEEK_MAX_RETRIES", "3"))
        self.retry_backoff_seconds = float(
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else os.getenv("DEEPSEEK_RETRY_BACKOFF_SECONDS", "2.0")
        )

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for KOALA_LLM_PROVIDER=deepseek.")
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": temperature,
        }
        response = self._post_with_retries(payload)
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("DeepSeek response did not contain choices[0].message.content.") from exc
        if content is None:
            raise RuntimeError("DeepSeek response content was empty.")
        return str(content)

    def _post_with_retries(self, payload: dict[str, Any]) -> requests.Response:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    return response
                last_exc = requests.HTTPError(f"DeepSeek HTTP {response.status_code}")
                if attempt >= self.max_retries:
                    response.raise_for_status()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise
            time.sleep(self.retry_backoff_seconds * (2**attempt))
        raise RuntimeError("DeepSeek request failed after retries.") from last_exc


class CodexTextProvider:
    """Thin wrapper around `codex exec`.

    The auth file path is passed through the environment for pantheon/codex setups
    that consume it. If the local CLI ignores it, normal Codex auth still applies.
    """

    def __init__(self, model: str = "gpt-5.4-mini", auth_file: str | None = None, timeout_seconds: int = 180):
        self.model = model
        self.auth_file = auth_file
        self.timeout_seconds = int(timeout_seconds)

    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        env = os.environ.copy()
        if self.auth_file:
            env["CODEX_AUTH_FILE"] = self.auth_file
            env["PANTHEON_CODEX_AUTH_FILE"] = self.auth_file
        with tempfile.NamedTemporaryFile("r", encoding="utf-8", delete=False) as out:
            out_path = Path(out.name)
        try:
            cmd = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-C",
                str(project_root()),
                "-m",
                model or self.model,
                "-o",
                str(out_path),
                prompt,
            ]
            subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=self.timeout_seconds)
            return out_path.read_text(encoding="utf-8")
        finally:
            out_path.unlink(missing_ok=True)


class LocalHashEmbeddingProvider:
    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> Any:
        import numpy as np

        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in (text or "").lower().split():
                idx = hash(token) % self.dim
                mat[row, idx] += 1.0
            norm = np.linalg.norm(mat[row])
            if norm:
                mat[row] /= norm
        return mat


def get_text_provider(config: dict | None = None) -> TextProvider:
    cfg = config or load_config()
    provider = str(os.getenv("KOALA_LLM_PROVIDER") or cfg.get("models", {}).get("llm_provider", "heuristic")).lower()
    model = cfg.get("models", {}).get("codex_model", "gpt-5.4-mini")
    timeout_seconds = int((cfg.get("models", {}).get("agentic_llm", {}) or {}).get("llm_call_timeout_seconds", cfg.get("models", {}).get("llm_call_timeout_seconds", 180)))
    if provider == "api" and os.getenv("OPENAI_API_KEY"):
        return OpenAITextProvider(model=model)
    if provider == "deepseek":
        return DeepSeekTextProvider(timeout_seconds=timeout_seconds)
    if provider == "codex":
        return CodexTextProvider(model=model, auth_file=cfg.get("models", {}).get("codex_auth_file"), timeout_seconds=timeout_seconds)
    return HeuristicTextProvider()

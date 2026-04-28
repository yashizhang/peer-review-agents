from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

import numpy as np

from koala_strategy.config import load_config, project_root


class TextProvider(Protocol):
    def generate(self, prompt: str, *, model: str | None = None, temperature: float = 0.0) -> str:
        ...


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> np.ndarray:
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


class CodexTextProvider:
    """Thin wrapper around `codex exec`.

    The auth file path is passed through the environment for pantheon/codex setups
    that consume it. If the local CLI ignores it, normal Codex auth still applies.
    """

    def __init__(self, model: str = "gpt-5.4-mini", auth_file: str | None = None):
        self.model = model
        self.auth_file = auth_file

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
            subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
            return out_path.read_text(encoding="utf-8")
        finally:
            out_path.unlink(missing_ok=True)


class LocalHashEmbeddingProvider:
    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> np.ndarray:
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
    provider = str(cfg.get("models", {}).get("llm_provider", "heuristic")).lower()
    model = cfg.get("models", {}).get("codex_model", "gpt-5.4-mini")
    if provider == "api" and os.getenv("OPENAI_API_KEY"):
        return OpenAITextProvider(model=model)
    if provider == "codex":
        return CodexTextProvider(model=model, auth_file=cfg.get("models", {}).get("codex_auth_file"))
    return HeuristicTextProvider()


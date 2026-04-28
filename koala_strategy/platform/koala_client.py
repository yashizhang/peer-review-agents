from __future__ import annotations

import os
import time
from typing import Any

import requests

from koala_strategy.data.iclr_loader import load_koala_reference, paper_record_from_row
from koala_strategy.logging_utils import log_error
from koala_strategy.schemas import CommentRecord, PaperRecord
from koala_strategy.utils import iso_now


class KoalaClient:
    def __init__(self, agent_name: str, api_key: str | None = None, base_url: str | None = None, dry_run: bool = True):
        self.agent_name = agent_name
        self.api_key = api_key or os.getenv(f"KOALA_API_KEY_{agent_name.upper()}") or os.getenv("KOALA_API_KEY")
        self.base_url = (base_url or os.getenv("KOALA_API_BASE_URL") or "https://koala.science/api/v1").rstrip("/")
        self.dry_run = dry_run
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({"Authorization": self.api_key})

    def _request(self, method: str, path: str, **kwargs):
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.request(method, url, timeout=kwargs.pop("timeout", 20), **kwargs)
                response.raise_for_status()
                if response.content:
                    return response.json()
                return None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        log_error(last_exc or "request failed", {"method": method, "url": url})
        raise last_exc or RuntimeError("request failed")

    def get_unread_count(self) -> int:
        if self.dry_run:
            return 0
        data = self._request("GET", "notifications/unread-count")
        return int(data.get("unread_count", data.get("count", 0)) if isinstance(data, dict) else 0)

    def get_notifications(self) -> list[dict[str, Any]]:
        if self.dry_run:
            return []
        data = self._request("GET", "notifications")
        return data.get("results", data) if isinstance(data, dict) else data

    def mark_notifications_read(self, ids: list[str]) -> None:
        if self.dry_run or not ids:
            return
        self._request("POST", "notifications/read", json={"notification_ids": ids})

    def list_papers(self, status: str | None = None) -> list[PaperRecord]:
        if self.dry_run:
            papers = load_koala_reference()
            return [p for p in papers if status is None or p.status == status]
        path = "papers/"
        if status:
            path += f"?status={status}"
        data = self._request("GET", path)
        rows = data.get("results", data) if isinstance(data, dict) else data
        return [paper_record_from_row(row) for row in rows]

    def get_paper(self, paper_id: str) -> PaperRecord:
        if self.dry_run:
            for paper in load_koala_reference():
                if paper.paper_id == paper_id:
                    return paper
            raise KeyError(paper_id)
        return paper_record_from_row(self._request("GET", f"papers/{paper_id}/"))

    def get_comments(self, paper_id: str) -> list[CommentRecord]:
        if self.dry_run:
            return []
        data = self._request("GET", f"comments/paper/{paper_id}?limit=100")
        rows = data.get("results", data) if isinstance(data, dict) else data
        from datetime import datetime, timezone

        comments = []
        for row in rows:
            created = row.get("created_at")
            dt = datetime.fromisoformat(created) if created else datetime.now(timezone.utc)
            comments.append(
                CommentRecord(
                    comment_id=str(row.get("comment_id") or row.get("id")),
                    paper_id=paper_id,
                    author_agent=str(row.get("author_agent") or row.get("author") or ""),
                    owner_id=row.get("owner_id"),
                    parent_id=row.get("parent_id"),
                    content_markdown=str(row.get("content_markdown") or row.get("content") or ""),
                    created_at=dt,
                    quality_score=row.get("quality_score"),
                )
            )
        return comments

    def post_comment(self, paper_id: str, content_markdown: str, github_file_url: str, parent_id: str | None = None) -> str:
        if self.dry_run:
            return f"dry_comment_{self.agent_name}_{paper_id}_{abs(hash(content_markdown)) % 1000000}"
        data = self._request(
            "POST",
            "comments/",
            json={"paper_id": paper_id, "content_markdown": content_markdown, "github_file_url": github_file_url, "parent_id": parent_id},
        )
        return str(data.get("comment_id") or data.get("id"))

    def submit_verdict(self, paper_id: str, score: float, verdict_markdown: str, github_file_url: str | None = None) -> str:
        if self.dry_run:
            return f"dry_verdict_{self.agent_name}_{paper_id}"
        data = self._request(
            "POST",
            "verdicts/",
            json={"paper_id": paper_id, "score": score, "content_markdown": verdict_markdown, "github_file_url": github_file_url},
        )
        return str(data.get("verdict_id") or data.get("id") or iso_now())

    def get_agent_profile(self) -> dict[str, Any]:
        if self.dry_run:
            return {"agent_name": self.agent_name, "karma_remaining": 100.0}
        return self._request("GET", "users/me")

    def update_agent_profile(self, description: str) -> None:
        if self.dry_run:
            return
        self._request("PATCH", "users/me", json={"description": description})

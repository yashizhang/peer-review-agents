from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


from koala_strategy.config import load_config
from koala_strategy.data.iclr_loader import load_koala_reference, paper_record_from_row
from koala_strategy.logging_utils import log_error, log_event
from koala_strategy.schemas import CommentRecord, PaperRecord
from koala_strategy.utils import iso_now


def _read_first_existing(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue
    return None


def _normalise_auth_header(api_key: str | None, scheme: str) -> str | None:
    if not api_key:
        return None
    token = api_key.strip()
    if not token:
        return None
    if token.lower().startswith("bearer "):
        return token
    if scheme.lower() in {"none", "raw", "token"}:
        return token
    return f"Bearer {token}"


def _rows_from_response(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "items", "papers", "comments", "notifications", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        if all(isinstance(k, str) for k in data.keys()):
            return [data]
    return []


class KoalaClient:
    def __init__(
        self,
        agent_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        dry_run: bool = True,
        config: dict[str, Any] | None = None,
    ):
        self.agent_name = agent_name
        self.config = config or load_config()
        platform = self.config.get("platform", {})
        self.api_key = (
            api_key
            or os.getenv(f"KOALA_API_KEY_{agent_name.upper()}")
            or os.getenv("KOALA_API_KEY")
            or _read_first_existing([
                Path(".api_key"),
                Path("agent_configs") / agent_name / ".api_key",
                Path(agent_name) / ".api_key",
            ])
        )
        self.base_url = (base_url or platform.get("api_base_url") or os.getenv("KOALA_API_BASE_URL") or "https://koala.science/api/v1").rstrip("/")
        self.platform_base_url = (platform.get("base_url") or os.getenv("KOALA_PLATFORM_BASE_URL") or "https://koala.science").rstrip("/")
        self.auth_scheme = str(platform.get("auth_scheme") or os.getenv("KOALA_AUTH_SCHEME") or "bearer")
        self.dry_run = dry_run
        import requests

        self.session = requests.Session()
        auth_value = _normalise_auth_header(self.api_key, self.auth_scheme)
        if auth_value:
            self.session.headers.update({"Authorization": auth_value})
        self.session.headers.update({"User-Agent": f"koala-strategy-agent/{agent_name}"})

    def _request(self, method: str, path: str, *, allow_failure: bool = False, **kwargs):
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        timeout = kwargs.pop("timeout", 20)
        for attempt in range(3):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                if response.status_code == 401 and self.auth_scheme.lower() == "bearer" and self.api_key:
                    # Some early Koala clients used a raw Authorization header.
                    # Retry once with the raw key before surfacing the auth error.
                    raw_headers = dict(self.session.headers)
                    raw_headers["Authorization"] = self.api_key.strip()
                    response = self.session.request(method, url, timeout=timeout, headers=raw_headers, **kwargs)
                response.raise_for_status()
                if response.content:
                    try:
                        return response.json()
                    except ValueError:
                        return response.text
                return None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        log_error(last_exc or "request failed", {"method": method, "url": url}, self.config)
        if allow_failure:
            return None
        raise last_exc or RuntimeError("request failed")

    def _request_first(self, method: str, paths: list[str], *, allow_failure: bool = True, **kwargs):
        last: Any = None
        for path in paths:
            data = self._request(method, path, allow_failure=True, **kwargs)
            if data is not None:
                return data
            last = data
        if allow_failure:
            return last
        raise RuntimeError(f"No endpoint succeeded for {method} {paths}")

    def _paginate(self, path: str, *, limit_pages: int = 20) -> list[Any]:
        rows: list[Any] = []
        next_path: str | None = path
        pages = 0
        while next_path and pages < limit_pages:
            data = self._request("GET", next_path)
            rows.extend(_rows_from_response(data))
            pages += 1
            if isinstance(data, dict):
                nxt = data.get("next") or data.get("next_page") or data.get("next_page_url") or data.get("next_page_token")
                if isinstance(nxt, str) and nxt:
                    if nxt.startswith("http"):
                        next_path = nxt
                    elif "page_token" not in nxt and "=" not in nxt and not nxt.startswith("/"):
                        sep = "&" if "?" in path else "?"
                        next_path = f"{path}{sep}page_token={nxt}"
                    else:
                        next_path = nxt
                    continue
            next_path = None
        return rows

    def get_unread_count(self) -> int:
        if self.dry_run:
            return 0
        data = self._request_first(
            "GET",
            ["notifications/unread-count", "notifications/unread_count", "notifications/unread-count/", "notifications/unread_count/"],
        )
        if isinstance(data, dict):
            return int(data.get("unread_count", data.get("count", 0)) or 0)
        if isinstance(data, int):
            return int(data)
        return 0

    def get_notifications(self, unread_only: bool = True) -> list[dict[str, Any]]:
        if self.dry_run:
            return []
        suffix = "?unread=true" if unread_only else ""
        data = self._request_first("GET", [f"notifications{suffix}", f"notifications/{suffix}"])
        return [row for row in _rows_from_response(data) if isinstance(row, dict)]

    def mark_notifications_read(self, ids: list[str]) -> None:
        if self.dry_run or not ids:
            return
        payload = {"notification_ids": ids, "ids": ids}
        self._request_first(
            "POST",
            ["notifications/read", "notifications/mark-read", "notifications/mark_notifications_read", "mark_notifications_read"],
            json=payload,
        )

    def list_papers(self, status: str | None = None, limit: int | None = None) -> list[PaperRecord]:
        if self.dry_run:
            papers = load_koala_reference(config=self.config)
            out = [p for p in papers if status is None or p.status == status]
            return out[:limit] if limit else out
        query = f"?{urlencode({'status': status})}" if status else ""
        rows = self._paginate(f"papers/{query}")
        papers: list[PaperRecord] = []
        for row in rows:
            if isinstance(row, dict):
                try:
                    paper = paper_record_from_row(row)
                    if status is None or paper.status == status:
                        papers.append(paper)
                except Exception as exc:  # noqa: BLE001
                    log_error(exc, {"row_keys": list(row.keys())[:20], "context": "parse paper"}, self.config)
        return papers[:limit] if limit else papers

    def get_paper(self, paper_id: str) -> PaperRecord:
        if self.dry_run:
            for paper in load_koala_reference(config=self.config):
                if paper.paper_id == paper_id:
                    return paper
            raise KeyError(paper_id)
        return paper_record_from_row(self._request("GET", f"papers/{paper_id}/"))

    def get_comments(self, paper_id: str, limit: int = 100) -> list[CommentRecord]:
        if self.dry_run:
            return []
        data = self._request_first(
            "GET",
            [f"comments/paper/{paper_id}?limit={limit}", f"papers/{paper_id}/comments?limit={limit}", f"comments/?paper_id={paper_id}&limit={limit}"],
            allow_failure=False,
        )
        rows = _rows_from_response(data)
        comments = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            created = row.get("created_at") or row.get("created")
            try:
                dt = datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else datetime.now(timezone.utc)
            except ValueError:
                dt = datetime.now(timezone.utc)
            comments.append(
                CommentRecord(
                    comment_id=str(row.get("comment_id") or row.get("id")),
                    paper_id=str(row.get("paper_id") or paper_id),
                    author_agent=str(row.get("author_agent") or row.get("agent_name") or row.get("author") or ""),
                    owner_id=row.get("owner_id") or row.get("openreview_id"),
                    parent_id=row.get("parent_id"),
                    content_markdown=str(row.get("content_markdown") or row.get("content") or row.get("body") or ""),
                    created_at=dt,
                    quality_score=row.get("quality_score"),
                )
            )
        return comments

    def post_comment(self, paper_id: str, content_markdown: str, github_file_url: str, parent_id: str | None = None) -> str:
        if self.dry_run:
            return f"dry_comment_{self.agent_name}_{paper_id}_{abs(hash(content_markdown)) % 1000000}"
        payload: dict[str, Any] = {"paper_id": paper_id, "content_markdown": content_markdown, "github_file_url": github_file_url}
        if parent_id:
            payload["parent_id"] = parent_id
        data = self._request("POST", "comments/", json=payload)
        log_event("api_actions", {"action": "post_comment", "paper_id": paper_id, "response": data}, self.config)
        return str(data.get("comment_id") or data.get("id")) if isinstance(data, dict) else str(data)

    def submit_verdict(self, paper_id: str, score: float, verdict_markdown: str, github_file_url: str | None = None) -> str:
        if self.dry_run:
            return f"dry_verdict_{self.agent_name}_{paper_id}"
        payload: dict[str, Any] = {"paper_id": paper_id, "score": float(score), "content_markdown": verdict_markdown}
        if github_file_url:
            payload["github_file_url"] = github_file_url
        data = self._request("POST", "verdicts/", json=payload)
        log_event("api_actions", {"action": "submit_verdict", "paper_id": paper_id, "score": score, "response": data}, self.config)
        return str(data.get("verdict_id") or data.get("id") or iso_now()) if isinstance(data, dict) else str(data)

    def get_verdicts_for_paper(self, paper_id: str) -> list[dict[str, Any]]:
        if self.dry_run:
            return []
        data = self._request_first(
            "GET",
            [f"verdicts/paper/{paper_id}", f"papers/{paper_id}/verdicts", f"verdicts/?paper_id={paper_id}"],
            allow_failure=True,
        )
        return [row for row in _rows_from_response(data) if isinstance(row, dict)]

    def has_submitted_verdict(self, paper_id: str, agent_name: str | None = None) -> bool:
        agent = agent_name or self.agent_name
        for row in self.get_verdicts_for_paper(paper_id):
            author = str(row.get("author_agent") or row.get("agent_name") or row.get("author") or "")
            if author == agent:
                return True
        return False

    def get_agent_profile(self) -> dict[str, Any]:
        if self.dry_run:
            return {"agent_name": self.agent_name, "karma_remaining": 100.0}
        data = self._request_first("GET", ["users/me", "me", "agent/me"], allow_failure=False)
        return data if isinstance(data, dict) else {"raw": data}

    def update_agent_profile(self, description: str) -> None:
        if self.dry_run:
            return
        self._request_first("PATCH", ["users/me", "me", "agent/me"], json={"description": description})

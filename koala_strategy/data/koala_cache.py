from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from koala_strategy.config import load_config, resolve_path
from koala_strategy.schemas import CommentRecord, PaperRecord, PredictionBundle
from koala_strategy.utils import content_hash, ensure_dir, iso_now


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
  paper_id TEXT PRIMARY KEY,
  title TEXT,
  abstract TEXT,
  domains_json TEXT,
  release_time TEXT,
  status TEXT,
  pdf_url TEXT,
  parsed_text_path TEXT,
  comment_count INTEGER,
  participant_count INTEGER,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
  paper_id TEXT,
  model_version TEXT,
  prediction_bundle_json TEXT,
  created_at TEXT,
  PRIMARY KEY (paper_id, model_version)
);

CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT PRIMARY KEY,
  paper_id TEXT,
  author_agent TEXT,
  owner_id TEXT,
  parent_id TEXT,
  content_markdown TEXT,
  extracted_claims_json TEXT,
  quality_score REAL,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY,
  agent_name TEXT,
  paper_id TEXT,
  action_type TEXT,
  content_hash TEXT,
  github_file_url TEXT,
  karma_cost REAL,
  status TEXT,
  created_at TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS verdicts (
  paper_id TEXT,
  agent_name TEXT,
  score REAL,
  verdict_body TEXT,
  citation_ids_json TEXT,
  submitted BOOLEAN,
  created_at TEXT,
  PRIMARY KEY (paper_id, agent_name)
);

CREATE TABLE IF NOT EXISTS agent_state (
  agent_name TEXT PRIMARY KEY,
  karma_remaining REAL,
  last_notification_sync TEXT,
  last_paper_sync TEXT,
  active_papers_json TEXT
);
"""


class KoalaCache:
    def __init__(self, path: str | Path | None = None, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        if path is None:
            path = resolve_path(self.config, "koala_cache_dir") / "koala_cache.sqlite"
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_paper(self, paper: PaperRecord, parsed_text_path: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
              title=excluded.title,
              abstract=excluded.abstract,
              domains_json=excluded.domains_json,
              release_time=excluded.release_time,
              status=excluded.status,
              pdf_url=excluded.pdf_url,
              parsed_text_path=COALESCE(excluded.parsed_text_path, papers.parsed_text_path),
              comment_count=excluded.comment_count,
              participant_count=excluded.participant_count,
              last_seen=excluded.last_seen
            """,
            (
                paper.paper_id,
                paper.title,
                paper.abstract,
                json.dumps(paper.domains),
                paper.release_time.isoformat() if paper.release_time else None,
                paper.status,
                paper.pdf_url,
                parsed_text_path,
                paper.comment_count,
                paper.participant_count,
                iso_now(),
            ),
        )
        self.conn.commit()

    def save_prediction(self, bundle: PredictionBundle) -> None:
        self.conn.execute(
            """
            INSERT INTO predictions VALUES (?, ?, ?, ?)
            ON CONFLICT(paper_id, model_version) DO UPDATE SET
              prediction_bundle_json=excluded.prediction_bundle_json,
              created_at=excluded.created_at
            """,
            (bundle.paper_id, bundle.model_version, bundle.model_dump_json(), iso_now()),
        )
        self.conn.commit()

    def get_prediction(self, paper_id: str, model_version: str) -> PredictionBundle | None:
        row = self.conn.execute(
            "SELECT prediction_bundle_json FROM predictions WHERE paper_id=? AND model_version=?",
            (paper_id, model_version),
        ).fetchone()
        if not row:
            return None
        return PredictionBundle.model_validate_json(row["prediction_bundle_json"])

    def upsert_comment(self, comment: CommentRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comment_id) DO UPDATE SET
              content_markdown=excluded.content_markdown,
              extracted_claims_json=excluded.extracted_claims_json,
              quality_score=excluded.quality_score
            """,
            (
                comment.comment_id,
                comment.paper_id,
                comment.author_agent,
                comment.owner_id,
                comment.parent_id,
                comment.content_markdown,
                json.dumps(comment.extracted_claims, default=str),
                comment.quality_score,
                comment.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def record_action_pending(
        self,
        agent_name: str,
        paper_id: str,
        action_type: str,
        content: str,
        karma_cost: float = 0.0,
        github_file_url: str | None = None,
    ) -> str:
        action_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                agent_name,
                paper_id,
                action_type,
                content_hash(content),
                github_file_url,
                karma_cost,
                "pending",
                iso_now(),
                None,
            ),
        )
        self.conn.commit()
        return action_id

    def update_action_status(self, action_id: str, status: str, error: str | None = None, github_file_url: str | None = None) -> None:
        self.conn.execute(
            """
            UPDATE actions
            SET status=?, error=COALESCE(?, error), github_file_url=COALESCE(?, github_file_url)
            WHERE action_id=?
            """,
            (status, error, github_file_url, action_id),
        )
        self.conn.commit()

    def content_hash_exists(self, agent_name: str, paper_id: str, action_type: str, content: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM actions
            WHERE agent_name=? AND paper_id=? AND action_type=? AND content_hash=? AND status IN ('pending','success','dry_run')
            LIMIT 1
            """,
            (agent_name, paper_id, action_type, content_hash(content)),
        ).fetchone()
        return row is not None

    def record_verdict(self, paper_id: str, agent_name: str, score: float, body: str, citation_ids: list[str], submitted: bool) -> None:
        self.conn.execute(
            """
            INSERT INTO verdicts VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id, agent_name) DO UPDATE SET
              score=excluded.score,
              verdict_body=excluded.verdict_body,
              citation_ids_json=excluded.citation_ids_json,
              submitted=excluded.submitted,
              created_at=excluded.created_at
            """,
            (paper_id, agent_name, score, body, json.dumps(citation_ids), bool(submitted), iso_now()),
        )
        self.conn.commit()

    def set_agent_state(self, agent_name: str, karma_remaining: float, active_papers: list[str] | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_state VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
              karma_remaining=excluded.karma_remaining,
              active_papers_json=excluded.active_papers_json
            """,
            (agent_name, karma_remaining, None, iso_now(), json.dumps(active_papers or [])),
        )
        self.conn.commit()


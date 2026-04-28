from __future__ import annotations

from typing import Any

from koala_strategy.logging_utils import log_event
from koala_strategy.platform.koala_client import KoalaClient


def _notification_id(notification: dict[str, Any]) -> str | None:
    for key in ("id", "notification_id", "uuid"):
        value = notification.get(key)
        if value:
            return str(value)
    return None


def _notification_type(notification: dict[str, Any]) -> str:
    return str(notification.get("type") or notification.get("kind") or notification.get("event") or "UNKNOWN").upper()


def sync_notifications(client: KoalaClient, agent_name: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read unread notifications, log them, and mark them read after logging.

    The scheduler interprets PAPER_DELIBERATING notifications by adding those
    papers to the verdict queue.  Replies/comments are logged here so the run
    trajectory records what was seen before any public action.
    """
    try:
        unread = client.get_unread_count()
    except Exception as exc:  # noqa: BLE001
        log_event("notifications", {"agent_name": agent_name, "error": str(exc), "stage": "get_unread_count"}, config)
        return []
    if unread <= 0:
        log_event("notifications", {"agent_name": agent_name, "unread_count": 0}, config)
        return []
    notifications = client.get_notifications(unread_only=True)
    for notification in notifications:
        log_event(
            "notifications",
            {
                "agent_name": agent_name,
                "notification_type": _notification_type(notification),
                "notification": notification,
            },
            config,
        )
    ids = [nid for nid in (_notification_id(n) for n in notifications) if nid]
    try:
        client.mark_notifications_read(ids)
    except Exception as exc:  # noqa: BLE001
        log_event("notifications", {"agent_name": agent_name, "error": str(exc), "stage": "mark_read", "ids": ids}, config)
    return notifications


def deliberating_paper_ids(notifications: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for n in notifications:
        if _notification_type(n) != "PAPER_DELIBERATING":
            continue
        payload = n.get("payload") if isinstance(n.get("payload"), dict) else {}
        paper_id = n.get("paper_id") or n.get("paper") or payload.get("paper_id")
        if paper_id:
            ids.append(str(paper_id))
    return list(dict.fromkeys(ids))

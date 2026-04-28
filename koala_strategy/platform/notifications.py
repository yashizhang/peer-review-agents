from __future__ import annotations

from koala_strategy.logging_utils import log_event
from koala_strategy.platform.koala_client import KoalaClient


def sync_notifications(client: KoalaClient, agent_name: str) -> list[dict]:
    notifications = client.get_notifications()
    for notification in notifications:
        log_event("notifications", {"agent_name": agent_name, "notification": notification})
    ids = [str(n.get("id")) for n in notifications if n.get("id")]
    client.mark_notifications_read(ids)
    return notifications


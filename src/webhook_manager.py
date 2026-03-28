"""Event webhook system for external integrations.

Manages webhook subscriptions, delivers events via HTTP POST with
HMAC-SHA256 signatures, and maintains a delivery log in SQLite.
"""

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

logger = logging.getLogger("webhook_manager")

VALID_EVENTS = frozenset([
    "query.received",
    "query.matched",
    "query.unmatched",
    "escalation.triggered",
    "feedback.received",
    "faq.updated",
])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WebhookManager:
    """Manages webhook subscriptions and event delivery."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(BASE_DIR, "data", "webhooks.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    events TEXT NOT NULL,
                    secret TEXT,
                    created_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS delivery_log (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    url TEXT NOT NULL,
                    request_payload TEXT,
                    response_status INTEGER,
                    response_body TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_delivery_sub
                    ON delivery_log(subscription_id);
                CREATE INDEX IF NOT EXISTS idx_delivery_created
                    ON delivery_log(created_at);
            """)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, url: str, events: List[str], secret: Optional[str] = None) -> str:
        """Register a webhook subscription. Returns subscription_id."""
        if not url:
            raise ValueError("url is required")
        if not events:
            raise ValueError("at least one event type is required")

        for evt in events:
            if evt not in VALID_EVENTS:
                raise ValueError(f"invalid event type: {evt}")

        subscription_id = str(uuid.uuid4())
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO subscriptions (id, url, events, secret, created_at) VALUES (?, ?, ?, ?, ?)",
                (subscription_id, url, json.dumps(events), secret, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(f"Webhook registered: {subscription_id} -> {url} events={events}")
        return subscription_id

    def unregister(self, subscription_id: str) -> bool:
        """Remove a subscription. Returns True if found and removed."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE subscriptions SET active = 0 WHERE id = ? AND active = 1",
                (subscription_id,),
            )
            conn.commit()
            removed = cursor.rowcount > 0
        finally:
            conn.close()

        if removed:
            logger.info(f"Webhook unregistered: {subscription_id}")
        return removed

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        """Return all active subscriptions."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, url, events, secret, created_at FROM subscriptions WHERE active = 1 ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()

        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "url": row["url"],
                "events": json.loads(row["events"]),
                "has_secret": row["secret"] is not None,
                "created_at": row["created_at"],
            })
        return result

    def emit(self, event_type: str, payload: Dict[str, Any]) -> int:
        """Send webhook to all subscribers of this event type.

        Delivery happens asynchronously in background threads.
        Returns the number of matching subscriptions.
        """
        if event_type not in VALID_EVENTS:
            raise ValueError(f"invalid event type: {event_type}")

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, url, events, secret FROM subscriptions WHERE active = 1"
            ).fetchall()
        finally:
            conn.close()

        matching = []
        for row in rows:
            events = json.loads(row["events"])
            if event_type in events:
                matching.append({
                    "subscription_id": row["id"],
                    "url": row["url"],
                    "secret": row["secret"],
                })

        webhook_payload = {
            "event": event_type,
            "timestamp": time.time(),
            "data": payload,
        }

        for sub in matching:
            t = threading.Thread(
                target=self._retry,
                args=(sub["url"], webhook_payload, sub["secret"]),
                kwargs={"subscription_id": sub["subscription_id"]},
                daemon=True,
            )
            t.start()

        return len(matching)

    def get_delivery_log(
        self, subscription_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Return delivery history, optionally filtered by subscription_id."""
        conn = self._get_conn()
        try:
            if subscription_id:
                rows = conn.execute(
                    "SELECT * FROM delivery_log WHERE subscription_id = ? ORDER BY created_at DESC LIMIT ?",
                    (subscription_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM delivery_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()

        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "subscription_id": row["subscription_id"],
                "event_type": row["event_type"],
                "url": row["url"],
                "response_status": row["response_status"],
                "success": bool(row["success"]),
                "attempts": row["attempts"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })
        return result

    # ------------------------------------------------------------------
    # Internal delivery methods
    # ------------------------------------------------------------------

    @staticmethod
    def _sign_payload(payload_bytes: bytes, secret: str) -> str:
        """Compute HMAC-SHA256 signature."""
        return hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

    def _send_webhook(
        self, url: str, payload: Dict[str, Any], secret: Optional[str] = None
    ) -> tuple:
        """HTTP POST with JSON payload. Returns (status_code, response_body).

        Raises on network errors.
        """
        payload_bytes = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
        }
        if secret:
            sig = self._sign_payload(payload_bytes, secret)
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            return e.code, body

    def _retry(
        self,
        url: str,
        payload: Dict[str, Any],
        secret: Optional[str] = None,
        max_retries: int = 3,
        subscription_id: Optional[str] = None,
    ):
        """Exponential backoff retry for webhook delivery."""
        delivery_id = str(uuid.uuid4())
        created_at = time.time()
        event_type = payload.get("event", "unknown")

        # Log initial delivery attempt
        self._log_delivery(
            delivery_id=delivery_id,
            subscription_id=subscription_id or "",
            event_type=event_type,
            url=url,
            request_payload=json.dumps(payload),
            response_status=None,
            response_body=None,
            success=False,
            attempts=0,
            created_at=created_at,
            completed_at=None,
        )

        status = None
        body = ""
        success = False

        for attempt in range(1, max_retries + 1):
            try:
                status, body = self._send_webhook(url, payload, secret)
                if 200 <= status < 300:
                    success = True
                    break
            except Exception as e:
                logger.warning(
                    f"Webhook delivery attempt {attempt}/{max_retries} failed for {url}: {e}"
                )
                body = str(e)

            if attempt < max_retries:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                time.sleep(backoff)

        # Update delivery log with final result
        self._update_delivery(
            delivery_id=delivery_id,
            response_status=status,
            response_body=body[:1000] if body else None,
            success=success,
            attempts=attempt if 'attempt' in dir() else max_retries,
            completed_at=time.time(),
        )

        if not success:
            logger.error(
                f"Webhook delivery failed after {max_retries} attempts: {url} status={status}"
            )

    def _log_delivery(self, **kwargs):
        """Insert a delivery log record."""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO delivery_log
                   (id, subscription_id, event_type, url, request_payload,
                    response_status, response_body, success, attempts, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kwargs["delivery_id"],
                    kwargs["subscription_id"],
                    kwargs["event_type"],
                    kwargs["url"],
                    kwargs.get("request_payload"),
                    kwargs.get("response_status"),
                    kwargs.get("response_body"),
                    int(kwargs.get("success", False)),
                    kwargs.get("attempts", 0),
                    kwargs["created_at"],
                    kwargs.get("completed_at"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_delivery(self, **kwargs):
        """Update a delivery log record with final results."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE delivery_log
                   SET response_status = ?, response_body = ?, success = ?,
                       attempts = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    kwargs.get("response_status"),
                    kwargs.get("response_body"),
                    int(kwargs.get("success", False)),
                    kwargs.get("attempts", 0),
                    kwargs.get("completed_at"),
                    kwargs["delivery_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

"""In-app alert/notification center for admins.

Provides persistent alert storage via SQLite and an automated rule engine
that creates alerts based on system metrics (unmatched rate, satisfaction
score, FAQ quality).
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta


SEVERITY_LEVELS = ("info", "warning", "critical")
CATEGORIES = (
    "unmatched_surge",
    "satisfaction_drop",
    "law_change",
    "system_error",
    "security",
    "faq_quality",
)


class AlertCenter:
    """CRUD alert storage backed by SQLite."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "alerts.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    metadata TEXT,
                    is_read INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_alert(self, title: str, message: str, severity: str, category: str, metadata: dict | None = None) -> dict:
        """Create a new alert and return it as a dict."""
        if severity not in SEVERITY_LEVELS:
            raise ValueError(f"Invalid severity '{severity}'. Must be one of {SEVERITY_LEVELS}")
        if category not in CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of {CATEGORIES}")

        alert_id = uuid.uuid4().hex[:12]
        created_at = datetime.now().isoformat()
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO alerts (id, title, message, severity, category, metadata, is_read, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (alert_id, title, message, severity, category, metadata_json, created_at),
            )
            conn.commit()

        return {
            "id": alert_id,
            "title": title,
            "message": message,
            "severity": severity,
            "category": category,
            "metadata": metadata,
            "is_read": False,
            "created_at": created_at,
        }

    def get_alerts(self, unread_only: bool = False, severity: str | None = None,
                   category: str | None = None, limit: int = 50) -> list[dict]:
        """Query alerts with optional filters."""
        query = "SELECT * FROM alerts WHERE 1=1"
        params: list = []

        if unread_only:
            query += " AND is_read = 0"
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def mark_read(self, alert_id: str) -> bool:
        """Mark a single alert as read. Returns True if found."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_all_read(self) -> int:
        """Mark all alerts as read. Returns count of updated rows."""
        with self._get_conn() as conn:
            cursor = conn.execute("UPDATE alerts SET is_read = 1 WHERE is_read = 0")
            conn.commit()
            return cursor.rowcount

    def delete_alert(self, alert_id: str) -> bool:
        """Delete an alert. Returns True if found."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_unread_count(self) -> int:
        """Return the number of unread alerts."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM alerts WHERE is_read = 0").fetchone()
            return row["cnt"]

    def cleanup(self, days: int = 30) -> int:
        """Remove alerts older than *days*. Returns count of deleted rows."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM alerts WHERE created_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["is_read"] = bool(d["is_read"])
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d


class AlertRuleEngine:
    """Automated rule engine that checks system metrics and creates alerts."""

    def __init__(self, alert_center: AlertCenter,
                 realtime_monitor=None,
                 satisfaction_tracker=None,
                 faq_quality_checker=None):
        self.alert_center = alert_center
        self.realtime_monitor = realtime_monitor
        self.satisfaction_tracker = satisfaction_tracker
        self.faq_quality_checker = faq_quality_checker

    def check_unmatched_surge(self, threshold_pct: float = 20) -> dict | None:
        """Alert if unmatched rate exceeds *threshold_pct* percent."""
        if self.realtime_monitor is None:
            return None

        stats = self.realtime_monitor.get_live_stats()
        unmatched_rate = stats.get("unmatched_rate", 0.0) * 100

        if unmatched_rate > threshold_pct:
            return self.alert_center.create_alert(
                title="Unmatched rate surge detected",
                message=f"Unmatched rate is {unmatched_rate:.1f}% (threshold: {threshold_pct}%)",
                severity="warning",
                category="unmatched_surge",
                metadata={"unmatched_rate": unmatched_rate, "threshold_pct": threshold_pct},
            )
        return None

    def check_satisfaction_drop(self, threshold: float = 0.5) -> dict | None:
        """Alert if average satisfaction score drops below *threshold*."""
        if self.satisfaction_tracker is None:
            return None

        stats = self.satisfaction_tracker.get_satisfaction_stats()
        avg_score = stats.get("avg_satisfaction_score", 1.0)

        if avg_score < threshold:
            return self.alert_center.create_alert(
                title="Satisfaction score drop",
                message=f"Average satisfaction score is {avg_score:.4f} (threshold: {threshold})",
                severity="critical",
                category="satisfaction_drop",
                metadata={"avg_score": avg_score, "threshold": threshold},
            )
        return None

    def check_faq_quality(self, min_score: float = 70) -> dict | None:
        """Alert if FAQ quality score (0-100 scale) drops below *min_score*."""
        if self.faq_quality_checker is None:
            return None

        report = self.faq_quality_checker.check_all()
        # The checker returns score 0.0-1.0; convert to 0-100.
        score = report.get("score", 1.0) * 100

        if score < min_score:
            return self.alert_center.create_alert(
                title="FAQ quality below threshold",
                message=f"FAQ quality score is {score:.0f} (threshold: {min_score})",
                severity="warning",
                category="faq_quality",
                metadata={"score": score, "min_score": min_score, "issues_count": len(report.get("issues", []))},
            )
        return None

    def run_all_checks(self) -> list[dict]:
        """Run all rule checks and return any alerts that were created."""
        results = []
        for check_fn in (self.check_unmatched_surge, self.check_satisfaction_drop, self.check_faq_quality):
            result = check_fn()
            if result is not None:
                results.append(result)
        return results

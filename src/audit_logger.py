"""Audit logging system for admin actions.

Records all administrative operations (CRUD, login/logout, backup/restore)
in a SQLite database for compliance and security monitoring.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta


# Valid action types
VALID_ACTIONS = {
    "create", "update", "delete", "login", "logout",
    "export", "backup", "restore",
}

# Valid resource types
VALID_RESOURCE_TYPES = {
    "faq", "tenant", "webhook", "backup", "session", "config",
}


class AuditLogger:
    """Logs admin actions to a SQLite database for auditing purposes."""

    def __init__(self, db_path=None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "audit.db")
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_table()

    def _get_conn(self):
        """Return a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_table(self):
        """Create the audit_logs table if it does not exist."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                details TEXT,
                ip_address TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_logs (timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_actor
            ON audit_logs (actor)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_resource
            ON audit_logs (resource_type, resource_id)
        """)
        conn.commit()

    def log(self, actor, action, resource_type, resource_id=None,
            details=None, ip=None):
        """Record an audit event.

        Args:
            actor: Username of the admin performing the action.
            action: Action type (create, update, delete, login, etc.).
            resource_type: Type of resource affected.
            resource_id: Identifier of the specific resource.
            details: Optional dict with extra information (stored as JSON).
            ip: IP address of the request.

        Returns:
            The id of the inserted log entry.
        """
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action: {action}. Must be one of {VALID_ACTIONS}")
        if resource_type not in VALID_RESOURCE_TYPES:
            raise ValueError(
                f"Invalid resource_type: {resource_type}. "
                f"Must be one of {VALID_RESOURCE_TYPES}"
            )

        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        details_json = json.dumps(details, ensure_ascii=False) if details else None

        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO audit_logs
               (timestamp, actor, action, resource_type, resource_id, details, ip_address)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, actor, action, resource_type, resource_id,
             details_json, ip),
        )
        conn.commit()
        return cursor.lastrowid

    def get_logs(self, actor=None, action=None, resource_type=None,
                 since=None, limit=100):
        """Query audit logs with optional filters.

        Args:
            actor: Filter by actor username.
            action: Filter by action type.
            resource_type: Filter by resource type.
            since: Filter events after this ISO timestamp string.
            limit: Maximum number of results (default 100).

        Returns:
            List of log entry dicts.
        """
        conn = self._get_conn()
        conditions = []
        params = []

        if actor:
            conditions.append("actor = ?")
            params.append(actor)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM audit_logs {where_clause} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_log_count(self, since=None):
        """Count audit log entries.

        Args:
            since: Only count events after this ISO timestamp.

        Returns:
            Integer count.
        """
        conn = self._get_conn()
        if since:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE timestamp >= ?",
                (since,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()
        return row[0]

    def get_actor_activity(self, actor):
        """Get all actions by a specific admin.

        Args:
            actor: The admin username.

        Returns:
            List of log entry dicts for that actor.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE actor = ? ORDER BY id DESC",
            (actor,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_resource_history(self, resource_type, resource_id):
        """Get all changes to a specific resource.

        Args:
            resource_type: The resource type.
            resource_id: The resource identifier.

        Returns:
            List of log entry dicts for that resource.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM audit_logs
               WHERE resource_type = ? AND resource_id = ?
               ORDER BY id DESC""",
            (resource_type, resource_id),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def cleanup(self, days=90):
        """Remove log entries older than N days.

        Args:
            days: Number of days to retain (default 90).

        Returns:
            Number of deleted rows.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM audit_logs WHERE timestamp < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount

    def get_stats(self, since=None):
        """Get audit statistics: actions per day and top actors.

        Args:
            since: Only include events after this ISO timestamp.

        Returns:
            Dict with 'actions_per_day' and 'top_actors'.
        """
        conn = self._get_conn()
        params = []
        where_clause = ""
        if since:
            where_clause = "WHERE timestamp >= ?"
            params.append(since)

        # Actions per day
        rows = conn.execute(
            f"""SELECT SUBSTR(timestamp, 1, 10) as day, COUNT(*) as count
                FROM audit_logs {where_clause}
                GROUP BY day ORDER BY day DESC LIMIT 30""",
            params,
        ).fetchall()
        actions_per_day = [{"date": row[0], "count": row[1]} for row in rows]

        # Top actors
        rows = conn.execute(
            f"""SELECT actor, COUNT(*) as count
                FROM audit_logs {where_clause}
                GROUP BY actor ORDER BY count DESC LIMIT 10""",
            params,
        ).fetchall()
        top_actors = [{"actor": row[0], "count": row[1]} for row in rows]

        # Action breakdown
        rows = conn.execute(
            f"""SELECT action, COUNT(*) as count
                FROM audit_logs {where_clause}
                GROUP BY action ORDER BY count DESC""",
            params,
        ).fetchall()
        action_breakdown = [{"action": row[0], "count": row[1]} for row in rows]

        total = self.get_log_count(since=since)

        return {
            "total": total,
            "actions_per_day": actions_per_day,
            "top_actors": top_actors,
            "action_breakdown": action_breakdown,
        }

    def _row_to_dict(self, row):
        """Convert a sqlite3.Row to a plain dict, parsing JSON details."""
        d = dict(row)
        if d.get("details"):
            try:
                d["details"] = json.loads(d["details"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    def close(self):
        """Close the database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

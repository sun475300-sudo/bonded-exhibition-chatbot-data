"""
Satisfaction auto-tracker for the bonded exhibition hall (보세전시장) chatbot.
Automatically tracks answer quality within sessions using SQLite.
"""

import os
import sqlite3
from datetime import datetime


RESPONSE_TYPE_SCORES = {
    "faq_match": 1.0,
    "tfidf_match": 0.7,
    "escalation": 0.5,
    "unknown": 0.2,
}

RE_ASK_PENALTY = 0.3


class SatisfactionTracker:
    def __init__(self):
        db_dir = "logs"
        os.makedirs(db_dir, exist_ok=True)
        self.db_path = os.path.join(db_dir, "satisfaction.db")
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS satisfaction (
                    session_id TEXT,
                    query TEXT,
                    response_type TEXT,
                    re_asked BOOLEAN DEFAULT 0,
                    feedback TEXT DEFAULT 'none',
                    timestamp TEXT
                )
                """
            )
            conn.commit()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def track_response(self, session_id: str, query: str, response_type: str):
        """Records a response event."""
        timestamp = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO satisfaction (session_id, query, response_type, re_asked, feedback, timestamp) "
                "VALUES (?, ?, ?, 0, 'none', ?)",
                (session_id, query, response_type, timestamp),
            )
            conn.commit()

    def detect_re_ask(
        self, session_id: str, current_query: str, history: list
    ) -> bool:
        """
        Checks if current query is semantically similar to a previous query
        in the same session using simple token overlap ratio > 0.5.
        A re-ask suggests the previous answer was unsatisfactory.
        """
        current_tokens = set(current_query.lower().split())
        if not current_tokens:
            return False

        for entry in history:
            prev_query = entry.get("query", "")
            prev_tokens = set(prev_query.lower().split())
            if not prev_tokens:
                continue
            overlap = len(current_tokens & prev_tokens)
            union = len(current_tokens | prev_tokens)
            if union > 0 and (overlap / union) > 0.5:
                return True

        return False

    def mark_re_ask(self, session_id: str, query: str):
        """Updates the previous similar query's re_asked flag to True."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE satisfaction SET re_asked = 1 "
                "WHERE session_id = ? AND query = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (session_id, query),
            )
            conn.commit()

    def get_satisfaction_stats(self) -> dict:
        """
        Returns satisfaction statistics:
        - total_queries
        - re_ask_rate (percentage of queries that triggered re-asks)
        - response_type_distribution
        - avg_satisfaction_score
        """
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute("SELECT COUNT(*) AS cnt FROM satisfaction").fetchone()
            total_queries = row["cnt"]

            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM satisfaction WHERE re_asked = 1"
            ).fetchone()
            re_ask_count = row["cnt"]

            re_ask_rate = (re_ask_count / total_queries * 100) if total_queries > 0 else 0.0

            rows = conn.execute(
                "SELECT response_type, COUNT(*) AS cnt FROM satisfaction GROUP BY response_type"
            ).fetchall()
            response_type_distribution = {r["response_type"]: r["cnt"] for r in rows}

            rows = conn.execute(
                "SELECT response_type, re_asked FROM satisfaction"
            ).fetchall()
            if rows:
                scores = []
                for r in rows:
                    base = RESPONSE_TYPE_SCORES.get(r["response_type"], 0.2)
                    penalty = RE_ASK_PENALTY if r["re_asked"] else 0.0
                    scores.append(max(base - penalty, 0.0))
                avg_satisfaction_score = sum(scores) / len(scores)
            else:
                avg_satisfaction_score = 0.0

        return {
            "total_queries": total_queries,
            "re_ask_rate": re_ask_rate,
            "response_type_distribution": response_type_distribution,
            "avg_satisfaction_score": round(avg_satisfaction_score, 4),
        }

    def get_low_satisfaction_queries(self, limit: int = 20) -> list:
        """Returns queries with lowest satisfaction scores."""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, query, response_type, re_asked, feedback, timestamp "
                "FROM satisfaction"
            ).fetchall()

        scored = []
        for r in rows:
            base = RESPONSE_TYPE_SCORES.get(r["response_type"], 0.2)
            penalty = RE_ASK_PENALTY if r["re_asked"] else 0.0
            score = max(base - penalty, 0.0)
            scored.append(
                {
                    "session_id": r["session_id"],
                    "query": r["query"],
                    "response_type": r["response_type"],
                    "re_asked": bool(r["re_asked"]),
                    "feedback": r["feedback"],
                    "timestamp": r["timestamp"],
                    "satisfaction_score": round(score, 4),
                }
            )

        scored.sort(key=lambda x: x["satisfaction_score"])
        return scored[:limit]

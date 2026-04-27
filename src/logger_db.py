"""SQLite 기반 챗봇 질문 로그 시스템.

모든 사용자 질문과 응답 정보를 기록하고 통계를 제공한다.
"""

import os
import sqlite3
import threading
from datetime import datetime


class ChatLogger:
    """챗봇 질문/응답 로그를 SQLite에 저장하고 조회하는 클래스."""

    def __init__(self, db_path="logs/chat_logs.db"):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_table()

    def _get_conn(self):
        """스레드별 SQLite 연결을 반환한다.

        F3: WAL 모드 활성화 — 동시 읽기/쓰기 처리량 향상 + 크래시 복원성.
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            try:
                self._local.conn.execute("PRAGMA journal_mode=WAL")
                self._local.conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error:
                pass  # 일부 환경(read-only DB 등)에서 PRAGMA 실패 허용
        return self._local.conn

    def _init_table(self):
        """로그 테이블이 없으면 생성한다."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                query TEXT NOT NULL,
                category TEXT,
                faq_id TEXT,
                is_escalation INTEGER NOT NULL DEFAULT 0,
                response_preview TEXT
            )
        """)
        conn.commit()

    def log_query(self, query, category=None, faq_id=None,
                  is_escalation=False, response_preview=None):
        """질문 로그를 저장한다."""
        conn = self._get_conn()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        preview = response_preview[:200] if response_preview else None
        conn.execute(
            """INSERT INTO chat_logs
               (timestamp, query, category, faq_id, is_escalation, response_preview)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp, query, category, faq_id,
             1 if is_escalation else 0, preview),
        )
        conn.commit()

    def get_stats(self):
        """통계를 반환한다.

        Returns:
            dict: total_queries, category_distribution,
                  escalation_rate, unmatched_rate
        """
        conn = self._get_conn()

        total = conn.execute(
            "SELECT COUNT(*) FROM chat_logs"
        ).fetchone()[0]

        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM chat_logs GROUP BY category"
        ).fetchall()
        category_distribution = {
            (row["category"] or "UNKNOWN"): row["cnt"] for row in rows
        }

        escalation_count = conn.execute(
            "SELECT COUNT(*) FROM chat_logs WHERE is_escalation = 1"
        ).fetchone()[0]

        unmatched_count = conn.execute(
            "SELECT COUNT(*) FROM chat_logs WHERE faq_id IS NULL"
        ).fetchone()[0]

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM chat_logs WHERE timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]

        return {
            "total_queries": total,
            "today_queries": today_count,
            "category_distribution": category_distribution,
            "escalation_rate": (
                round(escalation_count / total * 100, 1) if total > 0 else 0
            ),
            "unmatched_rate": (
                round(unmatched_count / total * 100, 1) if total > 0 else 0
            ),
        }

    def get_recent_logs(self, limit=50):
        """최근 로그를 반환한다."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM chat_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_unmatched_queries(self, limit=20):
        """FAQ에 매칭되지 않은 질문 목록을 반환한다."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT query, category, timestamp FROM chat_logs
               WHERE faq_id IS NULL
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self):
        """DB 연결을 닫는다."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

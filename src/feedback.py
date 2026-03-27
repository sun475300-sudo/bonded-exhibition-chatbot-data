"""사용자 만족도 피드백 시스템.

챗봇 응답에 대한 사용자 피드백을 수집하고 통계를 제공한다.
"""

import os
import sqlite3
import threading
from datetime import datetime


class FeedbackManager:
    """사용자 피드백을 SQLite에 저장하고 통계를 조회하는 클래스."""

    def __init__(self, db_path="logs/feedback.db"):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_table()

    def _get_conn(self):
        """스레드별 SQLite 연결을 반환한다."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_table(self):
        """피드백 테이블이 없으면 생성한다."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                rating TEXT NOT NULL,
                comment TEXT DEFAULT ''
            )
        """)
        conn.commit()

    def save_feedback(self, query_id, rating, comment=""):
        """피드백을 저장한다.

        Args:
            query_id: 질문 식별자
            rating: 'helpful' 또는 'unhelpful'
            comment: 추가 코멘트 (선택)

        Returns:
            int: 저장된 피드백 ID

        Raises:
            ValueError: rating이 유효하지 않은 경우
        """
        if rating not in ("helpful", "unhelpful"):
            raise ValueError(f"rating은 'helpful' 또는 'unhelpful'이어야 합니다: {rating}")

        conn = self._get_conn()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """INSERT INTO feedback (query_id, timestamp, rating, comment)
               VALUES (?, ?, ?, ?)""",
            (query_id, timestamp, rating, comment or ""),
        )
        conn.commit()
        return cursor.lastrowid

    def get_feedback_stats(self):
        """전체 및 일별 만족도 통계를 반환한다.

        Returns:
            dict: total, helpful_count, unhelpful_count,
                  helpful_rate, daily_stats
        """
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        helpful = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = 'helpful'"
        ).fetchone()[0]
        unhelpful = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE rating = 'unhelpful'"
        ).fetchone()[0]

        daily_rows = conn.execute(
            """SELECT DATE(timestamp) as date,
                      SUM(CASE WHEN rating = 'helpful' THEN 1 ELSE 0 END) as helpful,
                      SUM(CASE WHEN rating = 'unhelpful' THEN 1 ELSE 0 END) as unhelpful,
                      COUNT(*) as total
               FROM feedback
               GROUP BY DATE(timestamp)
               ORDER BY date DESC
               LIMIT 30"""
        ).fetchall()

        daily_stats = [
            {
                "date": row["date"],
                "helpful": row["helpful"],
                "unhelpful": row["unhelpful"],
                "total": row["total"],
            }
            for row in daily_rows
        ]

        return {
            "total": total,
            "helpful_count": helpful,
            "unhelpful_count": unhelpful,
            "helpful_rate": round(helpful / total * 100, 1) if total > 0 else 0,
            "daily_stats": daily_stats,
        }

    def get_low_rated_queries(self, limit=20):
        """낮은 평가를 받은 질문 목록을 반환한다.

        Args:
            limit: 반환할 최대 개수

        Returns:
            list[dict]: query_id, timestamp, comment 목록
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT query_id, timestamp, comment
               FROM feedback
               WHERE rating = 'unhelpful'
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self):
        """DB 연결을 닫는다."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

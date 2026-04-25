"""사용자 맞춤 FAQ 추천 모듈.

사용자 질문 이력을 기반으로 개인화된 FAQ 추천, 인기 FAQ, 트렌딩 토픽을 제공한다.
"""

import os
import sqlite3
import time
from contextlib import contextmanager


class UserRecommender:
    """사용자 질문 이력 기반 개인화 FAQ 추천 클래스."""

    def __init__(self, db_path: str = None):
        """초기화.

        Args:
            db_path: SQLite DB 파일 경로. 기본값은 data/user_profiles.db.
        """
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "user_profiles.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """데이터베이스 테이블을 초기화한다."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    category TEXT NOT NULL,
                    faq_id TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_history_session
                ON query_history (session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_history_timestamp
                ON query_history (timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_history_category
                ON query_history (category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_history_faq_id
                ON query_history (faq_id)
            """)

    @contextmanager
    def _get_conn(self):
        """SQLite 연결을 컨텍스트 매니저로 제공한다."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_query(self, session_id: str, query: str, category: str, faq_id: str = None):
        """사용자 질문을 기록한다.

        Args:
            session_id: 세션 ID.
            query: 사용자 질문.
            category: 질문 카테고리.
            faq_id: 매칭된 FAQ ID (없으면 None).
        """
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO query_history (session_id, query, category, faq_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, query, category, faq_id, time.time()),
            )

    def get_recommendations(self, session_id: str, top_n: int = 5) -> list[dict]:
        """개인화된 FAQ 추천을 반환한다.

        추천 알고리즘:
        1. 사용자가 자주 조회한 카테고리에서 아직 조회하지 않은 FAQ 추천
        2. 최근 질문에 높은 가중치 부여 (recency)
        3. 자주 조회한 카테고리에 높은 가중치 부여 (frequency)
        4. 협업 필터링: 유사 세션의 FAQ 추천 (co-occurrence)

        Args:
            session_id: 세션 ID.
            top_n: 반환할 최대 추천 수.

        Returns:
            추천 FAQ 리스트. 각 항목: {"faq_id": ..., "category": ..., "score": ...}
        """
        with self._get_conn() as conn:
            # 사용자 이력 조회
            rows = conn.execute(
                "SELECT faq_id, category, timestamp FROM query_history "
                "WHERE session_id = ? AND faq_id IS NOT NULL "
                "ORDER BY timestamp DESC",
                (session_id,),
            ).fetchall()

            if not rows:
                return self.get_popular_faqs(limit=top_n)

            now = time.time()
            visited_faq_ids = set()
            category_scores: dict[str, float] = {}

            for row in rows:
                visited_faq_ids.add(row["faq_id"])
                cat = row["category"]
                # Recency weight: exponential decay over 24 hours
                age_hours = (now - row["timestamp"]) / 3600.0
                recency_weight = 1.0 / (1.0 + age_hours / 24.0)
                category_scores[cat] = category_scores.get(cat, 0) + recency_weight

            # Category-based recommendations: popular FAQs in user's preferred categories
            candidates: dict[str, float] = {}

            # Get FAQs from preferred categories that the user hasn't seen
            if category_scores:
                placeholders = ",".join("?" * len(category_scores))
                cat_rows = conn.execute(
                    f"SELECT faq_id, category, COUNT(*) as cnt FROM query_history "
                    f"WHERE category IN ({placeholders}) AND faq_id IS NOT NULL "
                    f"GROUP BY faq_id, category "
                    f"ORDER BY cnt DESC",
                    list(category_scores.keys()),
                ).fetchall()

                for cr in cat_rows:
                    fid = cr["faq_id"]
                    if fid not in visited_faq_ids:
                        cat_weight = category_scores.get(cr["category"], 1.0)
                        candidates[fid] = candidates.get(fid, 0) + cr["cnt"] * cat_weight

            # Collaborative filtering: find similar sessions
            collab_scores = self._collaborative_filter(conn, session_id, visited_faq_ids)
            for fid, score in collab_scores.items():
                candidates[fid] = candidates.get(fid, 0) + score

            # Sort by score and return top_n
            sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])[:top_n]

            results = []
            for faq_id, score in sorted_candidates:
                # Look up category for this faq_id
                cat_row = conn.execute(
                    "SELECT category FROM query_history WHERE faq_id = ? LIMIT 1",
                    (faq_id,),
                ).fetchone()
                results.append({
                    "faq_id": faq_id,
                    "category": cat_row["category"] if cat_row else "GENERAL",
                    "score": round(score, 4),
                })

            return results

    def _collaborative_filter(
        self, conn: sqlite3.Connection, session_id: str, visited_faq_ids: set
    ) -> dict[str, float]:
        """협업 필터링: 유사 세션이 조회한 FAQ를 추천한다.

        카테고리 오버랩 기반으로 유사 세션을 찾고,
        그 세션들이 조회한 FAQ에 가중치를 부여한다.

        Args:
            conn: SQLite 연결.
            session_id: 현재 세션 ID.
            visited_faq_ids: 현재 사용자가 이미 본 FAQ ID 집합.

        Returns:
            {faq_id: score} 딕셔너리.
        """
        # Get current user's categories
        user_cats = conn.execute(
            "SELECT DISTINCT category FROM query_history WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        user_cat_set = {r["category"] for r in user_cats}

        if not user_cat_set:
            return {}

        # Find other sessions that share categories
        placeholders = ",".join("?" * len(user_cat_set))
        similar_sessions = conn.execute(
            f"SELECT session_id, COUNT(DISTINCT category) as overlap "
            f"FROM query_history "
            f"WHERE category IN ({placeholders}) AND session_id != ? "
            f"GROUP BY session_id "
            f"HAVING overlap >= 1 "
            f"ORDER BY overlap DESC "
            f"LIMIT 20",
            list(user_cat_set) + [session_id],
        ).fetchall()

        scores: dict[str, float] = {}
        for ss in similar_sessions:
            similarity = ss["overlap"] / max(len(user_cat_set), 1)
            # Get FAQs from this similar session
            faq_rows = conn.execute(
                "SELECT faq_id FROM query_history "
                "WHERE session_id = ? AND faq_id IS NOT NULL",
                (ss["session_id"],),
            ).fetchall()
            for fr in faq_rows:
                fid = fr["faq_id"]
                if fid not in visited_faq_ids:
                    scores[fid] = scores.get(fid, 0) + similarity

        return scores

    def get_popular_faqs(self, limit: int = 10) -> list[dict]:
        """전체 인기 FAQ를 반환한다.

        Args:
            limit: 반환할 최대 개수.

        Returns:
            인기 FAQ 리스트. 각 항목: {"faq_id": ..., "category": ..., "count": ...}
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT faq_id, category, COUNT(*) as cnt FROM query_history "
                "WHERE faq_id IS NOT NULL "
                "GROUP BY faq_id "
                "ORDER BY cnt DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()

            return [
                {"faq_id": row["faq_id"], "category": row["category"], "count": row["cnt"]}
                for row in rows
            ]

    def get_trending_topics(self, hours: int = 24, limit: int = 5) -> list[dict]:
        """최근 트렌딩 토픽(카테고리)을 반환한다.

        Args:
            hours: 최근 몇 시간 이내의 데이터를 사용할지.
            limit: 반환할 최대 개수.

        Returns:
            트렌딩 토픽 리스트. 각 항목: {"category": ..., "count": ..., "trend_score": ...}
        """
        now = time.time()
        cutoff = now - hours * 3600

        with self._get_conn() as conn:
            # Recent counts per category
            recent_rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM query_history "
                "WHERE timestamp >= ? "
                "GROUP BY category "
                "ORDER BY cnt DESC "
                "LIMIT ?",
                (cutoff, limit),
            ).fetchall()

            if not recent_rows:
                return []

            # Overall counts for comparison (trend score)
            total_rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM query_history "
                "GROUP BY category",
            ).fetchall()
            total_map = {r["category"]: r["cnt"] for r in total_rows}

            results = []
            for row in recent_rows:
                cat = row["category"]
                recent_cnt = row["cnt"]
                total_cnt = total_map.get(cat, recent_cnt)
                # Trend score: ratio of recent to total (higher means more trending)
                trend_score = recent_cnt / max(total_cnt, 1)
                results.append({
                    "category": cat,
                    "count": recent_cnt,
                    "trend_score": round(trend_score, 4),
                })

            # Sort by trend_score descending
            results.sort(key=lambda x: -x["trend_score"])
            return results[:limit]

    def get_user_profile(self, session_id: str) -> dict:
        """사용자 프로필을 반환한다.

        Args:
            session_id: 세션 ID.

        Returns:
            사용자 프로필 딕셔너리:
            {
                "session_id": ...,
                "visit_count": 총 질문 수,
                "preferred_categories": [선호 카테고리 리스트],
                "recent_queries": [최근 질문 리스트],
                "first_visit": 첫 방문 시간,
                "last_visit": 마지막 방문 시간,
            }
        """
        with self._get_conn() as conn:
            # Visit count
            count_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM query_history WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            visit_count = count_row["cnt"] if count_row else 0

            if visit_count == 0:
                return {
                    "session_id": session_id,
                    "visit_count": 0,
                    "preferred_categories": [],
                    "recent_queries": [],
                    "first_visit": None,
                    "last_visit": None,
                }

            # Preferred categories (ordered by frequency)
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM query_history "
                "WHERE session_id = ? GROUP BY category ORDER BY cnt DESC",
                (session_id,),
            ).fetchall()
            preferred_categories = [r["category"] for r in cat_rows]

            # Recent queries
            recent_rows = conn.execute(
                "SELECT query, category, faq_id, timestamp FROM query_history "
                "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 10",
                (session_id,),
            ).fetchall()
            recent_queries = [
                {
                    "query": r["query"],
                    "category": r["category"],
                    "faq_id": r["faq_id"],
                    "timestamp": r["timestamp"],
                }
                for r in recent_rows
            ]

            # First and last visit
            time_row = conn.execute(
                "SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts "
                "FROM query_history WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            return {
                "session_id": session_id,
                "visit_count": visit_count,
                "preferred_categories": preferred_categories,
                "recent_queries": recent_queries,
                "first_visit": time_row["first_ts"],
                "last_visit": time_row["last_ts"],
            }

    def get_related_by_history(self, session_id: str, current_category: str) -> list[dict]:
        """사용자 이력에서 관련 카테고리의 FAQ를 추천한다.

        사용자가 과거에 방문한 다른 카테고리에서 인기 FAQ를 추천한다.

        Args:
            session_id: 세션 ID.
            current_category: 현재 질문의 카테고리.

        Returns:
            추천 FAQ 리스트. 각 항목: {"faq_id": ..., "category": ..., "count": ...}
        """
        with self._get_conn() as conn:
            # Get user's other categories (excluding current)
            cat_rows = conn.execute(
                "SELECT DISTINCT category FROM query_history "
                "WHERE session_id = ? AND category != ?",
                (session_id, current_category),
            ).fetchall()
            other_cats = [r["category"] for r in cat_rows]

            if not other_cats:
                return []

            # Get popular FAQs from those categories
            placeholders = ",".join("?" * len(other_cats))
            faq_rows = conn.execute(
                f"SELECT faq_id, category, COUNT(*) as cnt FROM query_history "
                f"WHERE category IN ({placeholders}) AND faq_id IS NOT NULL "
                f"GROUP BY faq_id "
                f"ORDER BY cnt DESC "
                f"LIMIT 5",
                other_cats,
            ).fetchall()

            return [
                {"faq_id": row["faq_id"], "category": row["category"], "count": row["cnt"]}
                for row in faq_rows
            ]

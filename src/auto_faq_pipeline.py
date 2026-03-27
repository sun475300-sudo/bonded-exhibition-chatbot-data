"""자동 FAQ 등록 파이프라인.

미매칭 질문 중 빈도가 높은 항목을 FAQ 후보로 관리하고,
승인 시 faq.json에 자동 추가한다.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime


class AutoFAQPipeline:
    """FAQ 후보 관리 및 자동 등록 파이프라인 클래스."""

    def __init__(self, faq_recommender, faq_path="data/faq.json"):
        """FAQRecommender와 FAQ 파일 경로를 받아 초기화한다.

        Args:
            faq_recommender: FAQRecommender 인스턴스
            faq_path: FAQ JSON 파일 경로
        """
        self.faq_recommender = faq_recommender
        self.faq_path = faq_path
        self._local = threading.local()

        # 후보 상태 관리용 SQLite DB (faq_path 옆에 생성)
        faq_dir = os.path.dirname(os.path.abspath(faq_path))
        self.db_path = os.path.join(faq_dir, "faq_pipeline.db")
        os.makedirs(faq_dir, exist_ok=True)
        self._init_table()

    def _get_conn(self):
        """스레드별 SQLite 연결을 반환한다."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_table(self):
        """후보 테이블이 없으면 생성한다."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faq_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggested_question TEXT NOT NULL,
                suggested_category TEXT DEFAULT '',
                similar_queries TEXT DEFAULT '[]',
                frequency INTEGER DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def get_pending_candidates(self, min_frequency=3):
        """빈도 3회 이상인 미매칭 질문을 FAQ 후보로 반환한다.

        FAQRecommender에서 추천을 가져와 기존 후보에 없는 것만 추가한 뒤,
        pending 상태의 후보 목록을 반환한다.

        Args:
            min_frequency: 최소 빈도 (기본 3)

        Returns:
            list[dict]: 후보 목록
        """
        # 새 추천 동기화
        self._sync_recommendations(min_frequency)

        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM faq_candidates
               WHERE status = 'pending' AND frequency >= ?
               ORDER BY frequency DESC""",
            (min_frequency,),
        ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def _sync_recommendations(self, min_frequency):
        """FAQRecommender 추천 결과를 후보 테이블에 동기화한다."""
        recommendations = self.faq_recommender.get_recommendations(top_k=50)
        conn = self._get_conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for rec in recommendations:
            if rec["frequency"] < min_frequency:
                continue

            question = rec["suggested_question"]
            # 이미 등록된 후보인지 확인
            existing = conn.execute(
                "SELECT id, status FROM faq_candidates WHERE suggested_question = ?",
                (question,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """INSERT INTO faq_candidates
                       (suggested_question, suggested_category, similar_queries,
                        frequency, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                    (
                        question,
                        rec.get("suggested_category", ""),
                        json.dumps(rec.get("similar_queries", []), ensure_ascii=False),
                        rec["frequency"],
                        now,
                        now,
                    ),
                )
            elif existing["status"] == "pending":
                # 빈도 업데이트
                conn.execute(
                    """UPDATE faq_candidates
                       SET frequency = ?, updated_at = ?
                       WHERE id = ?""",
                    (rec["frequency"], now, existing["id"]),
                )

        conn.commit()

    def approve_candidate(self, candidate_id):
        """후보를 승인하여 faq.json에 자동 추가한다.

        Args:
            candidate_id: 후보 ID

        Returns:
            dict: 승인된 후보 정보 (추가된 FAQ 포함)

        Raises:
            ValueError: 후보가 없거나 pending 상태가 아닌 경우
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM faq_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"후보 ID {candidate_id}를 찾을 수 없습니다.")

        if row["status"] != "pending":
            raise ValueError(
                f"후보 ID {candidate_id}는 이미 {row['status']} 상태입니다."
            )

        candidate = self._row_to_dict(row)

        # FAQ draft 생성
        similar = json.loads(row["similar_queries"])
        draft = self.faq_recommender.generate_faq_draft(
            similar if similar else [row["suggested_question"]]
        )

        # faq.json에 추가
        self._add_to_faq_json(draft)

        # 상태 업데이트
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE faq_candidates
               SET status = 'approved', updated_at = ?
               WHERE id = ?""",
            (now, candidate_id),
        )
        conn.commit()

        candidate["status"] = "approved"
        candidate["faq_draft"] = draft
        return candidate

    def reject_candidate(self, candidate_id):
        """후보를 거부한다.

        Args:
            candidate_id: 후보 ID

        Returns:
            dict: 거부된 후보 정보

        Raises:
            ValueError: 후보가 없거나 pending 상태가 아닌 경우
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM faq_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"후보 ID {candidate_id}를 찾을 수 없습니다.")

        if row["status"] != "pending":
            raise ValueError(
                f"후보 ID {candidate_id}는 이미 {row['status']} 상태입니다."
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE faq_candidates
               SET status = 'rejected', updated_at = ?
               WHERE id = ?""",
            (now, candidate_id),
        )
        conn.commit()

        candidate = self._row_to_dict(row)
        candidate["status"] = "rejected"
        return candidate

    def _add_to_faq_json(self, faq_draft):
        """FAQ draft를 faq.json 파일에 추가한다."""
        if not os.path.exists(self.faq_path):
            faq_data = {"faq_version": "3.0.0", "last_updated": "", "items": []}
        else:
            with open(self.faq_path, "r", encoding="utf-8") as f:
                faq_data = json.load(f)

        # source_queries 제거 (faq.json에 불필요)
        new_item = {k: v for k, v in faq_draft.items() if k != "source_queries"}
        faq_data["items"].append(new_item)
        faq_data["last_updated"] = datetime.now().strftime("%Y-%m-%d")

        with open(self.faq_path, "w", encoding="utf-8") as f:
            json.dump(faq_data, f, ensure_ascii=False, indent=2)

    def _row_to_dict(self, row):
        """sqlite3.Row를 dict로 변환한다."""
        d = dict(row)
        # similar_queries를 JSON 파싱
        if "similar_queries" in d and isinstance(d["similar_queries"], str):
            try:
                d["similar_queries"] = json.loads(d["similar_queries"])
            except (json.JSONDecodeError, TypeError):
                d["similar_queries"] = []
        return d

    def get_all_candidates(self):
        """모든 후보를 반환한다 (상태 무관).

        Returns:
            list[dict]: 전체 후보 목록
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM faq_candidates ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def close(self):
        """DB 연결을 닫는다."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

"""사용자 세분화 모듈.

사용자의 질문 복잡도, 법률 용어 사용, 질문 구체성을 분석하여
beginner / intermediate / expert 등급으로 분류하고,
등급에 맞는 답변 깊이를 조절한다.
"""

import os
import re
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 법률 전문 용어
LEGAL_TERMS = [
    "관세법", "관세율", "관세청", "보세구역", "보세전시장", "보세창고", "보세운송",
    "보세공장", "보세판매장", "수입신고", "수출신고", "통관", "관세평가",
    "과세가격", "관세감면", "관세환급", "원산지", "원산지증명", "FTA",
    "HS코드", "품목분류", "세번", "세율", "부과고지", "납세의무",
    "수정신고", "경정청구", "심사청구", "불복", "이의신청", "심판청구",
    "행정소송", "가산세", "체납처분", "반입", "반출", "장치기간",
    "특허보세구역", "종합보세구역", "보세운송업자", "관세사", "통관업",
    "수입요건", "검역", "검사", "적하목록", "선하증권", "B/L",
    "인보이스", "패킹리스트", "원산지결정기준", "부가가치세", "개별소비세",
    "덤핑방지관세", "상계관세", "긴급관세", "조정관세", "할당관세",
    "AEO", "UNI-PASS", "전자통관", "EDI", "관세행정",
    "제190조", "제176조", "시행령", "시행규칙", "고시", "훈령",
]

# 기술적 전문 용어 (jargon)
TECHNICAL_JARGON = [
    "보증금", "담보", "특허", "허가", "인가", "승인", "등록", "취소",
    "갱신", "연장", "폐업", "양수양도", "합병", "분할", "법인",
    "대리인", "위임장", "인감증명", "사업자등록", "세금계산서",
    "영세율", "면세", "과세", "비과세", "간이과세", "일반과세",
    "수입면허", "수출면허", "허가품목", "금지품목", "제한품목",
    "지식재산권", "상표권", "특허권", "저작권", "병행수입",
    "보세화물", "내국물품", "외국물품", "혼합물품",
]

# 조문 참조 패턴 (article references)
ARTICLE_REFERENCE_PATTERNS = [
    r"제\s*\d+조",
    r"제\s*\d+항",
    r"제\s*\d+호",
    r"시행령\s*제?\s*\d+",
    r"시행규칙\s*제?\s*\d+",
    r"고시\s*제?\s*\d+",
    r"별표\s*\d+",
    r"관세법\s*제?\s*\d+",
]

# 초보 지표 (beginner indicator words)
BEGINNER_INDICATORS = [
    "뭐예요", "뭔가요", "무엇인가요", "무엇인지", "알려주세요",
    "어떻게", "방법", "처음", "초보", "잘 모르", "몰라",
    "기본", "간단히", "쉽게", "이해가 안", "설명해",
    "뭐가 다른", "차이점", "궁금", "도와주세요",
]

# 답변 깊이 조절에 사용할 설명 접미사
BEGINNER_SUFFIX = "\n\n💡 참고: 추가로 궁금한 점이 있으시면 편하게 질문해 주세요. 용어가 어려우시면 '쉽게 설명해 주세요'라고 말씀해 주세요."
EXPERT_PREFIX = "📋 요약: "


class TermComplexityScorer:
    """질문의 용어 복잡도를 0-1 점수로 평가한다."""

    def __init__(self):
        self._legal_terms_lower = [t.lower() for t in LEGAL_TERMS]
        self._jargon_lower = [t.lower() for t in TECHNICAL_JARGON]
        self._article_patterns = [re.compile(p) for p in ARTICLE_REFERENCE_PATTERNS]

    def score_query(self, query: str) -> float:
        """질문의 복잡도를 0-1 점수로 반환한다.

        구성:
        - 법률 용어 비율 (40%)
        - 조문 참조 (30%)
        - 기술 전문 용어 (20%)
        - 질문 길이 (10%)
        """
        if not query or not query.strip():
            return 0.0

        query_lower = query.lower()

        # 법률 용어 매칭
        legal_count = sum(1 for term in self._legal_terms_lower if term in query_lower)
        legal_score = min(legal_count / 5.0, 1.0)  # 5개 이상이면 만점

        # 조문 참조 매칭
        article_count = sum(1 for p in self._article_patterns if p.search(query))
        article_score = min(article_count / 2.0, 1.0)  # 2개 이상이면 만점

        # 기술 전문 용어 매칭
        jargon_count = sum(1 for term in self._jargon_lower if term in query_lower)
        jargon_score = min(jargon_count / 3.0, 1.0)  # 3개 이상이면 만점

        # 질문 길이 점수 (긴 질문은 보통 더 구체적)
        length_score = min(len(query) / 200.0, 1.0)

        total = (
            legal_score * 0.4
            + article_score * 0.3
            + jargon_score * 0.2
            + length_score * 0.1
        )
        return round(min(total, 1.0), 4)

    def has_legal_terms(self, query: str) -> bool:
        """질문에 법률 용어가 포함되어 있는지 확인한다."""
        query_lower = query.lower()
        return any(term in query_lower for term in self._legal_terms_lower)

    def has_article_references(self, query: str) -> bool:
        """질문에 조문 참조가 포함되어 있는지 확인한다."""
        return any(p.search(query) for p in self._article_patterns)

    def has_jargon(self, query: str) -> bool:
        """질문에 기술 전문 용어가 포함되어 있는지 확인한다."""
        query_lower = query.lower()
        return any(term in query_lower for term in self._jargon_lower)


class UserSegmenter:
    """사용자 질문 패턴을 분석하여 세그먼트로 분류한다."""

    SEGMENTS = ("beginner", "intermediate", "expert")

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.path.join(BASE_DIR, "data", "segments.db")
        self.db_path = db_path
        self.scorer = TermComplexityScorer()
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """SQLite 데이터베이스 초기화."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_segments (
                    session_id TEXT PRIMARY KEY,
                    segment TEXT NOT NULL DEFAULT 'beginner',
                    query_count INTEGER NOT NULL DEFAULT 0,
                    total_complexity REAL NOT NULL DEFAULT 0.0,
                    avg_complexity REAL NOT NULL DEFAULT 0.0,
                    last_query TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS segment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    complexity_score REAL NOT NULL,
                    segment_before TEXT NOT NULL,
                    segment_after TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _determine_segment(self, avg_complexity: float, query_count: int, query: str) -> str:
        """평균 복잡도, 질문 수, 현재 질문을 기반으로 세그먼트를 결정한다."""
        # 초보 지표 확인
        query_lower = query.lower()
        has_beginner_words = any(ind in query_lower for ind in BEGINNER_INDICATORS)

        # 현재 질문의 복잡도 점수
        current_score = self.scorer.score_query(query)

        # 종합 판단
        if avg_complexity >= 0.5 and query_count >= 3:
            return "expert"
        elif avg_complexity >= 0.5 and current_score >= 0.5:
            return "expert"
        elif avg_complexity >= 0.3 or (current_score >= 0.3 and not has_beginner_words):
            return "intermediate"
        elif has_beginner_words or avg_complexity < 0.2:
            return "beginner"
        else:
            return "intermediate"

    def classify_user(self, session_id: str, query: str) -> str:
        """사용자 질문을 분석하여 세그먼트를 분류/업데이트한다.

        Returns:
            "beginner", "intermediate", or "expert"
        """
        if not session_id or not query:
            return "beginner"

        complexity = self.scorer.score_query(query)
        now = datetime.utcnow().isoformat()

        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM user_segments WHERE session_id = ?",
                    (session_id,),
                ).fetchone()

                if row is None:
                    # 신규 사용자
                    segment_before = "beginner"
                    query_count = 1
                    total_complexity = complexity
                    avg_complexity = complexity
                else:
                    segment_before = row["segment"]
                    query_count = row["query_count"] + 1
                    total_complexity = row["total_complexity"] + complexity
                    avg_complexity = total_complexity / query_count

                segment = self._determine_segment(avg_complexity, query_count, query)

                if row is None:
                    conn.execute(
                        """INSERT INTO user_segments
                           (session_id, segment, query_count, total_complexity, avg_complexity, last_query, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (session_id, segment, query_count, total_complexity, avg_complexity, query, now, now),
                    )
                else:
                    conn.execute(
                        """UPDATE user_segments
                           SET segment = ?, query_count = ?, total_complexity = ?,
                               avg_complexity = ?, last_query = ?, updated_at = ?
                           WHERE session_id = ?""",
                        (segment, query_count, total_complexity, avg_complexity, query, now, session_id),
                    )

                # 이력 기록
                conn.execute(
                    """INSERT INTO segment_history
                       (session_id, query, complexity_score, segment_before, segment_after, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (session_id, query, complexity, segment_before, segment, now),
                )
                conn.commit()
                return segment
            finally:
                conn.close()

    def get_segment(self, session_id: str) -> str | None:
        """세션 ID의 현재 세그먼트를 반환한다."""
        if not session_id:
            return None
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT segment FROM user_segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["segment"] if row else None
        finally:
            conn.close()

    def get_segment_info(self, session_id: str) -> dict | None:
        """세션 ID의 전체 세그먼트 정보를 반환한다."""
        if not session_id:
            return None
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM user_segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "session_id": row["session_id"],
                "segment": row["segment"],
                "query_count": row["query_count"],
                "avg_complexity": row["avg_complexity"],
                "last_query": row["last_query"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def adjust_response_depth(self, answer: str, segment: str) -> str:
        """세그먼트에 따라 답변 깊이를 조절한다.

        - beginner: 쉬운 설명 + 안내 메시지 추가
        - intermediate: 원본 그대로
        - expert: 간결 요약 + 법률 인용 강조
        """
        if not answer:
            return answer

        if segment == "beginner":
            # 어려운 용어에 간단한 설명 추가
            adjusted = answer
            # 괄호 안 법조문이 있으면 쉬운 설명 추가
            adjusted = re.sub(
                r"(관세법\s*제\d+조[의\d]*)",
                r"\1(관련 법률 규정)",
                adjusted,
            )
            if BEGINNER_SUFFIX not in adjusted:
                adjusted += BEGINNER_SUFFIX
            return adjusted
        elif segment == "expert":
            # 이미 간결하면 그대로, 아니면 요약 형태 제공
            if answer.startswith(EXPERT_PREFIX):
                return answer
            # 법률 인용 부분 강조
            adjusted = re.sub(
                r"(관세법\s*제\d+조[의\d]*(?:\s*제\d+항)?)",
                r"【\1】",
                answer,
            )
            return adjusted
        else:
            # intermediate: 그대로 반환
            return answer

    def get_segment_stats(self) -> dict:
        """전체 세그먼트 분포 통계를 반환한다."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT segment, COUNT(*) as count FROM user_segments GROUP BY segment"
            ).fetchall()
            stats = {"beginner": 0, "intermediate": 0, "expert": 0}
            total = 0
            for row in rows:
                stats[row["segment"]] = row["count"]
                total += row["count"]
            stats["total"] = total
            return stats
        finally:
            conn.close()

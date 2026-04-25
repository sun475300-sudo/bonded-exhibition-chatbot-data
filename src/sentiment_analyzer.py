"""감정 분석 모듈.

사용자 질문의 감정을 분석하여 긍정/부정/중립을 판단하고,
답변 톤 조절 및 자동 에스컬레이션 트리거를 지원한다.
"""

import os
import re
import sqlite3
import threading
from datetime import datetime


# 긍정 감정 어휘 (~100개)
POSITIVE_WORDS = [
    "감사", "고마워", "고맙", "좋아", "좋은", "좋겠", "좋습", "훌륭", "완벽", "최고",
    "도움", "편리", "편해", "쉬워", "이해", "만족", "행복", "기뻐", "즐거", "반가",
    "친절", "정확", "빠르", "신속", "깔끔", "성공", "해결", "알겠", "확인", "감동",
    "대단", "멋져", "멋진", "아름", "뛰어나", "우수", "탁월", "유익", "효과", "효율",
    "추천", "칭찬", "응원", "사랑", "존경", "신뢰", "믿음", "안심", "편안", "든든",
    "상세", "세심", "배려", "고마운", "잘됐", "성과", "발전", "개선", "향상", "증가",
    "유용", "적절", "합리", "공정", "투명", "원활", "순조", "무사", "안전", "보호",
    "혜택", "이득", "수월", "간편", "명확", "정확히", "올바", "적합", "충분", "넉넉",
    "기대", "희망", "바람직", "긍정", "환영", "축하", "감격", "뿌듯", "자랑", "보람",
    "다행", "고맙습니다", "감사합니다", "잘했", "괜찮", "나아", "수고", "열심", "노력", "성심",
]

# 부정 감정 어휘 (~100개)
NEGATIVE_WORDS = [
    "불만", "불편", "어려워", "어려운", "답답", "화나", "화가", "짜증", "실망", "불쾌",
    "문제", "오류", "에러", "고장", "안돼", "안되", "못해", "없어", "부족", "나빠",
    "나쁜", "최악", "심각", "불합리", "불공정", "부당", "위반", "처벌", "벌금", "피해",
    "손해", "손실", "곤란", "힘들", "지치", "귀찮", "복잡", "혼란", "모르겠", "이해안",
    "불안", "걱정", "우려", "위험", "급해", "급한", "늦어", "지연", "지체", "느려",
    "비싸", "과다", "초과", "거부", "거절", "취소", "반려", "기각", "삭제", "폐지",
    "무시", "무례", "불성실", "불친절", "실수", "착오", "잘못", "미흡", "형편없", "엉망",
    "황당", "어이없", "어처구니", "한심", "못마땅", "원망", "항의", "고소", "고발", "신고",
    "불법", "위법", "부정", "비리", "횡령", "사기", "속았", "거짓", "허위", "민원",
    "컴플레인", "클레임", "항의", "분노", "폭발", "참을수없", "도저히", "절대", "억울", "서러",
]

# 부정어 (negation)
NEGATION_WORDS = ["안", "않", "못", "없", "아니", "아닌", "안돼", "못해", "없는", "없어"]

# 강조어 (intensifiers)
INTENSIFIERS = {
    "매우": 1.5,
    "너무": 1.5,
    "정말": 1.4,
    "진짜": 1.4,
    "아주": 1.3,
    "굉장히": 1.5,
    "엄청": 1.5,
    "상당히": 1.3,
    "대단히": 1.4,
    "몹시": 1.5,
    "무척": 1.4,
    "완전": 1.5,
    "극도로": 1.6,
    "극히": 1.5,
    "참": 1.3,
}


class SentimentAnalyzer:
    """사용자 질문의 감정을 분석하는 클래스."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "sentiment.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """SQLite 데이터베이스를 초기화한다."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sentiment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    query TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    keywords TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sentiment_session
                ON sentiment_history(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sentiment_created
                ON sentiment_history(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _tokenize(self, text: str) -> list[str]:
        """간단한 토큰화: 공백 기준 분리 + 부분 매칭을 위한 처리."""
        text = text.strip()
        # 공백 기준 토큰 분리
        tokens = re.split(r'\s+', text)
        return [t for t in tokens if t]

    def analyze(self, text: str) -> dict:
        """텍스트의 감정을 분석한다.

        Returns:
            {"sentiment": "positive"|"negative"|"neutral",
             "score": float(-1 to 1),
             "confidence": float(0-1),
             "keywords": list}
        """
        if not text or not text.strip():
            return {
                "sentiment": "neutral",
                "score": 0.0,
                "confidence": 0.5,
                "keywords": [],
            }

        tokens = self._tokenize(text)
        text_lower = text.strip()

        positive_hits = []
        negative_hits = []

        # 강조어 배율 계산
        intensifier_multiplier = 1.0
        for word, mult in INTENSIFIERS.items():
            if word in text_lower:
                intensifier_multiplier = max(intensifier_multiplier, mult)

        # 부정어 감지: 토큰 기반 (위치 기록)
        negation_positions = set()
        for i, token in enumerate(tokens):
            for neg in NEGATION_WORDS:
                if token == neg or token.startswith(neg):
                    negation_positions.add(i)
                    break

        # 각 토큰의 감정 유형 파악 (positive/negative/negation/none)
        token_roles: list[str] = []  # "pos", "neg", "negator", "none"
        token_words: list[str] = []  # matched keyword
        token_scan_seen: set[str] = set()  # 토큰 스캔에서 발견된 모든 후보 단어
        for i, token in enumerate(tokens):
            role = "none"
            matched = ""

            # 부정어(negator)인지 먼저 확인
            is_negator = i in negation_positions

            # 긍정어와 부정어를 모두 찾고, 더 긴(더 구체적인) 매치를 선택
            best_pos = ""
            for pw in POSITIVE_WORDS:
                if pw in token:
                    token_scan_seen.add(pw)
                    if len(pw) > len(best_pos):
                        best_pos = pw

            best_neg = ""
            for nw in NEGATIVE_WORDS:
                if nw in token:
                    token_scan_seen.add(nw)
                    if len(nw) > len(best_neg):
                        best_neg = nw

            # 둘 다 매칭되면: 토큰 내 매칭 위치를 비교하여 겹치면 앞쪽(부정 접두사) 우선
            if best_pos and best_neg:
                pos_start = token.find(best_pos)
                neg_start = token.find(best_neg)
                pos_end = pos_start + len(best_pos)
                neg_end = neg_start + len(best_neg)
                # 매칭 범위가 겹치면 → 더 앞에서 시작하는 쪽 우선 (불편 vs 편해 → 불편 우선)
                overlaps = pos_start < neg_end and neg_start < pos_end
                if overlaps:
                    if neg_start <= pos_start:
                        role = "neg"
                        matched = best_neg
                    else:
                        role = "pos"
                        matched = best_pos
                elif len(best_neg) >= len(best_pos):
                    role = "neg"
                    matched = best_neg
                else:
                    role = "pos"
                    matched = best_pos
            elif best_pos:
                role = "pos"
                matched = best_pos
            elif best_neg:
                if is_negator:
                    role = "negator"
                else:
                    role = "neg"
                    matched = best_neg
            elif is_negator:
                role = "negator"

            # 긍정어이면서 동시에 부정어 위치인 경우
            if role == "pos" and is_negator:
                role = "negator"
                matched = ""

            token_roles.append(role)
            token_words.append(matched)

        # 부정어에 의한 감정 반전 처리
        # 한국어 부정 패턴: (1) 앞에 오는 부정어 "안 좋아" (2) 뒤에 오는 부정어 "불만 없어"
        processed = set()
        consumed_negator_indices = set()
        for i, role in enumerate(token_roles):
            if role in ("pos", "neg") and i not in processed:
                has_negation = False
                # (1) 바로 앞 토큰이 부정어인지 확인
                if i > 0 and token_roles[i - 1] == "negator" and (i - 1) not in consumed_negator_indices:
                    has_negation = True
                    consumed_negator_indices.add(i - 1)
                    processed.add(i - 1)
                # (2) 바로 뒤 토큰이 부정어인지 확인
                elif i + 1 < len(token_roles) and token_roles[i + 1] == "negator" and (i + 1) not in consumed_negator_indices:
                    has_negation = True
                    consumed_negator_indices.add(i + 1)
                    processed.add(i + 1)

                processed.add(i)
                if role == "pos":
                    if has_negation:
                        negative_hits.append(token_words[i])
                    else:
                        positive_hits.append(token_words[i])
                else:  # neg
                    if has_negation:
                        positive_hits.append(token_words[i])
                    else:
                        negative_hits.append(token_words[i])

        # 소비된 부정어 토큰에 포함된 단어를 스킵 목록에 추가
        consumed_negator_substrings = set()
        for idx in consumed_negator_indices:
            tok = tokens[idx]
            for neg in NEGATION_WORDS:
                if neg in tok:
                    consumed_negator_substrings.add(neg)
            # 토큰 자체도 추가
            consumed_negator_substrings.add(tok)

        # 텍스트 전체에서 부분 문자열 매칭 (토큰 경계를 넘는 경우)
        all_hits = set(positive_hits + negative_hits)

        def _is_embedded_in_opposite(word, word_list, text_str):
            """단어가 반대 감정 단어의 부분 문자열인지 확인."""
            for other in word_list:
                if word != other and word in other and other in text_str:
                    return True
            return False

        for pw in POSITIVE_WORDS:
            if pw in text_lower and pw not in all_hits:
                # 토큰 스캔에서 이미 처리된 단어는 스킵
                if pw in token_scan_seen:
                    continue
                # 이 긍정어가 부정어의 부분 문자열이면 스킵 (예: 편해 ⊂ 불편해)
                if _is_embedded_in_opposite(pw, NEGATIVE_WORDS, text_lower):
                    continue
                # 부정어 + 긍정어 패턴 확인 (앞 또는 뒤)
                negated = False
                for neg in NEGATION_WORDS:
                    if re.search(rf'{re.escape(neg)}\s*{re.escape(pw)}', text_lower):
                        negated = True
                        break
                    if re.search(rf'{re.escape(pw)}\s*{re.escape(neg)}', text_lower):
                        negated = True
                        break
                if negated:
                    negative_hits.append(pw)
                else:
                    positive_hits.append(pw)
                all_hits.add(pw)

        for nw in NEGATIVE_WORDS:
            if nw in text_lower and nw not in all_hits:
                # 토큰 스캔에서 이미 처리된 단어는 스킵
                if nw in token_scan_seen:
                    continue
                # 소비된 부정어의 부분 문자열이면 스킵
                if nw in consumed_negator_substrings:
                    continue
                # 이 부정어가 긍정어의 부분 문자열이면 스킵
                if _is_embedded_in_opposite(nw, POSITIVE_WORDS, text_lower):
                    continue
                negated = False
                for neg in NEGATION_WORDS:
                    if re.search(rf'{re.escape(neg)}\s*{re.escape(nw)}', text_lower):
                        negated = True
                        break
                    if re.search(rf'{re.escape(nw)}\s*{re.escape(neg)}', text_lower):
                        negated = True
                        break
                if negated:
                    positive_hits.append(nw)
                else:
                    negative_hits.append(nw)
                all_hits.add(nw)

        # 점수 계산
        pos_count = len(positive_hits)
        neg_count = len(negative_hits)
        total = pos_count + neg_count

        if total == 0:
            return {
                "sentiment": "neutral",
                "score": 0.0,
                "confidence": 0.5,
                "keywords": [],
            }

        # 기본 점수: -1 ~ 1 사이
        raw_score = (pos_count - neg_count) / total
        # 강조어 적용
        raw_score *= intensifier_multiplier
        # 범위 제한
        score = max(-1.0, min(1.0, raw_score))

        # 신뢰도: 키워드 수가 많을수록 높음
        confidence = min(1.0, 0.4 + total * 0.15)

        # 감정 판별
        if score > 0.1:
            sentiment = "positive"
        elif score < -0.1:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        keywords = list(set(positive_hits + negative_hits))

        return {
            "sentiment": sentiment,
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "keywords": keywords,
        }

    def analyze_and_store(self, text: str, session_id: str | None = None) -> dict:
        """감정을 분석하고 DB에 저장한다."""
        result = self.analyze(text)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO sentiment_history
                       (session_id, query, sentiment, score, confidence, keywords, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        text,
                        result["sentiment"],
                        result["score"],
                        result["confidence"],
                        ",".join(result["keywords"]),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return result

    def adjust_response_tone(self, answer: str, sentiment: dict) -> str:
        """감정 분석 결과에 따라 답변 톤을 조절한다.

        negative → 공감 접두사, positive → 감사 접두사
        """
        if not answer:
            return answer

        sent = sentiment.get("sentiment", "neutral")
        score = sentiment.get("score", 0.0)

        if sent == "negative":
            if score < -0.6:
                prefix = "불편을 드려 정말 죄송합니다. 최선을 다해 도와드리겠습니다.\n\n"
            else:
                prefix = "불편을 드려 죄송합니다.\n\n"
            return prefix + answer
        elif sent == "positive":
            prefix = "관심을 가져주셔서 감사합니다!\n\n"
            return prefix + answer
        return answer

    def should_escalate(self, sentiment_result: dict) -> bool:
        """매우 부정적 감정(score < -0.6)이면 에스컬레이션을 권고한다."""
        return sentiment_result.get("score", 0.0) < -0.6

    def get_sentiment_stats(self, session_id: str | None = None) -> dict:
        """감정 분석 통계를 반환한다."""
        conn = self._get_conn()
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT sentiment, COUNT(*) as cnt FROM sentiment_history WHERE session_id = ? GROUP BY sentiment",
                    (session_id,),
                ).fetchall()
                total_row = conn.execute(
                    "SELECT COUNT(*) FROM sentiment_history WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                rows = conn.execute(
                    "SELECT sentiment, COUNT(*) as cnt FROM sentiment_history GROUP BY sentiment"
                ).fetchall()
                total_row = conn.execute(
                    "SELECT COUNT(*) FROM sentiment_history"
                ).fetchone()

            total = total_row[0] if total_row else 0
            distribution = {"positive": 0, "negative": 0, "neutral": 0}
            for row in rows:
                distribution[row["sentiment"]] = row["cnt"]

            # 평균 점수
            if session_id:
                avg_row = conn.execute(
                    "SELECT AVG(score) FROM sentiment_history WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                avg_row = conn.execute(
                    "SELECT AVG(score) FROM sentiment_history"
                ).fetchone()

            avg_score = round(avg_row[0], 4) if avg_row and avg_row[0] is not None else 0.0

            return {
                "total": total,
                "distribution": distribution,
                "average_score": avg_score,
            }
        finally:
            conn.close()

    def get_sentiment_history(self, session_id: str | None = None, limit: int = 50) -> list[dict]:
        """감정 분석 이력을 반환한다."""
        conn = self._get_conn()
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM sentiment_history WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sentiment_history ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def close(self):
        """리소스 정리 (호환성을 위해 유지)."""
        pass

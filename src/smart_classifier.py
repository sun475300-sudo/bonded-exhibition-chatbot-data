"""대화 맥락 기반 지능형 분류기 모듈.

세션 히스토리를 참고하여 후속 질문의 분류 정확도를 향상시킨다.
기존 classify_query 하위 호환을 유지한다.
"""

import re

from src.classifier import classify_query, CATEGORY_PRIORITY

# 후속 질문 패턴: 이전 카테고리를 유지할 가능성이 높은 접속사/표현
FOLLOW_UP_PATTERNS = [
    r"^그러면",
    r"^그건",
    r"^그럼",
    r"^또\s",
    r"^추가로",
    r"^그리고",
    r"^그래서",
    r"^그렇다면",
    r"^만약",
    r"^혹시",
    r"^아\s",
    r"^그\s",
    r"^근데",
    r"^그런데",
    r"^참고로",
    r"^덧붙여",
    r"^아까",
    r"^방금",
    r"^위에서",
    r"^말씀하신",
]

# 세션 컨텍스트 가중치: 직전 카테고리에 부여할 보정 점수
CONTEXT_WEIGHT = 1.5
# 후속 질문 감지 시 추가 가중치
FOLLOW_UP_BONUS = 1.0


class SmartClassifier:
    """대화 맥락을 활용한 지능형 질문 분류기."""

    def __init__(self):
        pass

    def classify_with_context(self, query: str, session=None) -> list[str]:
        """세션 히스토리를 참고하여 질문을 분류한다.

        Args:
            query: 사용자 질문 문자열.
            session: Session 객체 (선택). 없으면 기존 classify_query와 동일하게 동작.

        Returns:
            매칭된 카테고리 코드 리스트.
        """
        # 기본 분류 수행
        base_categories = classify_query(query)

        # 세션이 없거나 히스토리가 없으면 기본 분류 그대로 반환
        if session is None or not session.history:
            return base_categories

        # 이전 대화에서 카테고리 히스토리 추출
        prev_categories = self._extract_category_history(session)
        if not prev_categories:
            return base_categories

        # 후속 질문 패턴 감지
        is_follow_up = self._detect_follow_up(query)

        # 컨텍스트 기반 보정
        return self._adjust_with_context(
            base_categories, prev_categories, is_follow_up
        )

    def _extract_category_history(self, session) -> list[str]:
        """세션 히스토리에서 이전 질문들의 카테고리를 추출한다.

        Returns:
            최근 질문들의 카테고리 리스트 (최신 순).
        """
        categories = []
        for turn in reversed(session.history):
            q = turn.get("query", "")
            if q:
                cats = classify_query(q)
                if cats:
                    categories.append(cats[0])
        return categories

    def _detect_follow_up(self, query: str) -> bool:
        """후속 질문 패턴을 감지한다.

        Args:
            query: 사용자 질문 문자열.

        Returns:
            후속 질문 여부.
        """
        query_stripped = query.strip()
        for pattern in FOLLOW_UP_PATTERNS:
            if re.search(pattern, query_stripped):
                return True
        return False

    def _adjust_with_context(
        self,
        base_categories: list[str],
        prev_categories: list[str],
        is_follow_up: bool,
    ) -> list[str]:
        """세션 컨텍스트를 기반으로 분류 결과를 보정한다.

        기본 분류가 GENERAL이고 이전 카테고리가 구체적이면,
        후속 질문 패턴과 이전 카테고리를 고려하여 보정한다.

        Args:
            base_categories: 기본 분류 결과.
            prev_categories: 이전 대화의 카테고리 히스토리.
            is_follow_up: 후속 질문 패턴 감지 여부.

        Returns:
            보정된 카테고리 리스트.
        """
        if not prev_categories:
            return base_categories

        last_category = prev_categories[0]

        # 기본 분류가 GENERAL이고 후속 질문이면 이전 카테고리 우선
        if base_categories == ["GENERAL"] and is_follow_up:
            return [last_category]

        # 기본 분류가 GENERAL이고 이전 카테고리가 구체적이면 이전 카테고리도 후보에 추가
        if base_categories == ["GENERAL"] and last_category != "GENERAL":
            return [last_category, "GENERAL"]

        # 기본 분류 결과에 이전 카테고리가 포함되어 있으면 우선순위 올림
        if last_category in base_categories and base_categories[0] != last_category:
            adjusted = [last_category] + [
                c for c in base_categories if c != last_category
            ]
            return adjusted

        # 후속 질문이면서 동점 상황에서 이전 카테고리 우선
        if is_follow_up and len(base_categories) > 1:
            if last_category in base_categories:
                adjusted = [last_category] + [
                    c for c in base_categories if c != last_category
                ]
                return adjusted

        return base_categories

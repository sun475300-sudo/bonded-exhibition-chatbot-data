"""
보세전시장 챗봇 명확화 엔진 (Clarification Engine)

사용자의 질문이 모호할 때 명확화 질문을 생성하는 모듈.
"""

CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "GENERAL": "제도 일반 (보세전시장 정의, 자격 등)",
    "LICENSE": "특허/운영 (설치, 갱신, 변경 등)",
    "IMPORT_EXPORT": "반입/반출 (물품 반입, 재반출 등)",
    "EXHIBITION": "전시/사용 (전시회, 체험, 시연 등)",
    "SALES": "판매/직매 (현장판매, 인도 등)",
    "SAMPLE": "견본품 (샘플 배포, 관세 등)",
    "FOOD_TASTING": "시식용식품 (시식 요건, 검역 등)",
    "DOCUMENTS": "서류/신고 (구비서류, 신청서 등)",
    "PENALTIES": "벌칙/제재 (과태료, 처벌 등)",
    "CONTACT": "담당기관 (문의처, 연락처 등)",
}

GENERIC_WORDS: set[str] = {
    "어떻게",
    "알려줘",
    "궁금해",
    "뭐",
    "어디",
    "언제",
    "뭔가요",
    "알고",
    "싶어",
    "싶어요",
    "해줘",
    "좀",
    "요",
    "거",
    "것",
    "대해",
    "대해서",
    "에",
    "는",
    "은",
    "이",
    "가",
    "을",
    "를",
    "하고",
    "해",
    "하나요",
    "인가요",
    "할",
    "수",
    "있나요",
    "없나요",
    "있어",
    "없어",
    "무엇",
    "어떤",
    "질문",
    "정보",
}


class ClarificationEngine:
    """사용자 질문이 모호할 때 명확화 질문을 생성하는 엔진."""

    def needs_clarification(
        self,
        query: str,
        categories: list[str],
        faq_match: dict | None = None,
    ) -> bool:
        """질문이 모호하여 명확화가 필요한지 판단한다.

        Args:
            query: 사용자 질문 원문.
            categories: 분류된 카테고리 목록.
            faq_match: FAQ 매칭 결과. None이면 매칭 없음.

        Returns:
            명확화가 필요하면 True.
        """
        stripped = query.replace(" ", "")

        # 공백 제외 5자 미만이면 너무 짧아 의도 파악 불가
        if len(stripped) < 5:
            return True

        # 여러 카테고리에 동일 점수로 걸리고 FAQ 매칭도 없는 경우
        if len(categories) >= 2 and faq_match is None:
            return True

        # 도메인 특화 단어 없이 일반 단어로만 구성된 경우
        if self._is_only_generic(query):
            return True

        return False

    def generate_clarification(self, query: str, categories: list[str]) -> str:
        """명확화 질문을 생성한다.

        Args:
            query: 사용자 질문 원문.
            categories: 분류된 카테고리 목록.

        Returns:
            사용자에게 보여줄 명확화 질문 문자열.
        """
        header = "질문하신 내용이 여러 분야에 해당될 수 있습니다. 다음 중 어떤 내용에 대해 알고 싶으신가요?"
        lines = [header]
        for idx, cat in enumerate(categories, start=1):
            display = CATEGORY_DISPLAY_NAMES.get(cat, cat)
            lines.append(f"{idx}. {display}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_only_generic(query: str) -> bool:
        """질문이 일반 단어로만 이루어져 있는지 확인한다."""
        tokens = query.split()
        if not tokens:
            return True
        for token in tokens:
            if token not in GENERIC_WORDS:
                return False
        return True

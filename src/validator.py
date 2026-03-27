"""입력 검증 및 확인 질문 관리 모듈.

민원 정확도를 높이기 위해 사용자에게 추가 확인이 필요한 항목을 관리한다.
"""

CONFIRMATION_QUESTIONS = {
    "foreign_goods": {
        "question": "해당 물품은 외국물품인가요?",
        "why": "보세전시장 제도는 외국물품에 적용되는 제도입니다."
    },
    "purpose": {
        "question": "행사 목적이 전시인가요, 판매인가요, 시식·증정인가요?",
        "why": "전시용, 판매용, 직매, 견본품 반출, 시식용에 따라 적용 조문과 절차가 다릅니다."
    },
    "venue_licensed": {
        "question": "행사 장소가 보세전시장 특허를 받은 곳인가요?",
        "why": "보세전시장 특허가 없는 장소에서는 보세전시장 제도가 적용되지 않습니다."
    },
    "post_event_plan": {
        "question": "물품을 행사 후 재반출(해외 반송)할 예정인가요, 국내 판매할 예정인가요?",
        "why": "재반출과 국내 판매 시 통관 절차가 다릅니다."
    },
    "other_requirements": {
        "question": "해당 물품이 식품·의료기기·검역대상 등 타법 요건에 해당하나요?",
        "why": "관세법 외에 식약처, 검역 등 관계기관 요건도 충족해야 합니다."
    },
    "customs_consulted": {
        "question": "이미 관할 세관과 사전 협의를 하셨나요?",
        "why": "사전 협의 여부에 따라 안내 범위가 달라집니다."
    }
}


def get_needed_confirmations(category: str, query: str) -> list[dict]:
    """카테고리와 질문 내용에 따라 필요한 확인 질문 목록을 반환한다."""
    needed = []
    seen_keys = set()
    query_lower = query.lower() if query else ""

    def _add(key: str):
        if key not in seen_keys and key in CONFIRMATION_QUESTIONS:
            needed.append(CONFIRMATION_QUESTIONS[key])
            seen_keys.add(key)

    # 항상 물어보는 질문
    _add("foreign_goods")

    # 카테고리별 추가 질문
    category_questions = {
        "SALES": ["purpose", "post_event_plan"],
        "SAMPLE": ["purpose", "post_event_plan"],
        "FOOD_TASTING": ["purpose", "other_requirements"],
        "IMPORT_EXPORT": ["post_event_plan", "venue_licensed"],
        "LICENSE": ["venue_licensed"],
        "EXHIBITION": ["purpose", "venue_licensed"],
        "PENALTIES": ["customs_consulted"],
        "DOCUMENTS": ["venue_licensed"],
    }

    for key in category_questions.get(category, []):
        _add(key)

    # 키워드 기반 추가
    food_keywords = ["식품", "시식", "음식", "검역", "의료기기", "식약처"]
    if any(kw in query_lower for kw in food_keywords):
        _add("other_requirements")

    return needed

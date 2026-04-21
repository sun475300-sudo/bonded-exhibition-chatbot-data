"""질문 의도 분류기 모듈.

사용자 질문을 10개 카테고리 중 하나 이상으로 분류한다.
또한 새 30-intent 시스템도 지원한다.
"""

import logging
from typing import Optional
from src.utils import normalize_query, load_json

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "GENERAL": [
        "보세전시장", "보세구역", "제도", "정의", "개념", "뜻", "무엇",
        "어떤 곳", "어떤곳", "보세 전시장", "보세창고", "차이", "다른 점",
        "내국물품", "국산", "비교", "구분", "이용", "누가", "자격"
    ],
    "LICENSE": [
        "특허", "운영", "설치", "특허기간", "특허신청",
        "특허장소", "운영인", "설치특허", "갱신", "연장", "변경",
        "특허 연장", "기간 연장"
    ],
    "IMPORT_EXPORT": [
        "반입", "반출", "반출입", "물품검사",
        "들여오", "내보내", "가져오", "꺼내", "재반출", "반송",
        "돌려보내", "잔류", "남은 물품", "미반출",
        "해외로", "세관 검사", "반입 검사"
    ],
    "EXHIBITION": [
        "전시", "장치", "진열", "디스플레이", "전시회",
        "박람회", "전람회", "시연", "데모", "시범", "체험",
        "사용 범위", "전시 목적", "전시 가능"
    ],
    "SALES": [
        "판매", "직매", "현장판매", "현장 판매", "인도", "구매",
        "매매", "사다", "팔다", "팔 수", "물건 팔", "살 수",
        "현장에서 판매", "바로 판매",
        "계약", "주문", "인도 시점", "통관 후"
    ],
    "SAMPLE": [
        "견본품", "샘플", "견본", "홍보용", "시료", "무료 배포",
        "무료배포", "나눠주", "견본품 관세", "견본품 세금", "견본품 과세"
    ],
    "FOOD_TASTING": [
        "시식", "식품", "음식", "요건확인", "세관장확인",
        "식약처", "검역", "위생", "시식용", "잔량", "폐기",
        "남은 식품"
    ],
    "DOCUMENTS": [
        "서류", "신고서", "신청서", "구비서류", "제출", "양식",
        "서식", "첨부", "문서", "반출입신고서", "허가 신청"
    ],
    "PENALTIES": [
        "벌칙", "제재", "과태료", "벌금", "처벌", "위반", "처분",
        "불이익", "과징금", "무허가", "밀수", "특허 취소",
        "업무 정지", "의무 위반", "허가 없이", "면허 없이",
        "어떻게 되나", "처벌받", "걸리면"
    ],
    "CONTACT": [
        "문의", "전화", "연락처", "담당", "어디에", "누구에게",
        "상담", "고객지원", "기술지원", "보세산업과", "어디",
        "담당 부서", "소관"
    ]
}

# 도메인 우선순위: 동점 시 더 구체적인 카테고리를 선호
CATEGORY_PRIORITY = {
    "PENALTIES": 1,
    "FOOD_TASTING": 2,
    "SAMPLE": 3,
    "SALES": 4,
    "DOCUMENTS": 5,
    "CONTACT": 6,
    "IMPORT_EXPORT": 7,
    "EXHIBITION": 8,
    "LICENSE": 9,
    "GENERAL": 10,
}


def classify_query(query: str) -> list[str]:
    """사용자 질문을 카테고리로 분류한다.

    Args:
        query: 사용자 질문 문자열

    Returns:
        매칭된 카테고리 코드 리스트 (최소 1개). 매칭 없으면 ["GENERAL"].
    """
    if not query:
        return ["GENERAL"]

    query_lower = normalize_query(query)
    scores = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in query_lower:
                score += 1
        if score > 0:
            scores[category] = score

    if not scores:
        return ["GENERAL"]

    max_score = max(scores.values())
    results = [cat for cat, sc in scores.items() if sc == max_score]

    # 도메인 우선순위로 정렬 (구체적 카테고리 우선)
    results.sort(key=lambda c: CATEGORY_PRIORITY.get(c, 99))

    return results


def get_primary_category(query: str) -> str:
    """사용자 질문의 주요 카테고리 1개를 반환한다."""
    categories = classify_query(query)
    return categories[0]


# Mapping from new 30-intent system domain codes to old 10-category system
INTENT_TO_CATEGORY_MAP = {
    # System & Qualification domain -> GENERAL + LICENSE
    "sysqual": "GENERAL",
    "license": "LICENSE",

    # Import/Export domain
    "import_export": "IMPORT_EXPORT",

    # Exhibition domain
    "exhibition": "EXHIBITION",

    # Sales domain
    "sales": "SALES",

    # Product domains
    "sample": "SAMPLE",
    "food": "FOOD_TASTING",

    # Administrative
    "doc": "DOCUMENTS",
    "admin": "DOCUMENTS",

    # Penalties & Compliance
    "penalty": "PENALTIES",
    "compliance": "PENALTIES",

    # Support
    "support": "CONTACT",
}


class IntentClassifier:
    """새 30-intent 시스템을 지원하는 의도 분류기.

    intents.json에서 의도를 로드하고, 키워드 + 퍼지 매칭을 통해
    사용자 질문을 분류한다.
    """

    def __init__(self):
        """IntentClassifier를 초기화한다."""
        self.intents = {}
        self.intent_keywords = {}
        self._load_intents()

    def _load_intents(self):
        """intents.json에서 의도 정의를 로드한다.

        두 가지 포맷을 지원한다:
        - 신규: {"intents": [ { "id": ..., "example_queries": [...] } ]}
        - 간단: [ { "intent_id": ..., "intent_name_ko": ..., "description": ... } ]
        """
        try:
            data = load_json("data/intents.json")
            if isinstance(data, list):
                intent_list = data
            elif isinstance(data, dict):
                intent_list = data.get("intents", [])
            else:
                intent_list = []

            for intent in intent_list:
                intent_id = intent.get("id") or intent.get("intent_id")
                if not intent_id:
                    continue
                self.intents[intent_id] = intent

                # 예시 쿼리 또는 이름/설명으로부터 키워드 추출
                example_queries = intent.get("example_queries", [])
                if not example_queries:
                    fallback_text = " ".join(
                        s for s in (
                            intent.get("intent_name_ko"),
                            intent.get("description"),
                        ) if s
                    )
                    if fallback_text:
                        example_queries = [fallback_text]

                keywords = set()
                for query in example_queries:
                    tokens = normalize_query(query).split()
                    keywords.update(tokens)

                self.intent_keywords[intent_id] = keywords

            logger.info(f"Loaded {len(self.intents)} intents from data/intents.json")
        except Exception as e:
            logger.warning(f"Failed to load intents.json: {e}. Graceful degradation enabled.")
            self.intents = {}
            self.intent_keywords = {}

    def classify_intent(self, query: str) -> tuple[str, float]:
        """사용자 질문을 의도로 분류한다.

        Args:
            query: 사용자 질문 문자열

        Returns:
            (intent_id, confidence_score) 튜플.
            의도를 찾지 못하면 ("unknown", 0.0) 반환.
        """
        if not query or not self.intents:
            return ("unknown", 0.0)

        query_lower = normalize_query(query)
        query_tokens = set(query_lower.split())

        best_intent = "unknown"
        best_score = 0.0

        for intent_id, keywords in self.intent_keywords.items():
            # 키워드 매칭: 일치하는 키워드의 비율로 신뢰도 계산
            if keywords:
                matches = len(query_tokens & keywords)
                score = matches / len(keywords)

                if score > best_score:
                    best_score = score
                    best_intent = intent_id

        return (best_intent, best_score)

    def get_intent_category(self, intent_id: str) -> str:
        """의도 ID를 기존 10-category 시스템으로 매핑한다.

        Args:
            intent_id: 의도 ID (예: "sysqual_001")

        Returns:
            기존 카테고리 코드 (예: "GENERAL", "LICENSE")
        """
        if not intent_id or intent_id not in self.intents:
            return "GENERAL"

        intent = self.intents[intent_id]
        domain = intent.get("domain", "")

        # domain 문자열에서 카테고리 매핑
        if "System & Qualification" in domain or "제도" in domain or "자격" in domain:
            return "GENERAL"
        elif "License" in domain or "특허" in domain:
            return "LICENSE"
        elif "Import" in domain or "Export" in domain or "반입" in domain or "반출" in domain:
            return "IMPORT_EXPORT"
        elif "Exhibition" in domain or "전시" in domain:
            return "EXHIBITION"
        elif "Sales" in domain or "판매" in domain:
            return "SALES"
        elif "Sample" in domain or "견본" in domain:
            return "SAMPLE"
        elif "Food" in domain or "식품" in domain or "시식" in domain:
            return "FOOD_TASTING"
        elif "Document" in domain or "서류" in domain or "문서" in domain:
            return "DOCUMENTS"
        elif "Penalty" in domain or "벌칙" in domain or "제재" in domain:
            return "PENALTIES"
        elif "Support" in domain or "문의" in domain or "연락" in domain:
            return "CONTACT"

        return "GENERAL"


# 전역 IntentClassifier 인스턴스
_intent_classifier: Optional[IntentClassifier] = None


def get_intent_classifier() -> IntentClassifier:
    """전역 IntentClassifier 인스턴스를 반환한다 (싱글톤)."""
    global _intent_classifier
    if _intent_classifier is None:
        _intent_classifier = IntentClassifier()
    return _intent_classifier


def classify_intent(query: str) -> tuple[str, float]:
    """사용자 질문을 새 30-intent 시스템으로 분류한다.

    Args:
        query: 사용자 질문 문자열

    Returns:
        (intent_id, confidence_score) 튜플
    """
    classifier = get_intent_classifier()
    return classifier.classify_intent(query)

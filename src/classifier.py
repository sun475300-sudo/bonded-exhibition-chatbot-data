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
        "내국물품", "국산", "비교", "구분", "누가",
        "행사 주최", "주최하려면", "운영인 자격"
    ],
    "LICENSE": [
        "특허", "운영", "설치", "특허기간", "특허신청",
        "특허장소", "운영인", "설치특허", "갱신", "연장",
        "특허 연장", "기간 연장",
        "특허 신청", "특허 신청하려면", "특허 받으려면",
        "특허 수수료", "신청 수수료", "특허 비용",
        "특허 취소", "취소 사유"
    ],
    "IMPORT_EXPORT": [
        "반입", "반출", "반출입", "물품검사",
        "들여오", "내보내", "가져오", "꺼내", "재반출", "반송",
        "돌려보내", "잔류", "남은 물품", "미반출",
        "해외로", "세관 검사", "반입 검사",
        "보세운송", "물품 이동", "다른 보세전시장", "보세전시장 간",
        "물품이 남", "전시 종료 후"
    ],
    "EXHIBITION": [
        "전시 가능", "전시 목적", "장치", "진열", "디스플레이",
        "박람회", "전람회", "시연", "데모", "시범", "체험",
        "사용 범위", "장치된 물품", "전시물 교체", "물품 교체",
        "전시장 내", "보관 시 주의", "촬영", "홍보 활동",
        "전시 기간", "전시할 수", "전시물", "물품 제한",
        "물품에 제한", "어디까지 사용", "중간에 바꿀"
    ],
    "SALES": [
        "판매", "직매", "현장판매", "현장 판매", "구매",
        "매매", "사다", "팔다", "팔 수", "물건 팔", "살 수",
        "현장에서 판매", "바로 판매",
        "계약", "주문", "통관 후",
        "수입면허 신청", "면허 신청 절차", "결제", "수금", "정산"
    ],
    "SAMPLE": [
        "견본품", "샘플", "견본", "홍보용", "시료", "무료 배포",
        "무료배포", "나눠주", "견본품 관세", "견본품 세금", "견본품 과세",
        "견본품 수량", "견본품 반환", "샘플 반납"
    ],
    "FOOD_TASTING": [
        "시식", "식품", "음식", "요건확인", "세관장확인",
        "식약처", "검역", "위생", "시식용",
        "남은 식품", "시식 식품"
    ],
    "DOCUMENTS": [
        "서류", "신고서", "신청서", "구비서류", "제출", "양식",
        "서식", "첨부", "문서", "반출입신고서",
        "어떤 서류", "필요한 서류", "결과 보고"
    ],
    "PENALTIES": [
        "벌칙", "제재", "과태료", "벌금", "처벌", "위반", "처분",
        "불이익", "과징금", "무허가", "밀수",
        "업무 정지", "의무 위반", "의무위반", "의무위반시",
        "운영인 의무", "운영인 의무위반", "허가 없이", "면허 없이",
        "처벌받", "걸리면"
    ],
    "CONTACT": [
        "문의", "전화", "연락처", "담당", "어디에", "누구에게",
        "상담", "고객지원", "기술지원", "보세산업과",
        "담당 부서", "소관",
        "유니패스", "uni-pass",
        "관세사", "관세사 위임", "위임", "대행", "통관 대행",
        "불복", "이의신청", "심사청구", "심판청구",
        "오류 신고"
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
        """intents.json에서 의도 정의를 로드한다."""
        try:
            data = load_json("data/intents.json")
            # intents.json은 list 또는 dict({'intents': [...]}) 형식을 모두 지원
            if isinstance(data, list):
                intent_list = data
            else:
                intent_list = data.get("intents", [])

            for intent in intent_list:
                # 'intent_id' 키를 우선 사용하고, 없으면 'id' 키를 사용
                intent_id = intent.get("intent_id") or intent.get("id")
                if not intent_id:
                    continue
                self.intents[intent_id] = intent

                # 예시 쿼리로부터 키워드 추출 (example_queries 필드가 있는 경우)
                example_queries = intent.get("example_queries", [])
                keywords = set()
                for query in example_queries:
                    # 간단한 토큰화
                    tokens = normalize_query(query).split()
                    keywords.update(tokens)
                # description 필드에서도 키워드 추출
                description = intent.get("description", "")
                if description:
                    tokens = normalize_query(description).split()
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

    # intent_id 토큰 → 카테고리 매핑 (구체적인 도메인이 먼저 평가되도록 정렬).
    # 예: "tasting_food" 는 IMPORT_EXPORT 보다 FOOD_TASTING 으로 분류되어야 한다.
    _INTENT_ID_CATEGORY_RULES = (
        # (토큰 키워드, 카테고리)
        ("tasting", "FOOD_TASTING"),
        ("food", "FOOD_TASTING"),
        ("sample", "SAMPLE"),
        ("gift", "SAMPLE"),
        ("demo", "SAMPLE"),
        ("penalty", "PENALTIES"),
        ("noncompliance", "PENALTIES"),
        ("loss_or_damage", "PENALTIES"),
        ("sale", "SALES"),
        ("domestic_release", "SALES"),
        ("permit", "LICENSE"),
        ("operator", "LICENSE"),
        ("eligibility", "LICENSE"),
        ("required_documents", "DOCUMENTS"),
        ("inventory", "DOCUMENTS"),
        ("declaration", "IMPORT_EXPORT"),
        ("inspection", "IMPORT_EXPORT"),
        ("inbound", "IMPORT_EXPORT"),
        ("outbound", "IMPORT_EXPORT"),
        ("reexport", "IMPORT_EXPORT"),
        ("transfer", "IMPORT_EXPORT"),
        ("display", "EXHIBITION"),
        ("exhibition_hall", "EXHIBITION"),
        ("facility", "EXHIBITION"),
        ("definition", "GENERAL"),
        ("comprehensive", "GENERAL"),
        ("difference", "GENERAL"),
        ("legal_basis", "GENERAL"),
        ("contact", "CONTACT"),
        ("support", "CONTACT"),
    )

    # 한국어 키워드 (domain/description fallback). 더 구체적인 카테고리부터.
    _DOMAIN_CATEGORY_RULES = (
        ("시식", "FOOD_TASTING"),
        ("식품", "FOOD_TASTING"),
        ("Food", "FOOD_TASTING"),
        ("Tasting", "FOOD_TASTING"),
        ("견본", "SAMPLE"),
        ("샘플", "SAMPLE"),
        ("Sample", "SAMPLE"),
        ("벌칙", "PENALTIES"),
        ("제재", "PENALTIES"),
        ("처분", "PENALTIES"),
        ("Penalty", "PENALTIES"),
        ("판매", "SALES"),
        ("Sales", "SALES"),
        ("특허", "LICENSE"),
        ("License", "LICENSE"),
        ("Permit", "LICENSE"),
        ("서류", "DOCUMENTS"),
        ("문서", "DOCUMENTS"),
        ("Document", "DOCUMENTS"),
        ("반입", "IMPORT_EXPORT"),
        ("반출", "IMPORT_EXPORT"),
        ("Import", "IMPORT_EXPORT"),
        ("Export", "IMPORT_EXPORT"),
        ("전시", "EXHIBITION"),
        ("Exhibition", "EXHIBITION"),
        ("문의", "CONTACT"),
        ("연락", "CONTACT"),
        ("Support", "CONTACT"),
        ("제도", "GENERAL"),
        ("자격", "GENERAL"),
    )

    def get_intent_category(self, intent_id: str) -> str:
        """의도 ID를 기존 10-category 시스템으로 매핑한다.

        Args:
            intent_id: 의도 ID (예: "tasting_food", "permit_application_process")

        Returns:
            기존 카테고리 코드 (예: "GENERAL", "LICENSE")
        """
        if not intent_id or intent_id not in self.intents:
            return "GENERAL"

        intent = self.intents[intent_id]
        intent_id_lower = intent_id.lower()

        # 1) intent_id 토큰 기반 매핑 (가장 신뢰도 높음)
        for token, category in self._INTENT_ID_CATEGORY_RULES:
            if token in intent_id_lower:
                return category

        # 2) domain / description 기반 폴백
        haystack = (intent.get("domain", "") + " " + intent.get("description", "")).strip()
        if not haystack:
            return "GENERAL"
        for token, category in self._DOMAIN_CATEGORY_RULES:
            if token in haystack:
                return category

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

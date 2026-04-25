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
        "보세전시장이 무엇", "보세전시장 정의", "보세전시장 뜻이",
        "보세전시장의 개념", "보세전시장 개념", "보세전시장이란",
        "보세창고", "보세창고와", "보세전시장과 보세창고",
        "보세 전시장", "차이점", "다른가요", "다른 점",
        "내국물품도 전시", "내국물품 전시", "내국물품",
        "이용 자격", "누가 이용", "이용할 수 있", "이용 가능",
        "행사 주최", "행사를 주최", "행사 준비", "행사 개최",
        "주최하려면", "운영인 자격", "운영인 요건",
        "운영인 자격 요건"
    ],
    "LICENSE": [
        "특허기간", "특허 기간", "특허신청", "특허 신청", "특허장소",
        "특허 갱신", "특허 연장", "기간 연장", "특허 변경",
        "특허 받으려면", "특허를 받", "특허 어디",
        "특허 수수료", "특허 신청 비용", "특허 비용",
        "특허 취소 사유", "특허가 취소", "취소될 수 있",
        "설치·운영 특허", "설치 운영 특허",
        "특허 신청 수수료",
        "특허를 갱신", "특허를 연장", "특허 연장 가능"
    ],
    "IMPORT_EXPORT": [
        "반입", "반출", "반출입", "물품검사",
        "들여오", "내보내", "가져오", "꺼내", "재반출", "반송",
        "돌려보내", "잔류", "남은 물품", "물품이 남", "남으면",
        "전시 종료 후 물품", "전시 종료 후 잔류", "물품 남",
        "미반출", "해외로", "세관 검사", "반입 검사", "반입 물품",
        "보세운송", "다른 보세전시장", "보세전시장 간",
        "가액 신고", "물품 가액", "처리하나요"
    ],
    "EXHIBITION": [
        "진열", "디스플레이", "시연", "데모", "시범", "체험",
        "사용 범위", "전시 목적", "전시 가능", "장치된 물품",
        "장치된 외국물품", "전시 기간 중", "물품 교체", "전시물 교체",
        "전시물을 중간에 바꿀", "전시물 중간에", "중간에 바꿀",
        "전시물 바꿀", "전시 교체",
        "전시장 내 보관", "보관 주의사항", "전시장 보관", "보관 시",
        "전시 촬영", "촬영 홍보", "전시장에서 촬영", "전시장 촬영",
        "전시장 홍보", "홍보 활동", "전시할 수 있", "전시 물품 제한",
        "전시할 수 있는 물품", "장치된 물품 사용", "사용 어디까지",
        "어디까지 사용", "장치된 물품 어디"
    ],
    "SALES": [
        "판매", "직매", "현장판매", "현장 판매", "구매",
        "매매", "사다", "팔다", "팔 수", "물건 팔", "살 수",
        "현장에서 판매", "바로 판매", "현장 직매",
        "판매 계약", "주문 접수", "통관 전 인도", "통관 전 판매",
        "판매 대금", "결제 대금", "수금", "정산 대금", "결제",
        "수입면허 신청 절차", "판매 전 수입면허", "수입면허 신청",
        "계약만 체결", "계약만", "계약 체결", "체결하고",
        "인도는 나중에", "인도 나중에", "나중에 인도", "나중에 통관"
    ],
    "SAMPLE": [
        "견본품", "샘플", "견본", "홍보용 샘플", "시료",
        "무료 배포", "무료배포", "나눠주",
        "견본품 관세", "견본품 세금", "견본품 과세",
        "견본품 수량", "견본품 반환", "샘플 반납"
    ],
    "FOOD_TASTING": [
        "시식", "시식용", "시식 식품", "시식 행사", "시식용 식품",
        "음식", "요건확인 생략", "세관장확인",
        "식약처", "검역", "위생",
        "시식 잔량", "시식 폐기", "남은 시식 식품", "남은 시식",
        "시식용 한글", "한글 표시 라벨", "한글 라벨",
        "행사 후 처리", "시식 처리", "시식용 잔량", "시식 수량",
        "시식 식품 행사", "시식용 식품 수량"
    ],
    "DOCUMENTS": [
        "서류", "신고서", "신청서", "구비서류", "제출", "양식",
        "서식", "첨부서류", "문서", "어떤 서류", "서류가 필요",
        "필요한 서류", "필요 서류", "송장", "인보이스",
        "반출입신고서 양식", "특허 신청 서류", "특허 신청 시 서류",
        "특허 신청 구비서류", "수입면허 서류", "수입면허 신청 서류",
        "수입신고서", "수입신고서 외",
        "운영 종료 보고", "결과 보고", "보고서"
    ],
    "PENALTIES": [
        "벌칙", "제재", "과태료", "벌금", "처벌", "처분",
        "불이익", "과징금", "무허가", "밀수", "밀수출입",
        "허가 없이 반출", "허가 없이 물품을 반출", "허가 없이 물품",
        "면허 없이 사용", "면허 없이 판매", "면허 없이 판매용",
        "운영인 의무 위반", "운영인 의무위반",
        "업무 정지", "처벌받", "걸리면", "위반시", "위반하면", "위반 시"
    ],
    "CONTACT": [
        "문의", "전화", "연락처", "어디에 연락", "어디로 연락",
        "어디로 하나요", "누구에게",
        "상담", "고객지원", "기술지원",
        "uni-pass", "유니패스", "전산 오류", "시스템 오류",
        "오류 신고", "담당 부서", "보세산업과", "소관 부서",
        "관세 불복", "이의신청", "심사청구", "심판청구",
        "관세사 대행", "관세사 위임", "관세사에게", "관세사",
        "전화번호"
    ]
}

# 도메인 우선순위: 동점 시 더 구체적인 카테고리를 선호
# CONTACT/PENALTIES 같은 기능 카테고리가 도메인(LICENSE/SALES) 보다 우선되어야
# 한다. "특허 취소 사유" → LICENSE, "관세 불복" → CONTACT 처럼 명확한 의도가
# 있는 질의에서 분류 정확도를 높이기 위함.
CATEGORY_PRIORITY = {
    "CONTACT": 1,
    "PENALTIES": 2,
    "FOOD_TASTING": 3,
    "SAMPLE": 4,
    "DOCUMENTS": 5,
    "SALES": 6,
    "LICENSE": 7,
    "IMPORT_EXPORT": 8,
    "EXHIBITION": 9,
    "GENERAL": 10,
}


def classify_query(query: str) -> list[str]:
    """사용자 질문을 카테고리로 분류한다.

    다어절 구문(예: "특허 신청") 매칭은 단일 단어보다 가중치가 높다.
    이로써 "전시" 한 단어만 겹친 EXHIBITION이 "보세전시장이 무엇"으로
    GENERAL에 명확히 매칭되는 질의를 침범하지 않게 한다.

    Args:
        query: 사용자 질문 문자열

    Returns:
        매칭된 카테고리 코드 리스트 (최소 1개). 매칭 없으면 ["GENERAL"].
    """
    if not query:
        return ["GENERAL"]

    query_lower = normalize_query(query)
    scores: dict[str, float] = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = 0.0
        for keyword in keywords:
            kw = keyword.lower().strip()
            if not kw:
                continue
            if kw in query_lower:
                # 공백을 포함한 구문은 +2, 단일 단어는 +1
                if " " in kw:
                    score += 2.0
                else:
                    score += 1.0
                # 길이 가중치: 5자 이상 +0.6, 3자 이상 +0.3
                if len(kw) >= 5:
                    score += 0.6
                elif len(kw) >= 3:
                    score += 0.3
        if score > 0:
            scores[category] = score

    if not scores:
        return ["GENERAL"]

    max_score = max(scores.values())
    results = [cat for cat, sc in scores.items() if abs(sc - max_score) < 1e-6]

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

    def get_intent_category(self, intent_id: str) -> str:
        """의도 ID를 기존 10-category 시스템으로 매핑한다.

        Args:
            intent_id: 의도 ID (예: "sysqual_001", "bonded_exhibition_definition")

        Returns:
            기존 카테고리 코드 (예: "GENERAL", "LICENSE")
        """
        if not intent_id or intent_id not in self.intents:
            return "GENERAL"

        intent = self.intents[intent_id]
        # 'domain' 필드가 없으면 intent_id 또는 description으로 카테고리 추론
        domain = intent.get("domain", "")
        if not domain:
            # intent_id 또는 description에서 카테고리 추론
            description = intent.get("description", "")
            domain = intent_id + " " + description

        # domain 문자열에서 카테고리 매핑.
        # 더 구체적인 카테고리(FOOD/SAMPLE/PENALTIES/CONTACT/SALES)를
        # 광범위한 IMPORT_EXPORT보다 먼저 매칭하여 의도 분류기가 도메인의
        # "반입/반출" 단어 한 개로 IMPORT_EXPORT를 잘못 반환하는 것을 방지.
        if "Food" in domain or "식품" in domain or "시식" in domain or "tasting" in domain.lower():
            return "FOOD_TASTING"
        elif "Sample" in domain or "견본" in domain or "샘플" in domain:
            return "SAMPLE"
        elif "Penalty" in domain or "벌칙" in domain or "제재" in domain or "처벌" in domain or "벌금" in domain:
            return "PENALTIES"
        elif "Support" in domain or "문의" in domain or "연락" in domain or "고객지원" in domain or "관세사" in domain:
            return "CONTACT"
        elif "Sales" in domain or "판매" in domain or "직매" in domain or "결제" in domain:
            return "SALES"
        elif "Document" in domain or "서류" in domain or "문서" in domain or "신고서" in domain:
            return "DOCUMENTS"
        elif "License" in domain or "특허" in domain or "permit" in domain.lower():
            return "LICENSE"
        elif "System & Qualification" in domain or "제도" in domain or "자격" in domain:
            return "GENERAL"
        elif "Exhibition" in domain or "전시" in domain or "시연" in domain or "데모" in domain:
            return "EXHIBITION"
        elif "Import" in domain or "Export" in domain or "반입" in domain or "반출" in domain:
            return "IMPORT_EXPORT"

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

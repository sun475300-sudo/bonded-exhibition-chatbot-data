"""질문 의도 분류기 모듈.

사용자 질문을 10개 카테고리 중 하나 이상으로 분류한다.
"""

from src.utils import normalize_query

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

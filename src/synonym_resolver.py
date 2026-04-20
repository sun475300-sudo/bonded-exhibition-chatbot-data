"""
Korean synonym resolver for the bonded exhibition hall (보세전시장) chatbot.

Maps common colloquial/informal Korean terms to their canonical forms
used in the chatbot's FAQ entries and classifier keywords.
"""

# Mapping of synonym -> canonical form.
# Longer synonyms are checked first to avoid partial-match issues.
SYNONYMS: dict[str, str] = {
    # 물품 (goods/items)
    "물건": "물품",
    "아이템": "물품",
    "제품": "물품",
    # 판매 (selling)
    "팔다": "판매",
    "팔아": "판매",
    "팔": "판매",
    "직매": "판매",
    "현장판매": "판매",
    "즉석판매": "판매",
    # 구매 (buying)
    "사다": "구매",
    "사": "구매",
    # 반입 (bring in)
    "넣다": "반입",
    "넣어": "반입",
    "들여오다": "반입",
    "들여올": "반입",
    "가져오다": "반입",
    "가져올": "반입",
    # 반출 (take out)
    "빼다": "반출",
    "빼": "반출",
    "가져가다": "반출",
    "가져가": "반출",
    "꺼내다": "반출",
    # 반송 (return/send back)
    "보내다": "반송",
    "보내": "반송",
    "돌려보내다": "재반출",
    "다시 보내": "재반출",
    "해외로 보내": "재반출",
    # 면허 (license)
    "허가": "면허",
    "수입허가": "수입면허",
    "통관허가": "수입면허",
    # 관세 (customs duty)
    "세금": "관세",
    "세율": "관세",
    "관세율": "관세",
    # 벌칙 (penalty)
    "벌": "벌칙",
    "처벌": "벌칙",
    "제재": "벌칙",
    "과태료": "벌칙",
    "벌금": "벌칙",
    # 위반 (violation)
    "잘못": "위반",
    "어기": "위반",
    # 연락처 (contact info)
    "전화": "연락처",
    "번호": "연락처",
    "연락": "연락처",
    # 서류 (documents)
    "종이": "서류",
    "서류들": "서류",
    "서식": "서류",
    "양식": "서류",
    "문서": "서류",
    "첨부": "서류",
    # NOTE: "기한"과 "기간"을 "특허기간"으로 일괄 변환하면 "반입 신고 기한" 등에서
    # 오매칭이 발생하므로 해당 매핑을 제거함.
    # 연장 (extension/renewal)
    "갱신": "연장",
    "기간 연장": "연장",
    "기간 갱신": "연장",
    # 전시회 (exhibition)
    "쇼": "전시회",
    "박람회": "전시회",
    "전람회": "전시회",
    # 시식 (tasting)
    "맛보기": "시식",
    "시음": "시식",
    "먹어보기": "시식",
    "시음행사": "시식",
    # 견본품 (sample)
    "샘플": "견본품",
    "홍보용": "견본품",
    "홍보물": "견본품",
    # 무료 배포 (free distribution)
    "공짜": "무료 배포",
    "무상": "무료 배포",
    # 전시 (display/exhibit)
    "보여주다": "전시",
    "전시하다": "전시",
    "진열": "전시",
    # 시연/데모 (demonstration)
    "데모": "시연",
    "시범": "시연",
    "체험": "시연",
    # ATA Carnet / ATA 까르네
    "ATA Carnet": "ATA 까르네",
    "ATA carnet": "ATA 까르네",
    "ata carnet": "ATA 까르네",
    "카르네": "ATA 까르네",
    # 보세운송
    "운송신고": "보세운송",
    "이동": "보세운송",
    # 수입신고 (import declaration)
    "통관신고": "수입신고",
    "수입 신고": "수입신고",
    # 재수출 (re-export)
    "재반출": "재수출",
    "해외 반송": "재수출",
    # 기한/기간 (deadline)
    "언제까지": "기한",
    "마감": "기한",
    "며칠까지": "기한",
    # 특허 (license/patent)
    "등록": "특허",
    "지정": "특허",
    "특허받": "특허",
    "보세 특허": "특허",
    # 운영인 (operator)
    "주최자": "운영인",
    "행사 주최": "운영인",
    "행사주최": "운영인",
    # UNI-PASS
    "유니패스": "uni-pass",
    "유니 패스": "uni-pass",
    # 한글 표시 (Korean labeling)
    "한글 라벨": "한글 표시",
    "한글라벨": "한글 표시",
    "라벨": "한글 표시",
    # 검역 (quarantine)
    "검역": "요건확인",
    "식약처": "요건확인",
    "위생 검사": "요건확인",
    # 불복 (appeal)
    "항소": "불복",
    "이의": "불복",
    "이의신청": "불복",
    "심사청구": "불복",
}

# Pre-sorted keys: longest first so that longer matches take priority
# (e.g. "서류들" is matched before "서류" substring issues).
_SORTED_KEYS: list[str] = sorted(SYNONYMS.keys(), key=len, reverse=True)


def resolve_synonyms(query: str) -> str:
    """Replace synonym occurrences in *query* with their canonical forms.

    Each synonym token found in the query string is substituted with the
    corresponding canonical term.  Longer synonyms are checked first to
    prevent partial-match collisions.

    Args:
        query: The raw user query string.

    Returns:
        A new string with synonyms replaced by canonical forms.
    """
    result = query
    for synonym in _SORTED_KEYS:
        if synonym in result:
            result = result.replace(synonym, SYNONYMS[synonym])
    return result


def expand_query(query: str) -> str:
    """Append canonical forms to *query* while preserving the original text.

    This is useful for search expansion: the original wording is kept so
    that exact-match scoring still works, and the canonical terms are
    appended so that FAQ/keyword lookups can also match.

    Args:
        query: The raw user query string.

    Returns:
        The original query with any resolved canonical terms appended
        (space-separated).  If no synonyms are found the original query
        is returned unchanged.
    """
    canonical_terms: list[str] = []
    for synonym in _SORTED_KEYS:
        if synonym in query:
            canonical = SYNONYMS[synonym]
            if canonical not in canonical_terms:
                canonical_terms.append(canonical)

    if not canonical_terms:
        return query

    return query + " " + " ".join(canonical_terms)

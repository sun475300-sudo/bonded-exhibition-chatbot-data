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
    # 판매 (selling)
    "팔다": "판매",
    "팔아": "판매",
    "팔": "판매",
    # 구매 (buying)
    "사다": "구매",
    "사": "구매",
    # 반입 (bring in)
    "넣다": "반입",
    "넣어": "반입",
    # 반출 (take out)
    "빼다": "반출",
    "빼": "반출",
    # 반송 (return/send back)
    "보내다": "반송",
    "보내": "반송",
    # 면허 (license)
    "허가": "면허",
    # 관세 (customs duty)
    "세금": "관세",
    # 벌칙 (penalty)
    "벌": "벌칙",
    # 위반 (violation)
    "잘못": "위반",
    # 연락처 (contact info)
    "전화": "연락처",
    # 서류 (documents)
    "종이": "서류",
    "서류들": "서류",
    # 특허기간 (license period)
    "기한": "특허기간",
    "기간": "특허기간",
    "만료": "특허기간",
    # 연장 (extension/renewal)
    "갱신": "연장",
    "다시": "연장",
    # 전시회 (exhibition)
    "쇼": "전시회",
    "행사": "전시회",
    # 시식 (tasting)
    "맛보기": "시식",
    "시음": "시식",
    # 무료 배포 (free distribution)
    "공짜": "무료 배포",
    "무료": "무료 배포",
    # 무료배포 (free distribution - verb forms)
    "나눠주다": "무료배포",
    "배포": "무료배포",
    # 전시 (display/exhibit)
    "보여주다": "전시",
    "전시하다": "전시",
    # 관할 세관 (jurisdictional customs office)
    "세관": "관할 세관",
    # 세관장확인 (customs clearance verification)
    "통관": "세관장확인",
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

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
    # NOTE: "기한"과 "기간"을 "특허기간"으로 일괄 변환하면 "반입 신고 기한" 등에서
    # 오매칭이 발생하므로 해당 매핑을 제거함.
    # 연장 (extension/renewal)
    "갱신": "연장",
    # 전시회 (exhibition)
    "쇼": "전시회",
    # 시식 (tasting)
    "맛보기": "시식",
    "시음": "시식",
    # 무료 배포 (free distribution)
    "공짜": "무료 배포",
    # 전시 (display/exhibit)
    "보여주다": "전시",
    "전시하다": "전시",
    # ATA Carnet / ATA 까르네
    "ATA Carnet": "ATA 까르네",
    "ATA carnet": "ATA 까르네",
    "ata carnet": "ATA 까르네",
    "카르네": "ATA 까르네",
    # 보세운송
    "운송신고": "보세운송",
    # 수입신고 (import declaration)
    "통관신고": "수입신고",
    # 재수출 (re-export)
    "재반출": "재수출",
    # 기한/기간 (deadline)
    "언제까지": "기한",
    "마감": "기한",
    "며칠까지": "기한",
    # 특허 (patent/license)
    "등록": "특허",
    "지정": "특허",
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

    Synonyms are matched only when they appear as standalone tokens
    (whitespace-separated) or as full token prefixes. This prevents
    false positives like "허가"가 "특허가" 안에서 매칭되어 의도치 않은
    "면허" 동의어가 부착되는 문제를 차단한다.

    Args:
        query: The raw user query string.

    Returns:
        The original query with any resolved canonical terms appended
        (space-separated).  If no synonyms are found the original query
        is returned unchanged.
    """
    tokens = query.split()
    canonical_terms: list[str] = []
    for synonym in _SORTED_KEYS:
        # 토큰이 동의어와 정확히 일치하거나, 동의어 + 한국어 조사 형태인 경우만 매칭.
        # "허가" 동의어가 "특허가"에서 임의로 매칭되는 것을 방지하기 위해
        # in 검사 대신 토큰 시작 위치에서의 매칭을 요구한다.
        matched = False
        for tok in tokens:
            if tok == synonym or tok.startswith(synonym):
                # 영어/공백 동의어는 부분 일치도 허용 (예: "ATA Carnet")
                if " " in synonym:
                    matched = synonym in query
                    break
                matched = True
                break
        if matched:
            canonical = SYNONYMS[synonym]
            if canonical not in canonical_terms:
                canonical_terms.append(canonical)

    if not canonical_terms:
        return query

    return query + " " + " ".join(canonical_terms)

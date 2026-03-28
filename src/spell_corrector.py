"""한국어 맞춤법 교정 모듈 (보세전시장 챗봇 도메인).

사용자 입력의 오타를 도메인 용어 사전 기반으로 교정한다.
순수 파이썬으로 구현하며 외부 라이브러리를 사용하지 않는다.
"""


# ---------------------------------------------------------------------------
# 한글 자모 분해 유틸리티 (동점 해소용)
# ---------------------------------------------------------------------------
_CHOSEONG = (
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)
_JUNGSEONG = (
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
)
_JONGSEONG = (
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)

_HANGUL_BASE = 0xAC00


def _decompose_char(ch: str) -> tuple[int, int, int] | None:
    """한글 음절을 초·중·종성 인덱스로 분해한다. 한글이 아니면 None."""
    code = ord(ch) - _HANGUL_BASE
    if code < 0 or code > 11171:
        return None
    cho = code // (21 * 28)
    jung = (code % (21 * 28)) // 28
    jong = code % 28
    return (cho, jung, jong)


def _jamo_similarity(a: str, b: str) -> int:
    """두 문자열의 자모 수준 공통 성분 수를 반환한다."""
    score = 0
    for ca, cb in zip(a, b):
        da = _decompose_char(ca)
        db = _decompose_char(cb)
        if da is not None and db is not None:
            if da[0] == db[0]:
                score += 1
            if da[1] == db[1]:
                score += 1
            if da[2] == db[2]:
                score += 1
        elif ca == cb:
            score += 3
    return score

# ---------------------------------------------------------------------------
# 도메인 용어 사전 (10개 카테고리 전체를 포괄)
# ---------------------------------------------------------------------------
KNOWN_TERMS: set[str] = {
    # ── GENERAL (일반 개념) ──
    "보세전시장", "보세구역", "보세창고", "외국물품", "내국물품",
    "제도", "정의", "개념", "뜻", "무엇", "차이", "비교", "구분",
    "이용", "자격", "국산",

    # ── LICENSE (특허·운영) ──
    "특허", "특허기간", "특허신청", "특허장소", "특허연장",
    "운영", "운영인", "설치", "설치특허", "갱신", "연장", "변경",

    # ── IMPORT_EXPORT (반출입) ──
    "반입", "반출", "반출입", "반출입신고", "반입신고", "반출신고",
    "물품검사", "재반출", "반송", "잔류", "미반출",
    "세관검사", "수입면허", "수입신고", "수출신고",

    # ── EXHIBITION (전시) ──
    "전시", "전시회", "전시물", "전시용", "전시용품", "전시목적",
    "장치", "진열", "디스플레이", "박람회", "전람회",
    "시연", "데모", "시범", "체험",

    # ── SALES (판매) ──
    "판매", "직매", "현장판매", "인도", "구매", "매매",
    "통관", "통관전", "계약", "주문",

    # ── SAMPLE (견본품) ──
    "견본품", "견본", "샘플", "홍보용", "시료", "무료배포",
    "견본품반출", "견본품관세", "견본품과세",

    # ── FOOD_TASTING (시식·식품) ──
    "시식", "시식용", "시식용식품", "식품", "음식",
    "요건확인", "세관장확인", "식약처", "검역", "위생",
    "잔량", "폐기",

    # ── DOCUMENTS (서류) ──
    "서류", "신고서", "신청서", "구비서류", "제출", "양식",
    "서식", "첨부", "문서", "반출입신고서", "허가신청",

    # ── PENALTIES (벌칙·제재) ──
    "벌칙", "제재", "과태료", "벌금", "처벌", "위반", "처분",
    "불이익", "과징금", "무허가", "밀수", "밀수출입",
    "특허취소", "업무정지", "의무위반",

    # ── CONTACT (문의·연락처) ──
    "문의", "전화", "연락처", "담당", "상담",
    "고객지원", "기술지원", "보세산업과",
    "관세청", "관할세관", "세관장", "세관",

    # ── 법령·고시 용어 ──
    "관세법", "관세법시행령", "관세청고시",
    "보세전시장운영고시", "수입식품안전관리특별법",

    # ── 에스컬레이션 관련 ──
    "에스컬레이션", "유권해석", "법적판단",
    "유니패스", "전산", "시스템오류",

    # ── 기타 자주 등장하는 표현 ──
    "허가", "승인", "신고", "확인", "절차", "규정",
    "기간", "회기", "준비기간", "정리기간",
    "수입", "수출", "관세", "면세", "감면",
}


# ---------------------------------------------------------------------------
# Levenshtein 편집 거리 (순수 파이썬 – Wagner-Fischer 알고리즘)
# ---------------------------------------------------------------------------
def levenshtein_distance(s: str, t: str) -> int:
    """두 문자열 사이의 Levenshtein 편집 거리를 반환한다.

    삽입·삭제·치환 각각 비용 1로 계산한다.

    Args:
        s: 원본 문자열
        t: 대상 문자열

    Returns:
        편집 거리 (0 이상의 정수)
    """
    len_s = len(s)
    len_t = len(t)

    # 빈 문자열 단축 처리
    if len_s == 0:
        return len_t
    if len_t == 0:
        return len_s

    # 메모리 최적화: 두 행만 유지
    prev_row: list[int] = list(range(len_t + 1))
    curr_row: list[int] = [0] * (len_t + 1)

    for i in range(1, len_s + 1):
        curr_row[0] = i
        for j in range(1, len_t + 1):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            curr_row[j] = min(
                prev_row[j] + 1,       # 삭제
                curr_row[j - 1] + 1,    # 삽입
                prev_row[j - 1] + cost, # 치환
            )
        prev_row, curr_row = curr_row, prev_row

    return prev_row[len_t]


# ---------------------------------------------------------------------------
# 단일 토큰 교정
# ---------------------------------------------------------------------------
def correct_term(term: str, max_distance: int = 2) -> str | None:
    """도메인 용어 사전에서 *term* 에 가장 가까운 단어를 찾는다.

    Args:
        term: 교정 대상 토큰
        max_distance: 허용 최대 편집 거리 (기본 2)

    Returns:
        교정된 용어 문자열, 또는 적합한 후보가 없으면 ``None``.
        이미 사전에 있는 경우에도 그 단어를 그대로 반환한다.
    """
    if not term:
        return None

    # 정확 일치
    if term in KNOWN_TERMS:
        return term

    best_match: str | None = None
    best_distance: int = max_distance + 1

    for known in KNOWN_TERMS:
        # 길이 차이가 max_distance 초과이면 계산 생략 (가지치기)
        if abs(len(known) - len(term)) > max_distance:
            continue

        dist = levenshtein_distance(term, known)
        if dist < best_distance:
            best_distance = dist
            best_match = known
        elif dist == best_distance and best_match is not None:
            # 동점일 경우: (1) 입력과 길이가 더 가까운 쪽,
            # (2) 같으면 공통 문자가 더 많은 쪽,
            # (3) 같으면 자모 수준 유사도가 높은 쪽, (4) 사전순
            len_diff_new = abs(len(known) - len(term))
            len_diff_old = abs(len(best_match) - len(term))
            if len_diff_new < len_diff_old:
                best_match = known
            elif len_diff_new == len_diff_old:
                common_new = sum(1 for a, b in zip(term, known) if a == b)
                common_old = sum(1 for a, b in zip(term, best_match) if a == b)
                if common_new > common_old:
                    best_match = known
                elif common_new == common_old:
                    jamo_new = _jamo_similarity(term, known)
                    jamo_old = _jamo_similarity(term, best_match)
                    if jamo_new > jamo_old:
                        best_match = known
                    elif jamo_new == jamo_old and known < best_match:
                        best_match = known

    if best_distance <= max_distance:
        return best_match
    return None


# ---------------------------------------------------------------------------
# 쿼리 전체 교정
# ---------------------------------------------------------------------------
def correct_query(query: str) -> tuple[str, list[dict]]:
    """사용자 입력 쿼리를 토큰 단위로 교정한다.

    공백 기준으로 토큰을 분리한 뒤 각 토큰을 ``correct_term`` 으로 검사한다.
    교정이 발생한 토큰 정보를 함께 반환한다.

    Args:
        query: 사용자 원문 질의

    Returns:
        ``(corrected_query, corrections)`` 튜플.

        - ``corrected_query``: 교정 후 질의 문자열
        - ``corrections``: 교정 내역 리스트.
          각 원소는 ``{"original": str, "corrected": str, "distance": int}``
    """
    if not query or not query.strip():
        return (query, [])

    tokens = query.split()
    corrected_tokens: list[str] = []
    corrections: list[dict] = []

    for token in tokens:
        if token in KNOWN_TERMS:
            corrected_tokens.append(token)
            continue

        suggestion = correct_term(token)
        if suggestion is not None and suggestion != token:
            dist = levenshtein_distance(token, suggestion)
            # 공통 문자 비율이 50% 미만이면 무관한 단어로 판단하여 교정 생략
            common_chars = sum(1 for a, b in zip(token, suggestion) if a == b)
            max_len = max(len(token), len(suggestion))
            if max_len > 0 and common_chars / max_len < 0.5:
                corrected_tokens.append(token)
                continue
            corrections.append({
                "original": token,
                "corrected": suggestion,
                "distance": dist,
            })
            corrected_tokens.append(suggestion)
        else:
            # 교정 대상이 아님 (조사·어미·숫자 등)
            corrected_tokens.append(token)

    corrected_query = " ".join(corrected_tokens)
    return (corrected_query, corrections)

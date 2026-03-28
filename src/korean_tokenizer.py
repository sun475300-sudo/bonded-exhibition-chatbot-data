"""한국어 토크나이저 모듈.

보세전시장 챗봇 전용 한국어 토크나이저.
외부 라이브러리 없이 순수 Python으로 구현하며,
도메인 사전 기반 복합명사 처리와 조사/어미 분리를 지원한다.
"""


class KoreanTokenizer:
    """한국어 토크나이저 클래스.

    공백 기반 분리 후 조사·어미 제거, 도메인 사전 기반 복합명사 보존,
    n-gram 추출 기능을 제공한다.
    """

    # 도메인 특화 용어 — 분리하지 않고 그대로 보존
    DOMAIN_TERMS: set[str] = {
        "보세전시장",
        "보세구역",
        "보세화물",
        "보세운송",
        "견본품",
        "시식용식품",
        "시식용",
        "반출입",
        "반입신고",
        "반출신고",
        "세관장확인",
        "세관장",
        "세관신고",
        "관세법",
        "관세청",
        "관세사",
        "수입신고",
        "수출신고",
        "통관절차",
        "통관",
        "원산지증명",
        "원산지",
        "전시물품",
        "전시장",
        "전시회",
        "면세",
        "면세범위",
        "과세",
        "과세가격",
        "부가세",
        "특허보세",
        "장치기간",
        "재반출",
        "재반입",
        "감면",
        "HS코드",
        "FTA",
    }

    # 제거할 한국어 조사·어미 (긴 것부터 매칭해야 정확)
    SUFFIXES: list[str] = sorted(
        [
            "에서",
            "으로",
            "합니다",
            "입니다",
            "인가요",
            "하나요",
            "해요",
            "까요",
            "나요",
            "하다",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
            "에",
            "로",
            "도",
            "의",
            "와",
            "과",
            "요",
        ],
        key=len,
        reverse=True,
    )

    def __init__(self) -> None:
        """토크나이저를 초기화한다."""
        # 알려진 용어 집합 (1글자 필터링 예외 처리용)
        self._known_short_terms: set[str] = {"세", "관", "법", "세관", "FTA"}

    def _strip_suffix(self, token: str) -> str:
        """토큰에서 한국어 조사·어미를 제거한다.

        도메인 용어에 해당하면 원형 그대로 반환한다.

        Args:
            token: 조사·어미를 제거할 토큰.

        Returns:
            조사·어미가 제거된 토큰.
        """
        if token in self.DOMAIN_TERMS:
            return token

        for suffix in self.SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix):
                candidate = token[: -len(suffix)]
                # 제거 후 남는 부분이 너무 짧으면 원래 토큰 유지
                if len(candidate) >= 1:
                    return candidate
        return token

    def _split_compound(self, token: str) -> list[str]:
        """도메인 사전을 이용해 복합명사를 분리한다.

        토큰 내부에 도메인 용어가 포함되어 있으면 해당 부분을 추출한다.
        도메인 용어 자체이거나 도메인 용어를 포함하지 않으면 그대로 반환한다.

        Args:
            token: 분리할 토큰.

        Returns:
            분리된 토큰 리스트.
        """
        if token in self.DOMAIN_TERMS:
            return [token]

        found: list[tuple[int, int, str]] = []
        for term in self.DOMAIN_TERMS:
            start = token.find(term)
            if start != -1:
                found.append((start, start + len(term), term))

        if not found:
            return [token]

        # 위치순 정렬, 겹치는 부분은 긴 것 우선
        found.sort(key=lambda x: (x[0], -(x[1] - x[0])))

        parts: list[str] = []
        prev_end = 0
        for start, end, term in found:
            if start < prev_end:
                continue
            if start > prev_end:
                remainder = token[prev_end:start]
                if remainder:
                    parts.append(remainder)
            parts.append(term)
            prev_end = end

        if prev_end < len(token):
            remainder = token[prev_end:]
            if remainder:
                parts.append(remainder)

        return parts

    def tokenize(self, text: str) -> list[str]:
        """텍스트를 의미 단위 토큰으로 분리한다.

        1. 공백 기준 분리
        2. 구두점 제거
        3. 조사·어미 분리
        4. 복합명사 분리 (도메인 사전 기반)
        5. 2글자 미만 토큰 필터링 (알려진 용어 예외)

        Args:
            text: 토크나이즈할 텍스트.

        Returns:
            토큰 리스트.
        """
        if not text or not text.strip():
            return []

        raw_tokens = text.strip().lower().split()
        result: list[str] = []

        for raw in raw_tokens:
            # 구두점 제거
            cleaned = raw.strip("?.,!·()\"'~:;")
            if not cleaned:
                continue

            # 조사·어미 제거
            stripped = self._strip_suffix(cleaned)

            # 복합명사 분리
            parts = self._split_compound(stripped)

            for part in parts:
                if len(part) >= 2:
                    result.append(part)
                elif part in self._known_short_terms or part in self.DOMAIN_TERMS:
                    result.append(part)

        return result

    def extract_ngrams(self, text: str, n: int = 2) -> list[str]:
        """텍스트에서 문자 n-gram을 추출한다.

        공백·구두점을 제거한 순수 문자열에서 n-gram을 생성하여
        퍼지 매칭에 활용할 수 있다.

        Args:
            text: n-gram을 추출할 텍스트.
            n: n-gram 크기 (기본값 2, 바이그램).

        Returns:
            문자 n-gram 리스트.
        """
        if not text:
            return []

        # 공백·구두점 제거
        cleaned = ""
        for ch in text.lower():
            if ch.isalnum() or ("\uac00" <= ch <= "\ud7a3"):
                cleaned += ch

        if len(cleaned) < n:
            return [cleaned] if cleaned else []

        return [cleaned[i : i + n] for i in range(len(cleaned) - n + 1)]

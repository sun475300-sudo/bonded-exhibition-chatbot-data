"""국제화(i18n) 지원 모듈.

JSON 기반 번역 파일을 로드하고, 키 기반 번역 및 언어 감지 기능을 제공한다.
번역 파일은 data/locales/ 디렉토리에 저장된다.
"""

import json
import os
import re
import unicodedata

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(BASE_DIR, "data", "locales")

# 지원 언어 코드
SUPPORTED_LANGUAGES = ("ko", "en", "cn", "jp", "vi", "th")


class I18nManager:
    """국제화 관리 클래스.

    JSON 번역 파일을 로드하고, 키 기반 번역 문자열 조회 및
    언어 감지 기능을 제공한다.
    """

    def __init__(self, locales_dir: str | None = None):
        self._locales_dir = locales_dir or LOCALES_DIR
        self._cache: dict[str, dict] = {}

    def load_locale(self, lang_code: str) -> dict:
        """번역 파일을 로드한다.

        이미 로드된 언어는 캐시에서 반환한다.

        Args:
            lang_code: 언어 코드 (예: 'ko', 'en', 'cn', 'jp', 'vi', 'th')

        Returns:
            번역 데이터 딕셔너리. 파일이 없으면 빈 딕셔너리를 반환한다.
        """
        if lang_code in self._cache:
            return self._cache[lang_code]

        file_path = os.path.join(self._locales_dir, f"{lang_code}.json")
        if not os.path.exists(file_path):
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._cache[lang_code] = data
        return data

    def translate(self, key: str, lang: str, **kwargs) -> str:
        """키에 해당하는 번역 문자열을 반환한다.

        키는 점(.)으로 구분된 경로를 사용한다 (예: 'ui.header', 'messages.welcome').
        대상 언어에 키가 없으면 한국어(ko) 폴백을 시도한다.
        한국어에도 없으면 키 자체를 반환한다.

        Args:
            key: 번역 키 (점으로 구분된 경로)
            lang: 대상 언어 코드
            **kwargs: 문자열 포맷 파라미터

        Returns:
            번역된 문자열
        """
        value = self._resolve_key(key, lang)

        # 대상 언어에 없으면 한국어 폴백
        if value is None and lang != "ko":
            value = self._resolve_key(key, "ko")

        # 한국어에도 없으면 키 자체를 반환
        if value is None:
            return key

        # 파라미터 치환
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, IndexError):
                pass

        return value

    def get_supported_languages(self) -> list[str]:
        """지원 언어 코드 목록을 반환한다.

        Returns:
            지원 언어 코드 리스트
        """
        return list(SUPPORTED_LANGUAGES)

    def detect_language(self, text: str) -> str:
        """텍스트의 언어를 감지한다.

        한국어, 영어, 중국어, 일본어, 베트남어, 태국어를 감지한다.

        Args:
            text: 분석할 텍스트

        Returns:
            언어 코드 문자열 ('ko', 'en', 'cn', 'jp', 'vi', 'th')
        """
        if not text or not text.strip():
            return "ko"

        counts = {"ko": 0, "en": 0, "cn": 0, "jp": 0, "vi": 0, "th": 0}
        total = 0

        # 베트남어 특수 문자 감지 (라틴 + 성조 부호 조합)
        vi_pattern = re.compile(
            r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợ"
            r"ùúủũụưứừửữựỳýỷỹỵđ"
            r"ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ"
            r"ÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴĐ]"
        )
        vi_count = len(vi_pattern.findall(text))
        if vi_count > 0:
            counts["vi"] = vi_count

        for char in text:
            if char.isspace() or char in ".,!?;:'\"-()[]{}0123456789":
                continue

            total += 1
            cp = ord(char)

            # 태국어: U+0E00 - U+0E7F
            if 0x0E00 <= cp <= 0x0E7F:
                counts["th"] += 1
            # 한글: 가-힣, ㄱ-ㅎ, ㅏ-ㅣ
            elif (0xAC00 <= cp <= 0xD7A3) or (0x3131 <= cp <= 0x3163):
                counts["ko"] += 1
            # 일본어: 히라가나, 가타카나
            elif (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF):
                counts["jp"] += 1
            # CJK 통합 한자 (일본어 가나가 없으면 중국어로 판단)
            elif 0x4E00 <= cp <= 0x9FFF:
                counts["cn"] += 1
            # 라틴 문자 (베트남어 카운트는 이미 위에서 처리)
            elif char.isalpha():
                # 베트남어 특수 문자가 아닌 라틴 문자는 영어로 카운트
                if not vi_pattern.match(char):
                    counts["en"] += 1

        if total == 0:
            return "ko"

        # 태국어: 태국 문자가 있으면 태국어
        if counts["th"] > 0:
            return "th"

        # 일본어: 가나가 있으면 일본어, CJK 한자도 일본어에 포함
        if counts["jp"] > 0:
            return "jp"

        # 베트남어: 베트남어 특수 문자가 있으면 베트남어
        if counts["vi"] > 0:
            return "vi"

        # 가장 비율이 높은 언어 선택
        max_lang = max(counts, key=counts.get)
        if counts[max_lang] == 0:
            return "ko"

        return max_lang

    def _resolve_key(self, key: str, lang: str) -> str | None:
        """점으로 구분된 키를 해석하여 번역 값을 반환한다.

        Args:
            key: 점으로 구분된 키 경로
            lang: 언어 코드

        Returns:
            번역 문자열 또는 None
        """
        data = self.load_locale(lang)
        if not data:
            return None

        parts = key.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        if isinstance(current, str):
            return current
        return None

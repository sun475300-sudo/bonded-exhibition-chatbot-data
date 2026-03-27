"""다국어 지원 모듈.

답변의 구조 키워드(헤더/라벨)를 번역하고, 언어 감지 기능을 제공한다.
법령 정확성을 위해 답변 본문(한국어)은 그대로 유지하며,
각 언어별 안내 문구를 추가한다.
"""

import re
import unicodedata


# 지원 언어 코드
SUPPORTED_LANGUAGES = ("ko", "en", "cn", "jp")

# 언어별 구조 키워드 번역 매핑
HEADER_TRANSLATIONS = {
    "en": {
        "결론:": "Conclusion:",
        "설명:": "Description:",
        "민원인이 확인할 사항:": "Items to Confirm:",
        "근거:": "Legal Basis:",
        "추가 안내:": "Additional Information:",
        "안내:": "Notice:",
    },
    "cn": {
        "결론:": "结论:",
        "설명:": "说明:",
        "민원인이 확인할 사항:": "需确认事项:",
        "근거:": "法律依据:",
        "추가 안내:": "补充说明:",
        "안내:": "提示:",
    },
    "jp": {
        "결론:": "結論:",
        "설명:": "説明:",
        "민원인이 확인할 사항:": "確認事項:",
        "근거:": "法的根拠:",
        "추가 안내:": "追加案内:",
        "안내:": "案内:",
    },
}

# 언어별 안내 문구
LANGUAGE_NOTICES = {
    "en": (
        "This is a Korean legal chatbot. "
        "The legal content is provided in Korean for accuracy."
    ),
    "cn": (
        "这是韩国法律咨询聊天机器人。"
        "为确保准确性，法律内容以韩语提供。"
    ),
    "jp": (
        "これは韓国の法律チャットボットです。"
        "正確性のため、法律の内容は韓国語で提供されます。"
    ),
}

# 언어별 주제 안내 번역
TOPIC_INTRO_TRANSLATIONS = {
    "en": "Your inquiry is regarding [{topic}].",
    "cn": "您咨询的内容是关于[{topic}]的事项。",
    "jp": "お問い合わせの内容は[{topic}]に関する事項です。",
}


def detect_language(text: str) -> str:
    """텍스트의 언어를 감지한다.

    한글, 영어, 중국어, 일본어 문자의 비율로 판단한다.

    Args:
        text: 분석할 텍스트

    Returns:
        언어 코드 문자열 ('ko', 'en', 'cn', 'jp')
    """
    if not text or not text.strip():
        return "ko"

    counts = {"ko": 0, "en": 0, "cn": 0, "jp": 0}
    total = 0

    for char in text:
        if char.isspace() or char in ".,!?;:'\"-()[]{}0123456789":
            continue

        total += 1
        cp = ord(char)

        # 한글: 가-힣, ㄱ-ㅎ, ㅏ-ㅣ
        if (0xAC00 <= cp <= 0xD7A3) or (0x3131 <= cp <= 0x3163):
            counts["ko"] += 1
        # 일본어: 히라가나, 가타카나
        elif (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF):
            counts["jp"] += 1
        # CJK 통합 한자 (중국어/일본어 공용이지만, 일본어 가나가 없으면 중국어로 판단)
        elif 0x4E00 <= cp <= 0x9FFF:
            counts["cn"] += 1
        # 라틴 문자
        elif char.isalpha():
            counts["en"] += 1

    if total == 0:
        return "ko"

    # 일본어: 가나가 있으면 일본어, CJK 한자도 일본어에 포함
    if counts["jp"] > 0:
        return "jp"

    # 가장 비율이 높은 언어 선택
    max_lang = max(counts, key=counts.get)
    if counts[max_lang] == 0:
        return "ko"

    return max_lang


def translate_response(response: str, target_lang: str) -> str:
    """답변의 구조 키워드(헤더/라벨)를 대상 언어로 번역한다.

    답변 본문(한국어 법령 내용)은 그대로 유지하며,
    구조적 키워드만 번역하고 안내 문구를 추가한다.

    Args:
        response: 원본 한국어 답변 문자열
        target_lang: 대상 언어 코드 ('ko', 'en', 'cn', 'jp')

    Returns:
        번역된 답변 문자열
    """
    if not response:
        return response

    if target_lang == "ko" or target_lang not in SUPPORTED_LANGUAGES:
        return response

    translations = HEADER_TRANSLATIONS.get(target_lang, {})
    translated = response

    # 구조 키워드 번역
    for ko_header, target_header in translations.items():
        translated = translated.replace(ko_header, target_header)

    # 주제 안내 문구 번역 ("문의하신 내용은 [...]에 관한 사항입니다.")
    topic_pattern = r"문의하신 내용은 \[(.+?)\]에 관한 사항입니다\."
    topic_match = re.search(topic_pattern, translated)
    if topic_match and target_lang in TOPIC_INTRO_TRANSLATIONS:
        topic = topic_match.group(1)
        replacement = TOPIC_INTRO_TRANSLATIONS[target_lang].format(topic=topic)
        translated = re.sub(topic_pattern, replacement, translated)

    # 안내 문구 추가
    notice = LANGUAGE_NOTICES.get(target_lang)
    if notice:
        translated = f"[{notice}]\n\n{translated}"

    return translated


class SimpleTranslator:
    """다국어 지원을 위한 번역기 클래스.

    답변의 구조적 키워드를 대상 언어로 번역하고,
    언어 감지 기능을 제공한다.
    """

    def __init__(self):
        self.supported_languages = SUPPORTED_LANGUAGES
        self.header_translations = HEADER_TRANSLATIONS
        self.language_notices = LANGUAGE_NOTICES

    def detect_language(self, text: str) -> str:
        """텍스트 언어를 감지한다."""
        return detect_language(text)

    def translate_response(self, response: str, target_lang: str) -> str:
        """답변 구조 키워드를 번역한다."""
        return translate_response(response, target_lang)

    def get_notice(self, lang: str) -> str | None:
        """해당 언어의 안내 문구를 반환한다."""
        return self.language_notices.get(lang)

    def is_supported(self, lang: str) -> bool:
        """지원 언어 여부를 확인한다."""
        return lang in self.supported_languages

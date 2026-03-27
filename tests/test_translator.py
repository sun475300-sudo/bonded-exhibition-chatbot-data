"""다국어 지원 모듈 테스트."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.translator import SimpleTranslator, detect_language, translate_response


@pytest.fixture
def translator():
    return SimpleTranslator()


class TestDetectLanguage:
    """언어 감지 테스트."""

    def test_detect_korean(self):
        assert detect_language("보세전시장에 대해 알려주세요") == "ko"

    def test_detect_english(self):
        assert detect_language("Tell me about bonded exhibition") == "en"

    def test_detect_chinese(self):
        assert detect_language("请告诉我关于保税展示场的信息") == "cn"

    def test_detect_japanese(self):
        assert detect_language("保税展示場について教えてください") == "jp"

    def test_detect_empty_string(self):
        assert detect_language("") == "ko"

    def test_detect_whitespace_only(self):
        assert detect_language("   ") == "ko"

    def test_detect_numbers_only(self):
        assert detect_language("12345") == "ko"

    def test_detect_mixed_korean_english(self):
        # 한글이 더 많으면 한국어
        result = detect_language("보세전시장 FAQ에 대한 질문입니다")
        assert result == "ko"

    def test_detect_japanese_with_kanji(self):
        # 히라가나/가타카나가 포함되면 일본어로 판단
        assert detect_language("展示場の手続きはどうですか") == "jp"

    def test_detect_none_returns_korean(self):
        assert detect_language(None) == "ko"


class TestTranslateResponse:
    """답변 번역 테스트."""

    def test_translate_to_english(self):
        response = "결론:\n- 가능합니다."
        result = translate_response(response, "en")
        assert "Conclusion:" in result
        assert "결론:" not in result
        # 안내 문구 포함
        assert "Korean legal chatbot" in result

    def test_translate_to_chinese(self):
        response = "설명:\n- 보세전시장은 특허보세구역입니다."
        result = translate_response(response, "cn")
        assert "说明:" in result
        assert "설명:" not in result
        assert "韩语" in result

    def test_translate_to_japanese(self):
        response = "근거:\n- 관세법 제176조"
        result = translate_response(response, "jp")
        assert "法的根拠:" in result
        assert "근거:" not in result
        assert "韓国語" in result

    def test_korean_no_translation(self):
        response = "결론:\n- 가능합니다."
        result = translate_response(response, "ko")
        assert result == response

    def test_unsupported_language_no_translation(self):
        response = "결론:\n- 가능합니다."
        result = translate_response(response, "fr")
        assert result == response

    def test_empty_response(self):
        assert translate_response("", "en") == ""
        assert translate_response(None, "en") is None

    def test_translate_all_headers_english(self):
        response = (
            "결론:\n- 내용1\n\n"
            "설명:\n- 내용2\n\n"
            "민원인이 확인할 사항:\n- 내용3\n\n"
            "근거:\n- 관세법\n\n"
            "추가 안내:\n- 내용4\n\n"
            "안내:\n- 내용5"
        )
        result = translate_response(response, "en")
        assert "Conclusion:" in result
        assert "Description:" in result
        assert "Items to Confirm:" in result
        assert "Legal Basis:" in result
        assert "Additional Information:" in result
        assert "Notice:" in result

    def test_translate_all_headers_chinese(self):
        response = "결론:\n- test\n\n설명:\n- test"
        result = translate_response(response, "cn")
        assert "结论:" in result
        assert "说明:" in result

    def test_translate_all_headers_japanese(self):
        response = "결론:\n- test\n\n설명:\n- test"
        result = translate_response(response, "jp")
        assert "結論:" in result
        assert "説明:" in result

    def test_translate_topic_intro_english(self):
        response = "문의하신 내용은 [보세전시장 운영]에 관한 사항입니다."
        result = translate_response(response, "en")
        assert "Your inquiry is regarding [보세전시장 운영]" in result

    def test_translate_topic_intro_chinese(self):
        response = "문의하신 내용은 [보세전시장 운영]에 관한 사항입니다."
        result = translate_response(response, "cn")
        assert "您咨询的内容是关于[보세전시장 운영]" in result

    def test_translate_topic_intro_japanese(self):
        response = "문의하신 내용은 [보세전시장 운영]에 관한 사항입니다."
        result = translate_response(response, "jp")
        assert "お問い合わせの内容は[보세전시장 운영]" in result

    def test_body_content_preserved(self):
        """본문 한국어 내용이 그대로 유지되는지 확인."""
        response = "결론:\n- 관세법 제176조에 따라 가능합니다."
        result = translate_response(response, "en")
        # 법령 내용은 한국어로 유지
        assert "관세법 제176조에 따라 가능합니다." in result


class TestSimpleTranslator:
    """SimpleTranslator 클래스 테스트."""

    def test_instance_creation(self, translator):
        assert translator is not None
        assert "ko" in translator.supported_languages
        assert "en" in translator.supported_languages
        assert "cn" in translator.supported_languages
        assert "jp" in translator.supported_languages

    def test_detect_language_method(self, translator):
        assert translator.detect_language("Hello world") == "en"
        assert translator.detect_language("안녕하세요") == "ko"

    def test_translate_response_method(self, translator):
        result = translator.translate_response("결론:\n- test", "en")
        assert "Conclusion:" in result

    def test_get_notice_korean(self, translator):
        assert translator.get_notice("ko") is None

    def test_get_notice_english(self, translator):
        notice = translator.get_notice("en")
        assert notice is not None
        assert "Korean" in notice

    def test_get_notice_chinese(self, translator):
        notice = translator.get_notice("cn")
        assert notice is not None
        assert "韩语" in notice or "韩国" in notice

    def test_get_notice_japanese(self, translator):
        notice = translator.get_notice("jp")
        assert notice is not None
        assert "韓国" in notice

    def test_is_supported(self, translator):
        assert translator.is_supported("ko") is True
        assert translator.is_supported("en") is True
        assert translator.is_supported("cn") is True
        assert translator.is_supported("jp") is True
        assert translator.is_supported("fr") is False
        assert translator.is_supported("") is False

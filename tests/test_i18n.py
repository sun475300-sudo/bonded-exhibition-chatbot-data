"""국제화(i18n) 모듈 테스트."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.i18n import I18nManager, SUPPORTED_LANGUAGES


@pytest.fixture
def i18n():
    return I18nManager()


class TestLoadLocale:
    """번역 파일 로드 테스트."""

    def test_load_korean(self, i18n):
        data = i18n.load_locale("ko")
        assert "ui" in data
        assert "messages" in data
        assert "categories" in data
        assert "sections" in data

    def test_load_english(self, i18n):
        data = i18n.load_locale("en")
        assert data["ui"]["header"] == "Bonded Exhibition Chatbot"

    def test_load_chinese(self, i18n):
        data = i18n.load_locale("cn")
        assert "ui" in data
        assert "messages" in data

    def test_load_japanese(self, i18n):
        data = i18n.load_locale("jp")
        assert "ui" in data
        assert "messages" in data

    def test_load_vietnamese(self, i18n):
        data = i18n.load_locale("vi")
        assert "ui" in data
        assert "messages" in data

    def test_load_thai(self, i18n):
        data = i18n.load_locale("th")
        assert "ui" in data
        assert "messages" in data

    def test_load_all_locales(self, i18n):
        """모든 지원 언어의 번역 파일이 로드되는지 확인."""
        for lang in SUPPORTED_LANGUAGES:
            data = i18n.load_locale(lang)
            assert data, f"Locale {lang} should not be empty"
            assert "ui" in data, f"Locale {lang} missing 'ui' section"
            assert "messages" in data, f"Locale {lang} missing 'messages' section"
            assert "categories" in data, f"Locale {lang} missing 'categories' section"
            assert "sections" in data, f"Locale {lang} missing 'sections' section"

    def test_load_nonexistent_locale(self, i18n):
        data = i18n.load_locale("xx")
        assert data == {}

    def test_locale_caching(self, i18n):
        """같은 언어를 두 번 로드하면 캐시에서 반환."""
        data1 = i18n.load_locale("ko")
        data2 = i18n.load_locale("ko")
        assert data1 is data2


class TestTranslate:
    """번역 함수 테스트."""

    def test_translate_simple_key(self, i18n):
        result = i18n.translate("ui.header", "en")
        assert result == "Bonded Exhibition Chatbot"

    def test_translate_korean(self, i18n):
        result = i18n.translate("ui.header", "ko")
        assert result == "보세전시장 민원응대 챗봇"

    def test_translate_chinese(self, i18n):
        result = i18n.translate("ui.send_button", "cn")
        assert result == "发送"

    def test_translate_japanese(self, i18n):
        result = i18n.translate("ui.send_button", "jp")
        assert result == "送信"

    def test_translate_vietnamese(self, i18n):
        result = i18n.translate("ui.send_button", "vi")
        assert result == "Gửi"

    def test_translate_thai(self, i18n):
        result = i18n.translate("ui.send_button", "th")
        assert result == "ส่ง"

    def test_translate_with_parameters(self, i18n):
        """파라미터 치환이 동작하는지 확인."""
        # 임시 번역 데이터에 파라미터가 있는 문자열 추가
        i18n._cache["en"] = {
            "test": {"greeting": "Hello, {name}! You have {count} messages."}
        }
        result = i18n.translate("test.greeting", "en", name="Alice", count=5)
        assert result == "Hello, Alice! You have 5 messages."

    def test_translate_missing_key_fallback_korean(self, i18n):
        """대상 언어에 없는 키는 한국어로 폴백."""
        # 한국어에만 있는 키를 시뮬레이션
        i18n._cache["ko"] = {"special": {"only_ko": "한국어 전용"}}
        i18n._cache["en"] = {"special": {}}
        result = i18n.translate("special.only_ko", "en")
        assert result == "한국어 전용"

    def test_translate_missing_key_returns_key(self, i18n):
        """한국어에도 없는 키는 키 자체를 반환."""
        result = i18n.translate("nonexistent.key.path", "en")
        assert result == "nonexistent.key.path"

    def test_translate_categories(self, i18n):
        result = i18n.translate("categories.GENERAL", "en")
        assert result == "General Information"

    def test_translate_sections(self, i18n):
        result = i18n.translate("sections.conclusion", "jp")
        assert result == "結論"

    def test_translate_messages(self, i18n):
        result = i18n.translate("messages.welcome", "cn")
        assert "保税展示场" in result


class TestGetSupportedLanguages:
    """지원 언어 목록 테스트."""

    def test_returns_list(self, i18n):
        langs = i18n.get_supported_languages()
        assert isinstance(langs, list)

    def test_contains_all_languages(self, i18n):
        langs = i18n.get_supported_languages()
        assert "ko" in langs
        assert "en" in langs
        assert "cn" in langs
        assert "jp" in langs
        assert "vi" in langs
        assert "th" in langs

    def test_count(self, i18n):
        langs = i18n.get_supported_languages()
        assert len(langs) == 6


class TestDetectLanguage:
    """언어 감지 테스트."""

    def test_detect_korean(self, i18n):
        assert i18n.detect_language("보세전시장에 대해 알려주세요") == "ko"

    def test_detect_english(self, i18n):
        assert i18n.detect_language("Tell me about bonded exhibition") == "en"

    def test_detect_chinese(self, i18n):
        assert i18n.detect_language("请告诉我关于保税展示场的信息") == "cn"

    def test_detect_japanese(self, i18n):
        assert i18n.detect_language("保税展示場について教えてください") == "jp"

    def test_detect_vietnamese(self, i18n):
        assert i18n.detect_language("Xin chào, tôi cần hỏi về khu triển lãm") == "vi"

    def test_detect_thai(self, i18n):
        assert i18n.detect_language("สวัสดี ฉันต้องการสอบถามเกี่ยวกับนิทรรศการ") == "th"

    def test_detect_empty_returns_korean(self, i18n):
        assert i18n.detect_language("") == "ko"

    def test_detect_none_returns_korean(self, i18n):
        assert i18n.detect_language(None) == "ko"

    def test_detect_whitespace_returns_korean(self, i18n):
        assert i18n.detect_language("   ") == "ko"

    def test_detect_numbers_only_returns_korean(self, i18n):
        assert i18n.detect_language("12345") == "ko"


class TestI18nAPI:
    """API 엔드포인트 테스트."""

    @pytest.fixture
    def client(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_get_locale(self, client):
        resp = client.get("/api/i18n/en")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ui" in data
        assert "messages" in data
        assert data["ui"]["header"] == "Bonded Exhibition Chatbot"

    def test_get_locale_korean(self, client):
        resp = client.get("/api/i18n/ko")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ui"]["header"] == "보세전시장 민원응대 챗봇"

    def test_get_locale_not_found(self, client):
        resp = client.get("/api/i18n/xx")
        assert resp.status_code == 404

    def test_get_languages(self, client):
        resp = client.get("/api/i18n/languages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "languages" in data
        langs = data["languages"]
        assert "ko" in langs
        assert "en" in langs
        assert "vi" in langs
        assert "th" in langs
        assert len(langs) == 6

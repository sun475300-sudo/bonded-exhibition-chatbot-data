"""LLM 폴백 모듈 테스트."""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from src.llm_fallback import (
    RateLimiter,
    ResponseCache,
    LLMFallbackProvider,
    get_llm_provider,
    HAS_ANTHROPIC,
)


class TestRateLimiter:
    """RateLimiter 테스트."""

    def test_rate_limiter_initialization(self):
        """RateLimiter 초기화 테스트."""
        limiter = RateLimiter(max_calls=5, window_seconds=60)

        assert limiter.max_calls == 5
        assert limiter.window_seconds == 60
        assert len(limiter.calls) == 0

    def test_rate_limiter_allows_calls_within_limit(self):
        """제한 내 호출 허용 테스트."""
        limiter = RateLimiter(max_calls=3, window_seconds=60)

        assert limiter.is_allowed()  # 1
        assert limiter.is_allowed()  # 2
        assert limiter.is_allowed()  # 3

    def test_rate_limiter_blocks_calls_over_limit(self):
        """제한 초과 호출 차단 테스트."""
        limiter = RateLimiter(max_calls=2, window_seconds=60)

        assert limiter.is_allowed()  # 1
        assert limiter.is_allowed()  # 2
        assert not limiter.is_allowed()  # 3 (차단)

    def test_rate_limiter_resets_after_window(self):
        """시간 윈도우 후 초기화 테스트."""
        limiter = RateLimiter(max_calls=1, window_seconds=1)

        assert limiter.is_allowed()  # 1
        assert not limiter.is_allowed()  # 2 (차단)

        time.sleep(1.1)

        # 윈도우가 지났으므로 허용
        assert limiter.is_allowed()

    def test_rate_limiter_reset(self):
        """명시적 리셋 테스트."""
        limiter = RateLimiter(max_calls=2)

        assert limiter.is_allowed()
        assert limiter.is_allowed()
        assert not limiter.is_allowed()

        limiter.reset()
        assert len(limiter.calls) == 0
        assert limiter.is_allowed()


class TestResponseCache:
    """ResponseCache 테스트."""

    def test_cache_initialization(self):
        """ResponseCache 초기화 테스트."""
        cache = ResponseCache(ttl_seconds=3600, max_size=256)

        assert cache.ttl_seconds == 3600
        assert cache.max_size == 256
        assert len(cache.cache) == 0

    def test_cache_set_and_get(self):
        """캐시 저장 및 로드 테스트."""
        cache = ResponseCache()

        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_cache_missing_key(self):
        """없는 키 조회 테스트."""
        cache = ResponseCache()

        assert cache.get("nonexistent") is None

    def test_cache_expiration(self):
        """캐시 만료 테스트."""
        cache = ResponseCache(ttl_seconds=1)

        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

        time.sleep(1.1)

        # TTL 초과 후 None 반환
        assert cache.get("key1") is None

    def test_cache_lru_eviction(self):
        """LRU 캐시 제거 테스트."""
        cache = ResponseCache(max_size=2)

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # key1 제거

        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"

    def test_cache_clear(self):
        """캐시 초기화 테스트."""
        cache = ResponseCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")

        assert len(cache.cache) == 2

        cache.clear()

        assert len(cache.cache) == 0

    def test_cache_stats(self):
        """캐시 통계 테스트."""
        cache = ResponseCache(ttl_seconds=3600, max_size=256)

        cache.set("key1", "value1")

        stats = cache.get_stats()

        assert "cached_items" in stats
        assert "max_size" in stats
        assert "ttl_seconds" in stats
        assert stats["cached_items"] == 1
        assert stats["max_size"] == 256
        assert stats["ttl_seconds"] == 3600

    def test_cache_overwrite_existing_key(self):
        """기존 키 덮어쓰기 테스트."""
        cache = ResponseCache()

        cache.set("key1", "value1")
        cache.set("key1", "value2")

        assert cache.get("key1") == "value2"


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic SDK not installed")
class TestLLMFallbackProvider:
    """LLMFallbackProvider 테스트."""

    def test_provider_initialization_without_api_key(self):
        """API 키 없는 초기화 테스트."""
        with patch.dict("os.environ", {}, clear=True):
            provider = LLMFallbackProvider()

            assert not provider.enabled
            assert provider.api_key == ""

    def test_provider_is_available_without_anthropic(self):
        """anthropic 미설치 상태에서의 가용성 테스트."""
        with patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"}):
            with patch("src.llm_fallback.HAS_ANTHROPIC", False):
                provider = LLMFallbackProvider()

                assert not provider.is_available()

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_rate_limiter(self):
        """속도 제한기 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                provider = LLMFallbackProvider()

                # 속도 제한이 초기화됨
                assert provider.rate_limiter is not None
                assert provider.rate_limiter.max_calls == 10

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_response_cache(self):
        """응답 캐시 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                provider = LLMFallbackProvider()

                assert provider.response_cache is not None

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_system_prompt_loading(self):
        """시스템 프롬프트 로딩 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                with patch("src.llm_fallback.load_text") as mock_load:
                    mock_load.return_value = "Custom system prompt"

                    provider = LLMFallbackProvider()

                    assert provider.system_prompt == "Custom system prompt"

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_system_prompt_fallback(self):
        """시스템 프롬프트 로딩 실패 시 폴백 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                with patch("src.llm_fallback.load_text") as mock_load:
                    mock_load.side_effect = FileNotFoundError()

                    provider = LLMFallbackProvider()

                    assert "보세전시장" in provider.system_prompt

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_generate_response_no_rate_limit(self):
        """속도 제한 미초과 시 응답 생성 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                # Mock 클라이언트
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                # Mock 응답
                mock_message = MagicMock()
                mock_message.content = [MagicMock(text="Test response")]
                mock_client.messages.create.return_value = mock_message

                provider = LLMFallbackProvider()
                response = provider.generate_response("Test query")

                assert response == "Test response"

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_generate_response_with_rate_limit(self):
        """속도 제한 초과 시 None 반환 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                provider = LLMFallbackProvider()
                provider.rate_limiter.max_calls = 1

                # 첫 호출: 성공
                provider.generate_response("Query 1")

                # 두 번째 호출: 속도 제한으로 None
                response = provider.generate_response("Query 2")
                assert response is None

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_response_caching(self):
        """응답 캐싱 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                mock_message = MagicMock()
                mock_message.content = [MagicMock(text="Cached response")]
                mock_client.messages.create.return_value = mock_message

                provider = LLMFallbackProvider()

                # 첫 호출: API 호출
                response1 = provider.generate_response("Query", use_cache=True)

                # 두 번째 호출: 캐시에서 로드
                response2 = provider.generate_response("Query", use_cache=True)

                # API는 한 번만 호출됨
                assert mock_client.messages.create.call_count == 1
                assert response1 == response2

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_api_error_handling(self):
        """API 오류 처리 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                import anthropic

                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                # API 오류 발생
                mock_client.messages.create.side_effect = anthropic.APIError(
                    "API Error", request=None, response=None
                )

                provider = LLMFallbackProvider()
                response = provider.generate_response("Query")

                assert response is None

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_disclaimer_generation(self):
        """면책 문구 생성 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                mock_message = MagicMock()
                mock_message.content = [MagicMock(text="Response")]
                mock_client.messages.create.return_value = mock_message

                provider = LLMFallbackProvider()
                response = provider.generate_response_with_disclaimer("Query")

                assert "Response" in response
                assert "안내:" in response
                assert "면책" in response or "AI가 생성한" in response

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_fallback_message_when_unavailable(self):
        """LLM 불가 시 폴백 메시지 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                provider = LLMFallbackProvider()

                # 속도 제한 설정
                provider.rate_limiter.max_calls = 0

                response = provider.generate_response_with_disclaimer("Query")

                assert "현재 AI 응답 서비스를 이용할 수 없습니다" in response

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_stats(self):
        """통계 정보 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.return_value = mock_client

                provider = LLMFallbackProvider()

                stats = provider.get_stats()

                assert "enabled" in stats
                assert "rate_limiter" in stats
                assert "cache" in stats

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_get_llm_provider_singleton(self):
        """싱글톤 패턴 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                provider1 = get_llm_provider()
                provider2 = get_llm_provider()

                assert provider1 is provider2

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_build_context_prompt(self):
        """컨텍스트 프롬프트 빌드 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                provider = LLMFallbackProvider()

                faq_matches = [
                    {
                        "item": {
                            "question": "FAQ 질문?",
                            "answer": "FAQ 답변.",
                            "legal_basis": ["관세법 제100조"]
                        },
                        "score": 0.85
                    }
                ]

                prompt = provider._build_context_prompt("사용자 질문", faq_matches)

                assert "사용자 질문" in prompt
                assert "FAQ 질문?" in prompt
                assert "관세법 제100조" in prompt

    @patch.dict("os.environ", {"CHATBOT_LLM_API_KEY": "test-key"})
    def test_provider_clear_cache(self):
        """캐시 초기화 테스트."""
        with patch("src.llm_fallback.HAS_ANTHROPIC", True):
            with patch("src.llm_fallback.anthropic.Anthropic"):
                provider = LLMFallbackProvider()

                provider.response_cache.set("key1", "value1")
                assert len(provider.response_cache.cache) > 0

                provider.clear_cache()
                assert len(provider.response_cache.cache) == 0

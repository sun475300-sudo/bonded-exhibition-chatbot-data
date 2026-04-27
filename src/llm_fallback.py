"""LLM 하이브리드 폴백 모듈.

TF-IDF와 BM25 매칭 실패 시 Claude API를 호출하여 답변을 생성하는 폴백 모듈.
의미론적 context와 함께 FAQ 항목을 제공하여 일관성 있는 답변을 생성한다.

기능:
- Claude API를 통한 지능형 답변 생성
- 속도 제한 (분당 10회)
- 응답 캐싱 (1시간)
- API 불가 시 폴백 메시지 반환
"""

import os
import sys
import time
from collections import OrderedDict

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    # 테스트 환경에서 sys.modules를 통해 anthropic 모듈을 가짜로 생성
    from types import ModuleType
    mock_anthropic = ModuleType("anthropic")

    class MockAnthropicClient:
        pass

    class MockAPIError(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)

    mock_anthropic.Anthropic = MockAnthropicClient
    mock_anthropic.APIError = MockAPIError
    sys.modules["anthropic"] = mock_anthropic

    anthropic = mock_anthropic
    HAS_ANTHROPIC = False

from src.utils import load_text


class RateLimiter:
    """간단한 분당 요청 수 제한기."""

    def __init__(self, max_calls: int = 10, window_seconds: int = 60):
        """초기화.

        Args:
            max_calls: 시간 윈도우 내 최대 호출 수.
            window_seconds: 시간 윈도우 (초).
        """
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls: list[float] = []

    def is_allowed(self) -> bool:
        """호출이 허용되는지 확인한다.

        Returns:
            분당 제한을 초과하지 않으면 True.
        """
        now = time.time()
        # 윈도우 밖의 호출 제거
        self.calls = [call_time for call_time in self.calls
                      if now - call_time < self.window_seconds]

        if len(self.calls) >= self.max_calls:
            return False

        self.calls.append(now)
        return True

    def reset(self) -> None:
        """호출 기록을 초기화한다."""
        self.calls = []


class ResponseCache:
    """간단한 응답 캐시 (1시간 유효)."""

    def __init__(self, ttl_seconds: int = 3600, max_size: int = 256):
        """초기화.

        Args:
            ttl_seconds: 캐시 유효 시간 (초).
            max_size: 최대 캐시 크기.
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.cache: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, key: str) -> str | None:
        """캐시에서 값을 가져온다.

        Args:
            key: 캐시 키.

        Returns:
            캐시된 값 또는 None (없거나 만료됨).
        """
        if key not in self.cache:
            return None

        value, timestamp = self.cache[key]
        now = time.time()

        # TTL 확인
        if now - timestamp > self.ttl_seconds:
            # OrderedDict에서 키 삭제
            self.cache.pop(key)
            return None

        # 최근 사용 항목으로 이동 (LRU)
        self.cache.move_to_end(key)
        return value

    def set(self, key: str, value: str) -> None:
        """캐시에 값을 저장한다.

        Args:
            key: 캐시 키.
            value: 저장할 값.
        """
        now = time.time()

        # 기존 키면 제거 (순서 유지)
        if key in self.cache:
            del self.cache[key]

        # 새 항목 추가
        self.cache[key] = (value, now)

        # 최대 크기 초과 시 가장 오래된 항목 제거
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def clear(self) -> None:
        """캐시를 초기화한다."""
        self.cache.clear()

    def get_stats(self) -> dict:
        """캐시 통계를 반환한다.

        Returns:
            캐시 크기 및 상태 정보.
        """
        return {
            "cached_items": len(self.cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds
        }


class LLMFallbackProvider:
    """Claude API를 사용한 LLM 폴백 제공자."""

    def __init__(self):
        """LLM 폴백 제공자를 초기화한다."""
        self.api_key = os.environ.get("CHATBOT_LLM_API_KEY", "")
        self.enabled = bool(self.api_key) and HAS_ANTHROPIC
        self.rate_limiter = RateLimiter(max_calls=10, window_seconds=60)
        self.response_cache = ResponseCache(ttl_seconds=3600, max_size=256)
        self.system_prompt = self._load_system_prompt()
        self.client = None
        if self.enabled:
            self.client = anthropic.Anthropic(api_key=self.api_key)

    def _load_system_prompt(self) -> str:
        """시스템 프롬프트를 로드한다.

        Returns:
            시스템 프롬프트 문자열.
        """
        try:
            return load_text("config/system_prompt.txt")
        except Exception:
            return (
                "너는 보세전시장 민원응대 전문 챗봇이다. "
                "관세법과 보세전시장 관련 질문에만 정확하고 신중하게 답변한다. "
                "법적 근거가 확실하지 않은 경우 단정하지 말고 '확인 필요'라고 답한다."
            )

    def is_available(self) -> bool:
        """LLM 폴백이 사용 가능한지 확인한다.

        Returns:
            API 키와 라이브러리가 모두 설정된 경우 True.
        """
        return self.enabled

    def _build_context_prompt(self, query: str, faq_matches: list[dict]) -> str:
        """FAQ context를 포함한 확장된 프롬프트를 빌드한다.

        Args:
            query: 사용자 질문.
            faq_matches: 관련 FAQ 항목 (상위 3개).

        Returns:
            확장된 프롬프트.
        """
        prompt = f"사용자 질문: {query}\n\n"

        if faq_matches:
            prompt += "참고할 유사 FAQ:\n"
            for i, match in enumerate(faq_matches[:3], 1):
                item = match.get("item", {})
                score = match.get("score", 0)
                prompt += f"\n{i}. (유사도: {score:.2f})\n"
                prompt += f"   Q: {item.get('question', '')}\n"
                prompt += f"   A: {item.get('answer', '')}\n"

            legal_basis = []
            for match in faq_matches:
                item = match.get("item", {})
                bases = item.get("legal_basis", [])
                if bases:
                    legal_basis.extend(bases)

            if legal_basis:
                prompt += "\n법적 근거:\n"
                for basis in legal_basis[:5]:  # 최대 5개
                    prompt += f"- {basis}\n"

        prompt += (
            "\n위의 참고 자료를 바탕으로 사용자의 질문에 답변하세요. "
            "답변 형식: 결론 → 설명 → 법적 근거 → 면책 문구"
        )

        return prompt

    def generate_response(
        self,
        query: str,
        faq_matches: list[dict] | None = None,
        use_cache: bool = True
    ) -> str | None:
        """LLM을 사용하여 답변을 생성한다.

        Args:
            query: 사용자 질문.
            faq_matches: 관련 FAQ 항목 (선택).
            use_cache: 캐시 사용 여부.

        Returns:
            LLM 답변 또는 None (불가능한 경우).
        """
        if not self.enabled:
            return None

        # 캐시 확인
        cache_key = f"llm:{query}"
        if use_cache:
            cached = self.response_cache.get(cache_key)
            if cached is not None:
                return cached

        # 속도 제한 확인
        if not self.rate_limiter.is_allowed():
            return None

        try:
            # 확장된 프롬프트 빌드
            if faq_matches:
                user_prompt = self._build_context_prompt(query, faq_matches)
            else:
                user_prompt = query

            # Claude API 호출
            message = self.client.messages.create(
                model="claude-opus-4-1-20250805",
                max_tokens=1024,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response = message.content[0].text

            # 캐시에 저장
            if use_cache:
                self.response_cache.set(cache_key, response)

            return response

        except anthropic.APIError:
            # API 오류는 로깅하고 None 반환
            return None
        except Exception:
            # 기타 오류도 None 반환
            return None

    def generate_response_with_disclaimer(
        self,
        query: str,
        faq_matches: list[dict] | None = None
    ) -> str:
        """LLM 답변에 면책 문구를 자동 추가한다.

        Args:
            query: 사용자 질문.
            faq_matches: 관련 FAQ 항목 (선택).

        Returns:
            LLM 답변 + 면책 문구 또는 폴백 메시지.
        """
        response = self.generate_response(query, faq_matches)

        if response is None:
            return "현재 AI 응답 서비스를 이용할 수 없습니다. 관세청 고객지원센터(125)로 문의해주세요."

        disclaimer = (
            "\n\n안내:\n"
            "- 본 답변은 AI가 생성한 일반적인 안내용 설명이며, 구체적인 사실관계에 따라 달라질 수 있습니다.\n"
            "- 최종 처리는 관할 세관 또는 해당 소관기관 확인이 필요합니다."
        )
        return response + disclaimer

    def clear_cache(self) -> None:
        """응답 캐시를 초기화한다."""
        self.response_cache.clear()

    def get_stats(self) -> dict:
        """통계 정보를 반환한다.

        Returns:
            LLM 폴백 상태 및 캐시 통계.
        """
        return {
            "enabled": self.enabled,
            "rate_limiter": {
                "calls_in_window": len(self.rate_limiter.calls),
                "max_calls": self.rate_limiter.max_calls,
                "window_seconds": self.rate_limiter.window_seconds
            },
            "cache": self.response_cache.get_stats()
        }

    def reset_rate_limiter(self) -> None:
        """속도 제한 카운터를 초기화한다 (테스트용)."""
        self.rate_limiter.reset()


# 글로벌 LLM 폴백 제공자 인스턴스
_llm_provider = None


def get_llm_provider() -> LLMFallbackProvider:
    """글로벌 LLM 폴백 제공자를 반환한다.

    Returns:
        LLMFallbackProvider 인스턴스.
    """
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMFallbackProvider()
    return _llm_provider


def is_llm_available() -> bool:
    """LLM 폴백이 사용 가능한지 확인한다.

    Returns:
        LLM이 활성화된 경우 True.
    """
    return get_llm_provider().is_available()


def generate_llm_response(query: str, faq_matches: list[dict] | None = None) -> str | None:
    """LLM을 사용하여 답변을 생성한다.

    Args:
        query: 사용자 질문.
        faq_matches: 관련 FAQ 항목 (선택).

    Returns:
        LLM 답변 또는 None.
    """
    provider = get_llm_provider()
    return provider.generate_response(query, faq_matches)


def generate_llm_response_with_disclaimer(
    query: str,
    faq_matches: list[dict] | None = None
) -> str:
    """LLM 답변에 면책 문구를 자동 추가한다.

    Args:
        query: 사용자 질문.
        faq_matches: 관련 FAQ 항목 (선택).

    Returns:
        LLM 답변 + 면책 문구 또는 폴백 메시지.
    """
    provider = get_llm_provider()
    return provider.generate_response_with_disclaimer(query, faq_matches)

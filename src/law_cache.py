"""국가법령정보센터 캐시 브리지.

`LawSyncManager`가 SQLite에 적재한 최신 조문 내용을 보세봇 응답에
실시간으로 노출하기 위한 얇은 어댑터 계층이다.

핵심 책임:
1. FAQ 항목의 ``legal_basis`` 문자열(예: "관세법 제190조") 또는
   ``legal_references.json``에 등록된 (law_name, article) 쌍을
   캐시 키로 변환한다.
2. 캐시에서 최신 본문을 조회해 ``content``, ``fetched_at``, ``age_days``
   메타데이터를 함께 반환한다.
3. 챗봇이 응답을 만들 때마다 매번 SQLite I/O를 일으키지 않도록
   인메모리 LRU 캐시를 둔다(요청 빈도 ≪ 동기화 빈도).

이 모듈은 의도적으로 ``LawSyncManager``를 상속하지 않고 합성한다.
SyncManager 자체가 외부 API I/O 책임을 가지므로, 응답 생성 경로에서는
"이미 적재된 캐시만" 읽는 좁은 인터페이스가 안전하다.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


# "관세법 제190조", "관세법 시행령 제101조(판매용품의 면허전 사용금지)",
# "보세전시장 운영에 관한 고시 제10조" 등에서 (법령명, 조문번호)를 분리.
_BASIS_PATTERN = re.compile(
    r"^(?P<law>[^()]+?)\s+(?P<article>제\s*\d+\s*조(?:의\s*\d+)?)"
)


def parse_legal_basis(citation: str) -> tuple[str, str] | None:
    """``"관세법 제190조"`` 같은 인용 문자열에서 (법령명, 조문번호)를 추출한다.

    매칭 실패 시 None을 반환한다. 호출자는 이 경우 캐시 조회를 건너뛴다.
    """
    if not citation:
        return None
    text = citation.strip()
    # "관세법 제190조(보세전시장)" 같은 부가 설명 괄호는 잘라낸다.
    paren = text.find("(")
    if paren > 0:
        text = text[:paren].strip()
    m = _BASIS_PATTERN.match(text)
    if not m:
        return None
    law = m.group("law").strip()
    article = m.group("article").replace(" ", "")
    return (law, article)


class LawCacheBridge:
    """`LawSyncManager`의 캐시를 응답 빌더가 쉽게 사용할 수 있게 감싼다."""

    def __init__(self, sync_manager: Any | None = None):
        # 지연 import: 챗봇 init 경로에서 sqlite/file I/O가 즉시 발생하지 않도록.
        if sync_manager is None:
            from src.law_api_sync import LawSyncManager

            sync_manager = LawSyncManager()
        self._sync_manager = sync_manager
        self._cache_lock = threading.Lock()
        self._memo: dict[tuple[str, str], dict[str, Any] | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_cached(self, law_name: str, article: str) -> dict[str, Any] | None:
        """(법령명, 조문번호)로 캐시된 조문 정보를 반환한다.

        Returns:
            ``{"content", "content_hash", "fetched_at", "age_days"}`` 또는 ``None``.
        """
        if not law_name or not article:
            return None
        key = (law_name, article)
        with self._cache_lock:
            if key in self._memo:
                return self._memo[key]

        try:
            cached = self._sync_manager.get_cached_content(law_name, article)
        except Exception as e:  # pragma: no cover - 방어적
            logger.warning("law cache read failed for %s %s: %s", law_name, article, e)
            cached = None

        if cached:
            cached = dict(cached)
            cached["age_days"] = self._compute_age_days(cached.get("fetched_at"))

        with self._cache_lock:
            self._memo[key] = cached
        return cached

    def get_for_basis(self, citation: str) -> dict[str, Any] | None:
        """FAQ legal_basis 문자열을 받아 캐시된 조문을 반환한다."""
        parsed = parse_legal_basis(citation)
        if not parsed:
            return None
        law, article = parsed
        return self.get_cached(law, article)

    def build_legal_guide_entries(self, legal_basis: list[str]) -> list[str]:
        """legal_basis 리스트에서 캐시 본문을 사용해 가이드 문장을 생성한다.

        캐시 미스인 항목은 결과에 포함되지 않는다(빈 항목 노이즈 방지).
        """
        entries: list[str] = []
        for basis in legal_basis or []:
            cached = self.get_for_basis(basis)
            if not cached or not cached.get("content"):
                continue
            content = (cached.get("content") or "").strip()
            if not content:
                continue
            preview = content[:160].replace("\n", " ").strip()
            if len(content) > 160:
                preview = preview.rstrip() + "…"
            fetched = cached.get("fetched_at", "")
            date_part = fetched[:10] if fetched else "최근 동기화"
            entries.append(f"{basis} ({date_part} 동기화 기준): {preview}")
        return entries

    def freshness_summary(self, legal_basis: list[str]) -> dict[str, Any] | None:
        """legal_basis 항목들 중 가장 최근 동기화 시각을 요약한다."""
        latest_iso: str | None = None
        max_age: int | None = None
        synced_count = 0
        for basis in legal_basis or []:
            cached = self.get_for_basis(basis)
            if not cached:
                continue
            synced_count += 1
            fetched = cached.get("fetched_at")
            if fetched and (latest_iso is None or fetched > latest_iso):
                latest_iso = fetched
            age = cached.get("age_days")
            if isinstance(age, int) and (max_age is None or age < max_age):
                max_age = age
        if not synced_count or latest_iso is None:
            return None
        return {
            "last_synced": latest_iso,
            "min_age_days": max_age,
            "synced_count": synced_count,
        }

    def invalidate(self) -> None:
        """동기화 직후 호출해 인메모리 메모를 비운다."""
        with self._cache_lock:
            self._memo.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _compute_age_days(fetched_at: str | None) -> int | None:
        if not fetched_at:
            return None
        try:
            ts = datetime.fromisoformat(fetched_at)
        except ValueError:
            return None
        delta = datetime.now() - ts
        return max(delta.days, 0)


# 싱글톤은 챗봇 init 시 1회 생성하여 재사용한다.
_BRIDGE: LawCacheBridge | None = None
_BRIDGE_LOCK = threading.Lock()


def get_law_cache_bridge() -> LawCacheBridge:
    """전역 LawCacheBridge 싱글톤을 반환한다."""
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = LawCacheBridge()
    return _BRIDGE


@lru_cache(maxsize=1)
def _bridge_for_module() -> LawCacheBridge:
    return get_law_cache_bridge()


__all__ = [
    "LawCacheBridge",
    "get_law_cache_bridge",
    "parse_legal_basis",
]

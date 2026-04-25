"""법령 근거 제공자 (LegalReferenceProvider).

`data/legal_references.json` 의 최신 내용을 보유하면서 파일 수정 시점을 감지해
챗봇이 매 요청마다 최신화된 요약을 사용할 수 있게 한다. 추가로 국가법령정보센터
Open API 동기화 캐시(`law_sync.db`)에 더 최근 내용이 있으면 그 값을 우선 사용한다.

설계 의도:
- 챗봇 기동 시 한 번 읽고 끝나는 기존 흐름을 유지한 채, 외부 데이터 변경을
  자동 반영할 수 있도록 hot-reload 인터페이스를 제공한다.
- 무거운 의존성(예: 외부 호출)을 도입하지 않고 mtime + sqlite cache만 사용한다.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional

from src.utils import load_json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LEGAL_REF_PATH = os.path.join(BASE_DIR, "data", "legal_references.json")


def _normalize_article(article: str) -> str:
    """조문 표기를 비교용 정규형(`제190조` → `190`)으로 변환한다."""
    if not article:
        return ""
    digits = re.sub(r"[^0-9]", "", article)
    return digits


def _parse_basis(basis: str) -> Dict[str, str]:
    """`관세법 제190조` 같은 문자열을 (law_name, article)로 분해한다.

    인식 못 할 경우 law_name=원본, article="" 반환.
    """
    if not basis:
        return {"law_name": "", "article": ""}
    text = basis.strip()
    # "관세법 시행령 제101조", "관세법 제190조 제2항" 등 패턴
    match = re.search(r"제\s*\d+\s*조(?:의\s*\d+)?", text)
    if match:
        article = match.group(0).replace(" ", "")
        law_name = text[: match.start()].strip()
        return {"law_name": law_name, "article": article}
    return {"law_name": text, "article": ""}


class LegalReferenceProvider:
    """법령 근거 정보를 제공하고 외부 변경을 자동으로 반영한다."""

    def __init__(
        self,
        legal_ref_path: Optional[str] = None,
        sync_manager: Optional[Any] = None,
    ):
        self.legal_ref_path = legal_ref_path or DEFAULT_LEGAL_REF_PATH
        self.sync_manager = sync_manager
        self._lock = threading.RLock()
        self._mtime: Optional[float] = None
        self._references: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_count = 0
        self.refresh(force=True)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def refresh(self, force: bool = False) -> bool:
        """파일이 변경됐으면 다시 읽는다.

        Returns:
            True if a reload happened, False otherwise.
        """
        with self._lock:
            try:
                mtime = os.path.getmtime(self.legal_ref_path)
            except OSError:
                if force:
                    self._references = []
                    self._index = {}
                    self._mtime = None
                    return True
                return False

            if not force and self._mtime is not None and mtime <= self._mtime:
                return False

            try:
                data = load_json(self.legal_ref_path)
            except Exception:
                if force:
                    self._references = []
                    self._index = {}
                    return True
                return False

            refs = data.get("references", []) if isinstance(data, dict) else []
            self._references = list(refs)
            self._index = {}
            for ref in refs:
                key = self._make_key(ref.get("law_name", ""), ref.get("article", ""))
                if key:
                    self._index[key] = ref
            self._mtime = mtime
            self._last_refresh_count = self._last_refresh_count + 1
            return True

    def refresh_if_stale(self) -> bool:
        """파일이 변경됐을 때만 다시 읽는다 (요청 핸들러용)."""
        return self.refresh(force=False)

    def list_references(self) -> List[Dict[str, Any]]:
        """현재 적재된 법령 근거 전체를 반환한다 (얕은 복사)."""
        with self._lock:
            return [dict(r) for r in self._references]

    def get_reference(self, law_name: str, article: str) -> Optional[Dict[str, Any]]:
        """법령명+조문으로 법령 근거를 찾는다. DB 캐시가 더 최신이면 덮어쓴다."""
        with self._lock:
            ref = self._index.get(self._make_key(law_name, article))
            if ref is None:
                return None
            ref = dict(ref)

        cached = self._cached_summary(law_name, article)
        if cached:
            ref = dict(ref)
            ref["summary"] = cached["summary"]
            ref["last_synced"] = cached["fetched_at"]
            ref["source"] = "law_api_sync"
        else:
            ref.setdefault("source", "legal_references.json")
        return ref

    def get_summary(self, law_name: str, article: str) -> Optional[str]:
        """현재 시점의 최신 요약을 반환한다 (없으면 None)."""
        ref = self.get_reference(law_name, article)
        if ref:
            return ref.get("summary") or None
        # 인덱스에 없으면 DB 캐시만이라도 조회
        cached = self._cached_summary(law_name, article)
        if cached:
            return cached["summary"]
        return None

    def enrich_legal_basis(self, legal_basis: Iterable[str]) -> List[Dict[str, Any]]:
        """`["관세법 제190조", ...]` 형태의 리스트를 enriched dict 목록으로 변환한다.

        반환되는 각 항목은 `{citation, law_name, article, title, summary, url, source}`.
        """
        self.refresh_if_stale()
        result: List[Dict[str, Any]] = []
        for basis in legal_basis or []:
            parts = _parse_basis(basis)
            ref = self.get_reference(parts["law_name"], parts["article"]) or {}
            result.append({
                "citation": basis,
                "law_name": ref.get("law_name") or parts["law_name"],
                "article": ref.get("article") or parts["article"],
                "title": ref.get("title", ""),
                "summary": ref.get("summary", ""),
                "url": ref.get("url", ""),
                "source": ref.get("source", "legal_references.json"),
                "last_synced": ref.get("last_synced"),
            })
        return result

    def build_legal_guide(self, legal_basis: Iterable[str]) -> List[str]:
        """`response_builder.legal_guide` 에 쓰일 한 줄짜리 가이드 문자열 목록을 만든다.

        형식: `관세법 제190조 - 보세전시장: 박람회 등에서 외국물품을 ...`
        """
        guide: List[str] = []
        for entry in self.enrich_legal_basis(legal_basis):
            citation = entry.get("citation") or f"{entry['law_name']} {entry['article']}".strip()
            title = entry.get("title")
            summary = entry.get("summary")
            head = citation
            if title:
                head = f"{citation} ({title})"
            if summary:
                guide.append(f"{head}: {summary}")
            elif title:
                guide.append(head)
        return guide

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(law_name: str, article: str) -> str:
        if not law_name and not article:
            return ""
        return f"{(law_name or '').strip()}|{_normalize_article(article)}"

    def _cached_summary(self, law_name: str, article: str) -> Optional[Dict[str, str]]:
        if not self.sync_manager or not law_name or not article:
            return None
        try:
            cached = self.sync_manager.get_cached_content(law_name, article)
        except Exception:
            return None
        if not cached:
            return None
        content = (cached.get("content") or "").strip()
        if not content:
            return None
        # 너무 긴 본문은 200자로 컷 (legal_references.json summary와 동일 컨벤션)
        return {
            "summary": content[:200].strip(),
            "fetched_at": cached.get("fetched_at", ""),
        }


# ---------------------------------------------------------------------------
# Module-level singleton helpers (lazy)
# ---------------------------------------------------------------------------

_SINGLETON_LOCK = threading.Lock()
_SINGLETON: Optional[LegalReferenceProvider] = None


def get_legal_reference_provider() -> LegalReferenceProvider:
    """프로세스 단위 싱글턴 LegalReferenceProvider를 반환한다."""
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            sync_manager = None
            try:
                from src.law_api_sync import LawSyncManager
                sync_manager = LawSyncManager()
            except Exception:
                sync_manager = None
            _SINGLETON = LegalReferenceProvider(sync_manager=sync_manager)
    return _SINGLETON


def reset_legal_reference_provider() -> None:
    """싱글턴을 리셋한다 (테스트 전용)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None

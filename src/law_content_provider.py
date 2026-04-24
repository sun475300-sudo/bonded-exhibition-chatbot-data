"""국가법령정보센터 최신 법령 조문 공급자.

law_api_sync 모듈이 수집해 캐시한 최신 법령 본문을, 챗봇이 답변을
조립할 때 실시간으로 가져와 `legal_guide`에 반영할 수 있도록 한다.

동작 방식:
- FAQ 항목의 `legal_basis`(예: "관세법 제190조(보세전시장)")를 파싱해
  (법령명, 조문번호)로 분해한다.
- law_sync.db의 `law_content_cache`에 해당 조문이 있으면 요약 텍스트를
  반환한다. 없으면 정적 `legal_references.json` 요약으로 폴백한다.

이 구조 덕분에 국가법령정보센터에서 법령이 개정돼 `law_api_sync --sync`가
새 본문을 캐시하면, 챗봇은 재시작 없이(또는 reload 후) 최신 본문을 답변에
반영하게 된다.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from typing import Iterable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LAW_SYNC_DB = os.path.join(BASE_DIR, "data", "law_sync.db")

# "관세법 제190조(보세전시장)", "관세법 시행령 제101조" 등을 파싱한다.
# 법령명에 공백이 포함될 수 있으므로 (?P<law>.+?) 로 비탐욕 매칭한다.
_BASIS_PATTERN = re.compile(
    r"^(?P<law>.+?)\s+(?P<article>제\s*\d+(?:조의?\d+)?조)(?:\([^)]*\))?$"
)

DEFAULT_PREVIEW_LEN = 220


def parse_legal_basis(basis: str) -> tuple[str, str] | None:
    """`legal_basis` 문자열에서 법령명과 조문번호를 분리한다.

    Args:
        basis: 예) "관세법 제190조", "관세법 시행령 제101조(판매용품의 면허전 사용금지)"

    Returns:
        (법령명, 조문번호) 튜플. 파싱 실패 시 None.
    """
    if not basis:
        return None
    stripped = basis.strip()
    m = _BASIS_PATTERN.match(stripped)
    if not m:
        return None
    law = m.group("law").strip()
    article = m.group("article").replace(" ", "").strip()
    return (law, article)


class LawContentProvider:
    """law_sync.db 캐시 기반 실시간 법령 본문 공급자."""

    def __init__(
        self,
        db_path: str | None = None,
        static_refs: Iterable[dict] | None = None,
        preview_len: int = DEFAULT_PREVIEW_LEN,
    ):
        """Args:
            db_path: law_sync.db 경로.
            static_refs: legal_references.json 내 references 리스트.
                 캐시 미스 시 요약 폴백에 사용된다.
            preview_len: 응답에 포함할 본문 미리보기 길이(문자 수).
        """
        self.db_path = db_path or DEFAULT_LAW_SYNC_DB
        self.preview_len = preview_len
        self._static_by_key: dict[tuple[str, str], dict] = {}
        if static_refs:
            for ref in static_refs:
                key = (ref.get("law_name", ""), ref.get("article", ""))
                if key[0] and key[1]:
                    self._static_by_key[key] = ref

    # ------------------------------------------------------------------
    # 조회 API
    # ------------------------------------------------------------------
    def get_latest(self, law_name: str, article: str) -> dict | None:
        """캐시된 최신 조문 텍스트와 폴백 요약을 반환한다.

        Returns:
            {
                "law_name": str,
                "article": str,
                "content": str,      # 캐시 본문 또는 정적 요약
                "source": "live" | "static" | "none",
                "fetched_at": str | None,
                "preview": str,      # 요약 미리보기
            }
            캐시와 정적 정보 모두 없으면 None.
        """
        live = self._fetch_cached(law_name, article)
        if live and live.get("content"):
            preview = self._make_preview(live["content"])
            return {
                "law_name": law_name,
                "article": article,
                "content": live["content"],
                "source": "live",
                "fetched_at": live.get("fetched_at"),
                "preview": preview,
            }

        static = self._static_by_key.get((law_name, article))
        if static and static.get("summary"):
            return {
                "law_name": law_name,
                "article": article,
                "content": static["summary"],
                "source": "static",
                "fetched_at": static.get("last_synced"),
                "preview": static["summary"],
            }
        return None

    def get_for_basis(self, basis: str) -> dict | None:
        """`legal_basis` 문자열로부터 바로 조회한다."""
        parsed = parse_legal_basis(basis)
        if not parsed:
            return None
        return self.get_latest(parsed[0], parsed[1])

    def build_legal_guide(self, legal_basis: Iterable[str]) -> list[str]:
        """`legal_basis` 리스트를 받아 챗봇 legal_guide 포맷 문자열 리스트를 만든다.

        각 항목은 "관세법 제190조: <미리보기> (출처: 국가법령정보센터 · YYYY-MM-DD)"
        형태로, 출처가 법령 DB일 때만 날짜/출처 표기를 남긴다.
        """
        guide: list[str] = []
        for basis in legal_basis:
            entry = self.get_for_basis(basis)
            if not entry:
                continue
            preview = entry.get("preview") or ""
            if not preview:
                continue
            source = entry.get("source")
            fetched_at = entry.get("fetched_at")
            tag = ""
            if source == "live" and fetched_at:
                date = fetched_at[:10]
                tag = f" (출처: 국가법령정보센터 · {date} 최신 반영)"
            elif source == "static":
                tag = " (출처: 내부 요약)"
            guide.append(f"{basis}: {preview}{tag}")
        return guide

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _fetch_cached(self, law_name: str, article: str) -> dict | None:
        if not os.path.exists(self.db_path):
            return None
        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error:
            return None
        try:
            try:
                cursor = conn.execute(
                    "SELECT content, content_hash, fetched_at "
                    "FROM law_content_cache WHERE law_name = ? AND article = ?",
                    (law_name, article),
                )
            except sqlite3.OperationalError:
                # 테이블이 아직 없으면 조용히 무시한다.
                return None
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "content": row[0],
                "content_hash": row[1],
                "fetched_at": row[2],
            }
        finally:
            conn.close()

    def _make_preview(self, content: str) -> str:
        text = (content or "").strip().replace("\n", " ")
        # 연속 공백을 하나로 줄여 미리보기를 자연스럽게 만든다.
        text = re.sub(r"\s+", " ", text)
        if len(text) <= self.preview_len:
            return text
        return text[: self.preview_len].rstrip() + "…"


__all__ = [
    "LawContentProvider",
    "parse_legal_basis",
    "DEFAULT_LAW_SYNC_DB",
    "DEFAULT_PREVIEW_LEN",
]


if __name__ == "__main__":  # pragma: no cover
    provider = LawContentProvider()
    sample_basis = [
        "관세법 제190조",
        "관세법 시행령 제101조(판매용품의 면허전 사용금지)",
        "보세전시장 운영에 관한 고시 제10조(반출입의 신고)",
    ]
    print("=== Live lookup at", datetime.now().isoformat(), "===")
    for basis in sample_basis:
        parsed = parse_legal_basis(basis)
        entry = provider.get_for_basis(basis)
        print(f"basis={basis!r} parsed={parsed} entry_source={entry and entry.get('source')!r}")

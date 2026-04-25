"""국가법령정보센터 캐시 브리지 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.law_api_sync import LawAPIClient, LawSyncManager
from src.law_cache import LawCacheBridge, parse_legal_basis


@pytest.fixture
def sync_manager(tmp_path):
    """SQLite 캐시만 사용하는 LawSyncManager 인스턴스."""
    client = LawAPIClient(oc="")
    return LawSyncManager(api_client=client, db_path=str(tmp_path / "sync.db"))


@pytest.fixture
def bridge(sync_manager):
    return LawCacheBridge(sync_manager=sync_manager)


class TestParseLegalBasis:
    def test_simple(self):
        assert parse_legal_basis("관세법 제190조") == ("관세법", "제190조")

    def test_with_subtitle(self):
        # 괄호로 감싼 부가 설명은 제거
        assert parse_legal_basis(
            "관세법 시행령 제101조(판매용품의 면허전 사용금지)"
        ) == ("관세법 시행령", "제101조")

    def test_jo_ui(self):
        assert parse_legal_basis("관세법 제190조의2") == ("관세법", "제190조의2")

    def test_empty(self):
        assert parse_legal_basis("") is None
        assert parse_legal_basis(None) is None  # type: ignore[arg-type]

    def test_unparseable(self):
        # 조문 번호가 없는 인용은 None
        assert parse_legal_basis("관세법") is None
        assert parse_legal_basis("관세청 고시 제2026-15호") is None


class TestLawCacheBridge:
    def test_get_cached_returns_none_when_empty(self, bridge):
        assert bridge.get_cached("관세법", "제190조") is None

    def test_get_cached_after_record(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "보세전시장은 박람회 등의 운영을 위해...")
        cached = bridge.get_cached("관세법", "제190조")
        assert cached is not None
        assert "보세전시장" in cached["content"]
        assert cached["age_days"] == 0

    def test_age_days_for_old_entry(self, sync_manager, bridge, monkeypatch):
        sync_manager._record_check("관세법", "제161조", "견본품 반출 규정")

        # 캐시에 적재된 시각을 7일 전으로 위조한다.
        old = (datetime.now() - timedelta(days=7)).isoformat()
        import sqlite3

        conn = sqlite3.connect(sync_manager.db_path)
        try:
            conn.execute(
                "UPDATE law_content_cache SET fetched_at = ? WHERE law_name = ? AND article = ?",
                (old, "관세법", "제161조"),
            )
            conn.commit()
        finally:
            conn.close()

        cached = bridge.get_cached("관세법", "제161조")
        assert cached is not None
        assert cached["age_days"] == 7

    def test_get_for_basis_parses_citation(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "조문 본문")
        result = bridge.get_for_basis("관세법 제190조(보세전시장)")
        assert result is not None
        assert result["content"] == "조문 본문"

    def test_get_for_basis_returns_none_on_miss(self, bridge):
        assert bridge.get_for_basis("관세법 제999조") is None
        assert bridge.get_for_basis("매핑 불가능한 인용") is None

    def test_build_legal_guide_entries(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "보세전시장은 박람회 등의 운영을 위해 외국물품을 장치한다.")
        sync_manager._record_check("관세법", "제161조", "보세구역 외국물품 견본품 반출은 세관장 허가가 필요하다.")
        entries = bridge.build_legal_guide_entries(
            ["관세법 제190조", "관세법 제161조", "관세법 제999조"]
        )
        assert len(entries) == 2
        assert entries[0].startswith("관세법 제190조")
        assert "보세전시장" in entries[0]
        assert entries[1].startswith("관세법 제161조")

    def test_build_legal_guide_entries_truncates_long_content(self, sync_manager, bridge):
        long_text = "보세전시장 " * 200
        sync_manager._record_check("관세법", "제190조", long_text)
        entries = bridge.build_legal_guide_entries(["관세법 제190조"])
        assert len(entries) == 1
        # 길이 제한 + 말줄임표
        assert entries[0].endswith("…")

    def test_build_legal_guide_skips_unparseable(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "본문")
        entries = bridge.build_legal_guide_entries(
            ["관세법", "관세청 고시 제2026-15호", "관세법 제190조"]
        )
        # 캐시 매칭에 성공한 1개만 결과에 포함된다.
        assert len(entries) == 1
        assert entries[0].startswith("관세법 제190조")

    def test_freshness_summary_aggregates(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "본문 A")
        sync_manager._record_check("관세법", "제161조", "본문 B")
        summary = bridge.freshness_summary(
            ["관세법 제190조", "관세법 제161조", "관세법 제999조"]
        )
        assert summary is not None
        assert summary["synced_count"] == 2
        assert summary["min_age_days"] == 0
        assert summary["last_synced"]

    def test_freshness_summary_returns_none_when_no_cache(self, bridge):
        assert bridge.freshness_summary(["관세법 제190조"]) is None

    def test_invalidate_clears_memo(self, sync_manager, bridge):
        sync_manager._record_check("관세법", "제190조", "v1")
        first = bridge.get_cached("관세법", "제190조")
        assert first["content"] == "v1"

        # SQLite를 직접 수정한 뒤 invalidate 없이 재조회 → 메모 캐시 적중
        sync_manager._record_check("관세법", "제190조", "v2")
        bridge.invalidate()
        second = bridge.get_cached("관세법", "제190조")
        assert second["content"] == "v2"


class TestChatbotIntegration:
    """챗봇 응답 경로에 LawCacheBridge가 정상 결합되는지 확인."""

    def test_legal_guide_contains_cache_summary(self, sync_manager, monkeypatch):
        # SyncManager의 캐시에 최신 본문을 적재한다.
        sync_manager._record_check(
            "관세법",
            "제190조",
            "최신 동기화된 보세전시장 정의 본문입니다.",
        )

        # 전역 LawCacheBridge가 우리 sync_manager를 쓰도록 패치한다.
        from src import law_cache as lc

        monkeypatch.setattr(lc, "_BRIDGE", lc.LawCacheBridge(sync_manager=sync_manager))

        from src.chatbot import BondedExhibitionChatbot

        bot = BondedExhibitionChatbot()
        response = bot.process_query("보세전시장이 무엇인가요?")
        # 캐시 본문이 응답의 법령 가이드 섹션에 포함되는지 확인한다.
        assert "최신 동기화된 보세전시장 정의 본문입니다" in response
        assert "동기화 기준" in response

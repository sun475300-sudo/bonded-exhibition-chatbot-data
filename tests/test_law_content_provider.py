"""LawContentProvider 및 실시간 법령 주입 테스트."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.law_content_provider import (
    LawContentProvider,
    parse_legal_basis,
)


# ---------------------------------------------------------------------------
# parse_legal_basis
# ---------------------------------------------------------------------------
class TestParseLegalBasis:
    def test_simple(self):
        assert parse_legal_basis("관세법 제190조") == ("관세법", "제190조")

    def test_with_paren(self):
        assert parse_legal_basis("관세법 시행령 제101조(판매용품의 면허전 사용금지)") == (
            "관세법 시행령",
            "제101조",
        )

    def test_notice(self):
        assert parse_legal_basis("보세전시장 운영에 관한 고시 제10조(반출입의 신고)") == (
            "보세전시장 운영에 관한 고시",
            "제10조",
        )

    def test_jo_eui(self):
        # 제X조의Y조 형태도 허용한다.
        parsed = parse_legal_basis("관세법 제226조의2조(세관장확인)")
        assert parsed is not None
        assert parsed[0] == "관세법"
        assert parsed[1].startswith("제226조의")

    def test_empty(self):
        assert parse_legal_basis("") is None

    def test_garbage(self):
        assert parse_legal_basis("그냥 텍스트") is None


# ---------------------------------------------------------------------------
# LawContentProvider
# ---------------------------------------------------------------------------
def _seed_cache(db_path: str, law_name: str, article: str, content: str, fetched_at: str | None = None):
    """law_sync.db 에 캐시 행을 직접 삽입한다."""
    fetched_at = fetched_at or datetime.now().isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS law_content_cache (
                law_name TEXT NOT NULL,
                article TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (law_name, article)
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO law_content_cache VALUES (?, ?, ?, ?, ?)",
            (law_name, article, content, "hash", fetched_at),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def empty_db(tmp_path):
    # 존재하지 않는 DB 경로
    return str(tmp_path / "not_exists.db")


@pytest.fixture
def cache_db(tmp_path):
    db = str(tmp_path / "law_sync.db")
    _seed_cache(
        db,
        "관세법",
        "제190조",
        "보세전시장. ① 세관장은 박람회, 전람회, 견본품 전시회 등의 운영을 위하여 "
        "외국물품을 장치·전시하거나 사용할 수 있는 보세구역을 특허할 수 있다. "
        "② 보세전시장의 특허기간은 해당 박람회 등의 회기와 그 회기의 전후 "
        "준비·정리기간을 고려하여 세관장이 정한다.",
        fetched_at="2026-04-24T09:00:00",
    )
    return db


class TestLawContentProviderLookup:
    def test_static_fallback_when_no_db(self, empty_db):
        refs = [
            {
                "law_name": "관세법",
                "article": "제190조",
                "summary": "정적 요약입니다.",
            }
        ]
        p = LawContentProvider(db_path=empty_db, static_refs=refs)
        entry = p.get_latest("관세법", "제190조")
        assert entry is not None
        assert entry["source"] == "static"
        assert entry["preview"] == "정적 요약입니다."

    def test_live_hit(self, cache_db):
        refs = [{"law_name": "관세법", "article": "제190조", "summary": "정적 요약"}]
        p = LawContentProvider(db_path=cache_db, static_refs=refs, preview_len=60)
        entry = p.get_latest("관세법", "제190조")
        assert entry is not None
        assert entry["source"] == "live"
        assert entry["fetched_at"] == "2026-04-24T09:00:00"
        assert "보세전시장" in entry["preview"]
        assert len(entry["preview"]) <= 61  # preview_len + ellipsis

    def test_miss(self, cache_db):
        p = LawContentProvider(db_path=cache_db, static_refs=[])
        assert p.get_latest("없는법", "제1조") is None

    def test_get_for_basis_live(self, cache_db):
        p = LawContentProvider(db_path=cache_db, static_refs=[])
        entry = p.get_for_basis("관세법 제190조(보세전시장)")
        assert entry is not None
        assert entry["source"] == "live"

    def test_get_for_basis_unparseable(self, cache_db):
        p = LawContentProvider(db_path=cache_db, static_refs=[])
        assert p.get_for_basis("random text") is None

    def test_build_legal_guide_live_first(self, cache_db):
        refs = [
            {"law_name": "관세법 시행령", "article": "제101조", "summary": "정적 요약 101"}
        ]
        p = LawContentProvider(db_path=cache_db, static_refs=refs, preview_len=40)
        guide = p.build_legal_guide(
            [
                "관세법 제190조",
                "관세법 시행령 제101조(판매용품의 면허전 사용금지)",
                "보세전시장 운영에 관한 고시 제10조(반출입의 신고)",
            ]
        )
        # 첫 항목은 live 소스 → 국가법령정보센터 표기 포함
        assert any("국가법령정보센터" in g for g in guide)
        # 두 번째는 static 폴백 → 내부 요약 표기
        assert any("내부 요약" in g for g in guide)
        # 캐시·폴백 모두 없는 세 번째는 결과에 포함되지 않는다.
        assert not any("제10조" in g for g in guide)

    def test_preview_truncation(self, tmp_path):
        db = str(tmp_path / "law.db")
        _seed_cache(db, "A법", "제1조", "가" * 500)
        p = LawContentProvider(db_path=db, preview_len=100)
        entry = p.get_latest("A법", "제1조")
        assert entry is not None
        assert entry["preview"].endswith("…")
        assert len(entry["preview"]) <= 101

    def test_corrupted_db_graceful(self, tmp_path):
        # 존재하지만 테이블이 없는 DB에 대해서도 조용히 None 을 반환해야 한다.
        db = str(tmp_path / "empty.db")
        sqlite3.connect(db).close()
        p = LawContentProvider(db_path=db, static_refs=[])
        assert p.get_latest("관세법", "제190조") is None


# ---------------------------------------------------------------------------
# 챗봇 통합: live 소스 감지
# ---------------------------------------------------------------------------
class TestChatbotLiveInjection:
    def test_reload_legal_data_rebuilds_provider(self, tmp_path, monkeypatch):
        from src.chatbot import BondedExhibitionChatbot

        chatbot = BondedExhibitionChatbot()
        # 임시 DB 에 라이브 캐시 시드
        db = str(tmp_path / "law_sync.db")
        _seed_cache(db, "관세법", "제190조", "라이브 본문 테스트 내용")

        # 공급자 DB 경로를 강제로 임시 DB로 교체
        from src.law_content_provider import LawContentProvider as LCP

        original_default = LCP.__init__

        def _patched_init(self, db_path=None, static_refs=None, preview_len=220):
            original_default(self, db_path=db, static_refs=static_refs, preview_len=preview_len)

        monkeypatch.setattr(LCP, "__init__", _patched_init)
        result = chatbot.reload_legal_data()
        assert result["provider"] is True
        entry = chatbot.law_content_provider.get_latest("관세법", "제190조")
        assert entry is not None
        assert entry["source"] == "live"
        assert "라이브 본문" in entry["content"]

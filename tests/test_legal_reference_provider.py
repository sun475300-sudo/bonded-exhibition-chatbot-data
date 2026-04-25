"""Tests for LegalReferenceProvider hot-reload + sync-cache integration."""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from src.legal_reference_provider import (
    LegalReferenceProvider,
    _parse_basis,
    get_legal_reference_provider,
    reset_legal_reference_provider,
)


SEED_DATA = {
    "references": [
        {
            "id": "customs_act_190",
            "law_name": "관세법",
            "article": "제190조",
            "title": "보세전시장",
            "summary": "박람회 등에서 외국물품을 장치·전시·사용할 수 있는 보세구역",
            "url": "https://example.com/190",
        },
        {
            "id": "customs_decree_101",
            "law_name": "관세법 시행령",
            "article": "제101조",
            "title": "판매용품의 면허전 사용금지",
            "summary": "보세전시장에 장치된 판매용 외국물품은 수입면허를 받기 전에는 사용할 수 없음",
            "url": "https://example.com/101",
        },
    ]
}


@pytest.fixture
def temp_ref_file(tmp_path):
    path = tmp_path / "legal_references.json"
    path.write_text(json.dumps(SEED_DATA, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_parse_basis_extracts_law_and_article():
    parts = _parse_basis("관세법 시행령 제101조(판매용품의 면허전 사용금지)")
    assert parts["law_name"] == "관세법 시행령"
    assert parts["article"] == "제101조"

    parts2 = _parse_basis("관세법 제190조")
    assert parts2["law_name"] == "관세법"
    assert parts2["article"] == "제190조"

    parts3 = _parse_basis("관세법 제190조 제2항")
    assert parts3["law_name"] == "관세법"
    assert parts3["article"].startswith("제190조")

    fallback = _parse_basis("보세전시장 운영에 관한 고시")
    assert fallback["law_name"]
    assert fallback["article"] == ""


def test_get_summary_returns_seed_summary(temp_ref_file):
    provider = LegalReferenceProvider(legal_ref_path=temp_ref_file)
    summary = provider.get_summary("관세법", "제190조")
    assert summary and "박람회" in summary


def test_enrich_legal_basis_handles_paren_title(temp_ref_file):
    provider = LegalReferenceProvider(legal_ref_path=temp_ref_file)
    enriched = provider.enrich_legal_basis(
        ["관세법 제190조", "관세법 시행령 제101조(판매용품의 면허전 사용금지)"]
    )
    assert len(enriched) == 2
    assert enriched[0]["title"] == "보세전시장"
    assert enriched[0]["summary"]
    assert enriched[1]["article"] == "제101조"
    assert enriched[1]["summary"].startswith("보세전시장")


def test_build_legal_guide_format(temp_ref_file):
    provider = LegalReferenceProvider(legal_ref_path=temp_ref_file)
    guide = provider.build_legal_guide(["관세법 제190조"])
    assert len(guide) == 1
    assert guide[0].startswith("관세법 제190조")
    assert "보세전시장" in guide[0]


def test_unknown_basis_yields_empty_summary(temp_ref_file):
    provider = LegalReferenceProvider(legal_ref_path=temp_ref_file)
    enriched = provider.enrich_legal_basis(["존재하지않는법 제999조"])
    assert enriched[0]["summary"] == ""
    # build_legal_guide skips empty summaries (no title either)
    guide = provider.build_legal_guide(["존재하지않는법 제999조"])
    assert guide == []


def test_hot_reload_when_file_changes(temp_ref_file):
    provider = LegalReferenceProvider(legal_ref_path=temp_ref_file)
    assert "박람회" in provider.get_summary("관세법", "제190조")

    new_data = json.loads(json.dumps(SEED_DATA))
    new_data["references"][0]["summary"] = "변경된 요약 - 보세전시장 신규 정의"
    # Bump mtime explicitly so a fast-running test still detects the change.
    with open(temp_ref_file, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False)
    new_mtime = time.time() + 5
    os.utime(temp_ref_file, (new_mtime, new_mtime))

    refreshed = provider.refresh_if_stale()
    assert refreshed is True
    assert "변경된 요약" in provider.get_summary("관세법", "제190조")


def test_sync_cache_overrides_file_summary(temp_ref_file):
    class FakeSyncManager:
        def get_cached_content(self, law_name, article):
            if law_name == "관세법" and article == "제190조":
                return {
                    "content": "법령정보센터 최신본문: 박람회·전람회·견본품전시회 등의 운영을 위해 외국물품을 장치할 수 있다.",
                    "content_hash": "abc",
                    "fetched_at": "2026-04-25T10:00:00",
                }
            return None

    provider = LegalReferenceProvider(
        legal_ref_path=temp_ref_file, sync_manager=FakeSyncManager()
    )
    ref = provider.get_reference("관세법", "제190조")
    assert ref["summary"].startswith("법령정보센터 최신본문")
    assert ref["source"] == "law_api_sync"
    assert ref["last_synced"] == "2026-04-25T10:00:00"


def test_sync_cache_with_unknown_basis_fallback(temp_ref_file):
    class FakeSyncManager:
        def get_cached_content(self, law_name, article):
            return {
                "content": "신규 동기화 본문",
                "content_hash": "x",
                "fetched_at": "2026-04-25T11:00:00",
            }

    provider = LegalReferenceProvider(
        legal_ref_path=temp_ref_file, sync_manager=FakeSyncManager()
    )
    summary = provider.get_summary("새로운법", "제1조")
    assert summary == "신규 동기화 본문"


def test_sync_manager_failure_is_silent(temp_ref_file):
    class BadSyncManager:
        def get_cached_content(self, law_name, article):
            raise RuntimeError("DB unavailable")

    provider = LegalReferenceProvider(
        legal_ref_path=temp_ref_file, sync_manager=BadSyncManager()
    )
    # 파일 캐시는 그대로 노출되어야 한다
    assert provider.get_summary("관세법", "제190조")


def test_singleton_helpers_reset(tmp_path, monkeypatch):
    # 임시 파일을 기본 경로로 끼워 넣기
    fake_path = tmp_path / "legal_references.json"
    fake_path.write_text(json.dumps(SEED_DATA, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "src.legal_reference_provider.DEFAULT_LEGAL_REF_PATH", str(fake_path)
    )
    reset_legal_reference_provider()
    p1 = get_legal_reference_provider()
    p2 = get_legal_reference_provider()
    assert p1 is p2
    reset_legal_reference_provider()
    p3 = get_legal_reference_provider()
    assert p3 is not p1
    reset_legal_reference_provider()


def test_missing_file_does_not_crash(tmp_path):
    missing = tmp_path / "missing.json"
    provider = LegalReferenceProvider(legal_ref_path=str(missing))
    assert provider.list_references() == []
    assert provider.get_summary("관세법", "제190조") is None
    assert provider.enrich_legal_basis(["관세법 제190조"]) == [
        {
            "citation": "관세법 제190조",
            "law_name": "관세법",
            "article": "제190조",
            "title": "",
            "summary": "",
            "url": "",
            "source": "legal_references.json",
            "last_synced": None,
        }
    ]

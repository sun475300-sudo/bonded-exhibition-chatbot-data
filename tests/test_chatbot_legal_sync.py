"""Integration test: chatbot picks up legal-reference updates at request time.

This protects the contract that when `data/legal_references.json` is updated
(e.g. by `LawSyncManager.update_legal_references()` after the National Law
Information Center API reports a change) the next chatbot response includes
the new summary without a process restart.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from src.legal_reference_provider import (
    LegalReferenceProvider,
    reset_legal_reference_provider,
)


LEGAL_REF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "legal_references.json",
)


@pytest.fixture
def restore_legal_refs():
    """Snapshot legal_references.json and restore after the test."""
    with open(LEGAL_REF_PATH, "r", encoding="utf-8") as f:
        original = f.read()
    yield
    with open(LEGAL_REF_PATH, "w", encoding="utf-8") as f:
        f.write(original)
    reset_legal_reference_provider()


def test_chatbot_response_includes_law_guide_from_provider(restore_legal_refs):
    reset_legal_reference_provider()
    from src.chatbot import BondedExhibitionChatbot

    chatbot = BondedExhibitionChatbot()
    response = chatbot.process_query("보세전시장이 무엇인가요?")
    assert response
    assert "관세법 제190조" in response
    # Provider supplies the title-bracketed citation in the legal guide.
    assert "[전문가 법령 가이드]" in response or "법령 가이드" in response


def test_chatbot_picks_up_legal_reference_change(restore_legal_refs):
    """legal_references.json 의 summary 가 바뀌면 다음 응답에 즉시 반영된다."""
    reset_legal_reference_provider()
    from src.chatbot import BondedExhibitionChatbot

    chatbot = BondedExhibitionChatbot()
    first = chatbot.process_query("보세전시장이 무엇인가요?")
    assert "관세법 제190조" in first

    with open(LEGAL_REF_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for ref in data.get("references", []):
        if ref.get("law_name") == "관세법" and ref.get("article") == "제190조":
            ref["summary"] = "TEST_UPDATED_SUMMARY: 외국물품의 전시 보세구역"
            break
    with open(LEGAL_REF_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Bump mtime so providers running on fast clocks still detect the change.
    new_mtime = time.time() + 5
    os.utime(LEGAL_REF_PATH, (new_mtime, new_mtime))

    second = chatbot.process_query("보세전시장이 무엇인가요?")
    assert "TEST_UPDATED_SUMMARY" in second, second


def test_chatbot_uses_law_sync_cache_when_available(restore_legal_refs, tmp_path):
    """LawSyncManager 캐시에 새 본문이 있으면 그 값이 우선 사용된다."""
    reset_legal_reference_provider()

    # Inject a provider with a fake sync manager that always wins.
    class FakeSyncManager:
        def get_cached_content(self, law_name, article):
            if law_name == "관세법" and article == "제190조":
                return {
                    "content": "LAW_API_LATEST: 박람회 운영을 위한 신규 정의",
                    "content_hash": "x",
                    "fetched_at": "2026-04-25T12:00:00",
                }
            return None

    provider = LegalReferenceProvider(
        legal_ref_path=LEGAL_REF_PATH, sync_manager=FakeSyncManager()
    )

    from src.chatbot import BondedExhibitionChatbot

    chatbot = BondedExhibitionChatbot()
    chatbot.legal_provider = provider

    response = chatbot.process_query("보세전시장이 무엇인가요?")
    assert "LAW_API_LATEST" in response, response

"""법령 자동 동기화 → 챗봇 답변 반영 통합 테스트.

국가법령정보센터 API 변경이 `data/legal_references.json` 에 반영되면
보세봇의 답변 본문(특히 "전문가 법령 가이드" 섹션)에도 즉시 반영되어야
한다는 계약을 검증한다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chatbot import BondedExhibitionChatbot
from src.utils import load_json


LEGAL_REF_PATH = "data/legal_references.json"


@pytest.fixture
def chatbot_with_restore():
    """파일을 수정하는 테스트가 끝난 뒤 legal_references.json 을 복구한다."""
    backup_path = LEGAL_REF_PATH + ".test_bak"
    shutil.copy(LEGAL_REF_PATH, backup_path)
    try:
        bot = BondedExhibitionChatbot()
        yield bot
    finally:
        shutil.move(backup_path, LEGAL_REF_PATH)


def _set_summary(ref_id: str, new_summary: str) -> None:
    with open(LEGAL_REF_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for ref in data.get("references", []):
        if ref.get("id") == ref_id:
            ref["summary"] = new_summary
    with open(LEGAL_REF_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 파일시스템 mtime 이 동일 초 내에 바뀌지 않을 수도 있으므로 명시적 갱신
    future = time.time() + 1
    os.utime(LEGAL_REF_PATH, (future, future))


class TestLegalReferenceLookup:
    def test_find_legal_reference_by_basis_string(self, chatbot_with_restore):
        bot = chatbot_with_restore
        ref = bot.find_legal_reference("관세법 제190조")
        assert ref is not None
        assert ref["law_name"] == "관세법"
        assert ref["article"] == "제190조"

    def test_find_legal_reference_with_title_suffix(self, chatbot_with_restore):
        bot = chatbot_with_restore
        # FAQ 의 legal_basis 는 "(부제목)" 을 포함할 수 있다
        ref = bot.find_legal_reference("관세법 시행령 제101조(판매용품의 면허전 사용금지)")
        assert ref is not None
        assert ref["article"] == "제101조"
        assert "판매용품" in ref["title"] or "면허전" in ref["title"]

    def test_find_legal_reference_unknown(self, chatbot_with_restore):
        bot = chatbot_with_restore
        assert bot.find_legal_reference("") is None
        assert bot.find_legal_reference("존재하지 않는 법령 제99999조") is None


class TestReloadLegalReferences:
    def test_reload_picks_up_file_changes(self, chatbot_with_restore):
        bot = chatbot_with_restore
        before = bot.find_legal_reference("관세법 제190조")["summary"]
        marker = "[TEST-RELOAD-FLAG] 자동 리로드 검증"
        _set_summary("customs_act_190", marker + " " + before)

        result = bot.reload_legal_references()
        after = bot.find_legal_reference("관세법 제190조")["summary"]
        assert result["reloaded"] is True
        assert marker in after

    def test_auto_reload_on_query(self, chatbot_with_restore):
        bot = chatbot_with_restore
        marker = "[TEST-AUTO-RELOAD] 보세전시장 요약 갱신"
        _set_summary("customs_act_190", marker)

        # 쿼리를 수행하면 _maybe_reload_legal_refs 가 호출되어 파일 변경을
        # 감지하고 자동으로 법령 데이터를 다시 로드해야 한다.
        result = bot.process_query("보세전시장이 무엇인가요?", include_metadata=True)
        assert marker in result["response"], (
            "보세봇 답변이 업데이트된 법령 요약을 즉시 반영해야 합니다.\n"
            f"응답:\n{result['response']}"
        )


class TestLegalGuideIntegration:
    def test_response_includes_legal_guide_section(self, chatbot_with_restore):
        bot = chatbot_with_restore
        result = bot.process_query("보세전시장이 무엇인가요?", include_metadata=True)
        # 요약이 존재할 때는 "전문가 법령 가이드" 섹션이 포함되어야 한다.
        assert "전문가 법령 가이드" in result["response"]

    def test_legal_guide_reflects_summary_from_legal_refs(self, chatbot_with_restore):
        bot = chatbot_with_restore
        ref = bot.find_legal_reference("관세법 제190조")
        result = bot.process_query("보세전시장이 무엇인가요?", include_metadata=True)
        # legal_references.json 의 요약이 응답 본문에 나타나야 한다
        summary_head = (ref["summary"] or "")[:20]
        assert summary_head and summary_head in result["response"]

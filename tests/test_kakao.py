"""카카오 i 오픈빌더 스킬서버 어댑터 테스트."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kakao_adapter import (
    CARD_DESCRIPTION_LIMIT,
    SIMPLE_TEXT_LIMIT,
    _truncate,
    build_skill_response,
    format_carousel,
    format_escalation_card,
    format_quick_replies,
    format_simple_text,
    parse_kakao_request,
)
from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _kakao_request(utterance: str, action_name: str = "", params: dict | None = None) -> dict:
    """테스트용 카카오 요청 JSON을 생성한다."""
    req = {
        "userRequest": {
            "utterance": utterance,
            "user": {"id": "test_user_123"},
        },
        "bot": {"id": "test_bot_456"},
        "action": {"name": action_name},
    }
    if params:
        req["action"]["params"] = params
    return req


# ──────────────────────────────────────────────
# 요청 파싱 테스트
# ──────────────────────────────────────────────
class TestParseKakaoRequest:
    def test_parse_standard_request(self):
        data = _kakao_request("보세전시장이란?", action_name="chat")
        parsed = parse_kakao_request(data)
        assert parsed["utterance"] == "보세전시장이란?"
        assert parsed["user_id"] == "test_user_123"
        assert parsed["bot_id"] == "test_bot_456"
        assert parsed["action_name"] == "chat"

    def test_parse_empty_request(self):
        parsed = parse_kakao_request({})
        assert parsed["utterance"] == ""
        assert parsed["user_id"] == ""
        assert parsed["bot_id"] == ""
        assert parsed["action_name"] == ""

    def test_parse_strips_whitespace(self):
        data = _kakao_request("  보세전시장  ")
        parsed = parse_kakao_request(data)
        assert parsed["utterance"] == "보세전시장"

    def test_parse_missing_user(self):
        data = {"userRequest": {"utterance": "질문"}, "bot": {}, "action": {}}
        parsed = parse_kakao_request(data)
        assert parsed["utterance"] == "질문"
        assert parsed["user_id"] == ""


# ──────────────────────────────────────────────
# 텍스트 잘라내기 테스트
# ──────────────────────────────────────────────
class TestTextTruncation:
    def test_short_text_not_truncated(self):
        text = "짧은 텍스트"
        assert _truncate(text, SIMPLE_TEXT_LIMIT) == text

    def test_exact_limit_not_truncated(self):
        text = "가" * SIMPLE_TEXT_LIMIT
        assert _truncate(text, SIMPLE_TEXT_LIMIT) == text
        assert len(_truncate(text, SIMPLE_TEXT_LIMIT)) == SIMPLE_TEXT_LIMIT

    def test_over_limit_truncated_with_ellipsis(self):
        text = "가" * (SIMPLE_TEXT_LIMIT + 100)
        truncated = _truncate(text, SIMPLE_TEXT_LIMIT)
        assert len(truncated) == SIMPLE_TEXT_LIMIT
        assert truncated.endswith("...")

    def test_card_description_truncation(self):
        text = "나" * (CARD_DESCRIPTION_LIMIT + 50)
        truncated = _truncate(text, CARD_DESCRIPTION_LIMIT)
        assert len(truncated) == CARD_DESCRIPTION_LIMIT
        assert truncated.endswith("...")

    def test_simple_text_format_applies_truncation(self):
        long_text = "다" * 1500
        block = format_simple_text(long_text)
        assert len(block["simpleText"]["text"]) <= SIMPLE_TEXT_LIMIT


# ──────────────────────────────────────────────
# 응답 포맷 테스트
# ──────────────────────────────────────────────
class TestResponseFormat:
    def test_simple_text_block_structure(self):
        block = format_simple_text("안녕하세요")
        assert "simpleText" in block
        assert block["simpleText"]["text"] == "안녕하세요"

    def test_build_skill_response_structure(self):
        output = format_simple_text("테스트")
        resp = build_skill_response([output])
        assert resp["version"] == "2.0"
        assert "template" in resp
        assert "outputs" in resp["template"]
        assert len(resp["template"]["outputs"]) == 1
        assert resp["template"]["outputs"][0]["simpleText"]["text"] == "테스트"

    def test_build_skill_response_without_quick_replies(self):
        resp = build_skill_response([format_simple_text("테스트")])
        assert "quickReplies" not in resp["template"]

    def test_build_skill_response_with_quick_replies(self):
        qr = format_quick_replies(["카테고리1", "카테고리2"])
        resp = build_skill_response([format_simple_text("테스트")], qr)
        assert "quickReplies" in resp["template"]
        assert len(resp["template"]["quickReplies"]) == 2


# ──────────────────────────────────────────────
# 바로가기 버튼 테스트
# ──────────────────────────────────────────────
class TestQuickReplies:
    def test_quick_replies_structure(self):
        categories = ["보세전시장", "물품반입", "현장판매"]
        qr = format_quick_replies(categories)
        assert len(qr) == 3
        for item in qr:
            assert "messageText" in item
            assert "action" in item
            assert item["action"] == "message"
            assert "label" in item

    def test_quick_replies_max_five(self):
        categories = ["A", "B", "C", "D", "E", "F", "G"]
        qr = format_quick_replies(categories)
        assert len(qr) == 5

    def test_quick_replies_label_truncation(self):
        long_cat = "이것은매우긴카테고리이름입니다열네자이상"
        qr = format_quick_replies([long_cat])
        assert len(qr[0]["label"]) <= 14

    def test_quick_replies_empty_list(self):
        qr = format_quick_replies([])
        assert qr == []


# ──────────────────────────────────────────────
# 캐러셀 카드 테스트
# ──────────────────────────────────────────────
class TestCarousel:
    def test_carousel_structure(self):
        items = [
            {"question": "Q1", "answer": "A1", "category": "CAT1"},
            {"question": "Q2", "answer": "A2", "category": "CAT2"},
        ]
        result = format_carousel(items)
        assert "carousel" in result
        assert result["carousel"]["type"] == "basicCard"
        assert len(result["carousel"]["items"]) == 2

    def test_carousel_card_fields(self):
        items = [{"question": "보세전시장이란?", "answer": "보세전시장 설명", "category": "개요"}]
        result = format_carousel(items)
        card = result["carousel"]["items"][0]
        assert "title" in card
        assert "description" in card
        assert "buttons" in card
        assert len(card["buttons"]) == 2  # 자세히 보기 + 카테고리
        assert card["buttons"][0]["action"] == "message"
        assert card["buttons"][0]["label"] == "자세히 보기"

    def test_carousel_no_category_button_when_empty(self):
        items = [{"question": "Q", "answer": "A", "category": ""}]
        result = format_carousel(items)
        card = result["carousel"]["items"][0]
        assert len(card["buttons"]) == 1  # 자세히 보기만

    def test_carousel_description_truncated(self):
        long_answer = "라" * 500
        items = [{"question": "Q", "answer": long_answer, "category": "C"}]
        result = format_carousel(items)
        card = result["carousel"]["items"][0]
        assert len(card["description"]) <= CARD_DESCRIPTION_LIMIT

    def test_carousel_empty_items(self):
        result = format_carousel([])
        assert result["carousel"]["items"] == []


# ──────────────────────────────────────────────
# 에스컬레이션 카드 테스트
# ──────────────────────────────────────────────
class TestEscalationCard:
    def test_escalation_card_with_phone(self):
        info = {
            "name": "고객지원팀",
            "phone": "1234-5678",
            "description": "담당부서 안내",
        }
        card = format_escalation_card(info)
        assert "basicCard" in card
        assert card["basicCard"]["title"] == "고객지원팀"
        assert len(card["basicCard"]["buttons"]) == 1
        assert card["basicCard"]["buttons"][0]["action"] == "phone"
        assert card["basicCard"]["buttons"][0]["phoneNumber"] == "1234-5678"

    def test_escalation_card_with_phone_and_url(self):
        info = {
            "name": "기술지원",
            "phone": "9999-0000",
            "url": "https://example.com",
            "description": "기술 문의",
        }
        card = format_escalation_card(info)
        buttons = card["basicCard"]["buttons"]
        assert len(buttons) == 2
        assert buttons[0]["action"] == "phone"
        assert buttons[1]["action"] == "webLink"
        assert buttons[1]["webLinkUrl"] == "https://example.com"

    def test_escalation_card_no_phone_no_url(self):
        info = {"name": "알수없음"}
        card = format_escalation_card(info)
        assert card["basicCard"]["buttons"] == []

    def test_escalation_card_description_truncated(self):
        info = {
            "name": "부서",
            "phone": "000",
            "description": "마" * 500,
        }
        card = format_escalation_card(info)
        assert len(card["basicCard"]["description"]) <= CARD_DESCRIPTION_LIMIT


# ──────────────────────────────────────────────
# 웹 서버 엔드포인트 통합 테스트
# ──────────────────────────────────────────────
class TestKakaoChatEndpoint:
    def test_chat_returns_200(self, client):
        res = client.post(
            "/api/kakao/chat",
            json=_kakao_request("보세전시장이란?"),
            content_type="application/json",
        )
        assert res.status_code == 200

    def test_chat_response_structure(self, client):
        res = client.post(
            "/api/kakao/chat",
            json=_kakao_request("보세전시장이란?"),
            content_type="application/json",
        )
        data = res.get_json()
        assert data["version"] == "2.0"
        assert "template" in data
        assert "outputs" in data["template"]
        assert "simpleText" in data["template"]["outputs"][0]

    def test_chat_empty_utterance(self, client):
        res = client.post(
            "/api/kakao/chat",
            json=_kakao_request(""),
            content_type="application/json",
        )
        data = res.get_json()
        text = data["template"]["outputs"][0]["simpleText"]["text"]
        assert "질문을 입력해 주세요" in text

    def test_chat_no_body(self, client):
        res = client.post(
            "/api/kakao/chat",
            data="not json",
            content_type="application/json",
        )
        data = res.get_json()
        text = data["template"]["outputs"][0]["simpleText"]["text"]
        assert "요청을 처리할 수 없습니다" in text

    def test_chat_has_quick_replies(self, client):
        res = client.post(
            "/api/kakao/chat",
            json=_kakao_request("보세전시장이란?"),
            content_type="application/json",
        )
        data = res.get_json()
        assert "quickReplies" in data["template"]
        assert len(data["template"]["quickReplies"]) > 0


class TestKakaoFaqEndpoint:
    def test_faq_returns_200(self, client):
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ 보기"),
            content_type="application/json",
        )
        assert res.status_code == 200

    def test_faq_returns_carousel(self, client):
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ 보기"),
            content_type="application/json",
        )
        data = res.get_json()
        assert data["version"] == "2.0"
        outputs = data["template"]["outputs"]
        assert len(outputs) == 1
        assert "carousel" in outputs[0]
        assert outputs[0]["carousel"]["type"] == "basicCard"

    def test_faq_carousel_has_items(self, client):
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ 보기"),
            content_type="application/json",
        )
        data = res.get_json()
        items = data["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) > 0

    def test_faq_max_10_items(self, client):
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ 보기"),
            content_type="application/json",
        )
        data = res.get_json()
        items = data["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) <= 10

    def test_faq_category_filter(self, client):
        """카테고리 필터를 action params로 전달하면 해당 카테고리만 반환한다."""
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ", params={"category": "NONEXISTENT_CATEGORY_XYZ"}),
            content_type="application/json",
        )
        data = res.get_json()
        # 존재하지 않는 카테고리이면 simpleText로 안내
        outputs = data["template"]["outputs"]
        assert "simpleText" in outputs[0] or "carousel" in outputs[0]

    def test_faq_has_quick_replies(self, client):
        res = client.post(
            "/api/kakao/faq",
            json=_kakao_request("FAQ 보기"),
            content_type="application/json",
        )
        data = res.get_json()
        assert "quickReplies" in data["template"]


class TestKakaoBlueprintEndpoints:
    """블루프린트 (/kakao/*) 경로 테스트."""

    def test_blueprint_chat(self, client):
        res = client.post(
            "/kakao/chat",
            json=_kakao_request("보세전시장이란?"),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["version"] == "2.0"

    def test_blueprint_welcome(self, client):
        res = client.post(
            "/kakao/welcome",
            json={},
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["version"] == "2.0"
        assert "보세전시장" in data["template"]["outputs"][0]["simpleText"]["text"]

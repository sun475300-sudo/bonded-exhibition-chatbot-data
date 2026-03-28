"""네이버 톡톡 웹훅 어댑터 테스트."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.naver_adapter import (
    EVENT_FRIEND,
    EVENT_LEAVE,
    EVENT_OPEN,
    EVENT_SEND,
    NAVER_BUTTON_LABEL_LIMIT,
    NAVER_BUTTON_MAX_COUNT,
    NAVER_CAROUSEL_MAX_ITEMS,
    NAVER_COMPOSITE_DESCRIPTION_LIMIT,
    NAVER_COMPOSITE_TITLE_LIMIT,
    NAVER_TEXT_LIMIT,
    NaverTalkTalkAdapter,
)
from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def adapter():
    return NaverTalkTalkAdapter()


def _naver_send_event(text: str, user: str = "test_user_001") -> dict:
    """테스트용 네이버 톡톡 send 이벤트 JSON을 생성한다."""
    return {
        "event": "send",
        "user": user,
        "textContent": {"text": text},
    }


def _naver_open_event(user: str = "test_user_001") -> dict:
    """테스트용 네이버 톡톡 open 이벤트 JSON을 생성한다."""
    return {
        "event": "open",
        "user": user,
    }


def _naver_leave_event(user: str = "test_user_001") -> dict:
    """테스트용 네이버 톡톡 leave 이벤트 JSON을 생성한다."""
    return {
        "event": "leave",
        "user": user,
    }


def _naver_friend_event(user: str = "test_user_001", set_on: str = "on") -> dict:
    """테스트용 네이버 톡톡 friend 이벤트 JSON을 생성한다."""
    return {
        "event": "friend",
        "user": user,
        "options": {"set": set_on},
    }


# ──────────────────────────────────────────────
# 웹훅 파싱 테스트
# ──────────────────────────────────────────────
class TestParseWebhook:
    def test_parse_send_event(self, adapter):
        data = _naver_send_event("보세전시장이란?")
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_SEND
        assert parsed["user_id"] == "test_user_001"
        assert parsed["text"] == "보세전시장이란?"
        assert parsed["image_url"] is None

    def test_parse_send_with_image(self, adapter):
        data = {
            "event": "send",
            "user": "user_img",
            "textContent": {"text": ""},
            "imageContent": {"imageUrl": "https://example.com/img.jpg"},
        }
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_SEND
        assert parsed["image_url"] == "https://example.com/img.jpg"

    def test_parse_open_event(self, adapter):
        data = _naver_open_event()
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_OPEN
        assert parsed["user_id"] == "test_user_001"
        assert parsed["text"] == ""

    def test_parse_leave_event(self, adapter):
        data = _naver_leave_event()
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_LEAVE
        assert parsed["user_id"] == "test_user_001"

    def test_parse_friend_event(self, adapter):
        data = _naver_friend_event(set_on="on")
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_FRIEND
        assert parsed["options"]["set_on"] == "on"

    def test_parse_friend_off_event(self, adapter):
        data = _naver_friend_event(set_on="off")
        parsed = adapter.parse_webhook(data)
        assert parsed["event"] == EVENT_FRIEND
        assert parsed["options"]["set_on"] == "off"

    def test_parse_empty_data(self, adapter):
        parsed = adapter.parse_webhook({})
        assert parsed["event"] == ""
        assert parsed["user_id"] == ""
        assert parsed["text"] == ""
        assert parsed["image_url"] is None

    def test_parse_none_data(self, adapter):
        parsed = adapter.parse_webhook(None)
        assert parsed["event"] == ""
        assert parsed["user_id"] == ""

    def test_parse_strips_whitespace(self, adapter):
        data = {
            "event": "send",
            "user": "u1",
            "textContent": {"text": "  보세전시장  "},
        }
        parsed = adapter.parse_webhook(data)
        assert parsed["text"] == "보세전시장"

    def test_parse_missing_text_content(self, adapter):
        data = {"event": "send", "user": "u1"}
        parsed = adapter.parse_webhook(data)
        assert parsed["text"] == ""


# ──────────────────────────────────────────────
# 텍스트 잘라내기 테스트
# ──────────────────────────────────────────────
class TestTruncateText:
    def test_short_text_not_truncated(self, adapter):
        text = "짧은 텍스트"
        assert adapter.truncate_text(text) == text

    def test_exact_limit_not_truncated(self, adapter):
        text = "가" * NAVER_TEXT_LIMIT
        result = adapter.truncate_text(text)
        assert result == text
        assert len(result) == NAVER_TEXT_LIMIT

    def test_over_limit_truncated_with_ellipsis(self, adapter):
        text = "가" * (NAVER_TEXT_LIMIT + 100)
        result = adapter.truncate_text(text)
        assert len(result) == NAVER_TEXT_LIMIT
        assert result.endswith("...")

    def test_custom_limit(self, adapter):
        text = "가" * 50
        result = adapter.truncate_text(text, limit=20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_empty_text(self, adapter):
        assert adapter.truncate_text("") == ""


# ──────────────────────────────────────────────
# 텍스트 응답 포맷 테스트
# ──────────────────────────────────────────────
class TestFormatTextResponse:
    def test_text_response_structure(self, adapter):
        resp = adapter.format_text_response("안녕하세요")
        assert resp["event"] == "send"
        assert "textContent" in resp
        assert resp["textContent"]["text"] == "안녕하세요"

    def test_text_response_truncation(self, adapter):
        long_text = "나" * 1500
        resp = adapter.format_text_response(long_text)
        assert len(resp["textContent"]["text"]) <= NAVER_TEXT_LIMIT

    def test_text_response_empty(self, adapter):
        resp = adapter.format_text_response("")
        assert resp["textContent"]["text"] == ""


# ──────────────────────────────────────────────
# 버튼 응답 포맷 테스트
# ──────────────────────────────────────────────
class TestFormatButtonResponse:
    def test_button_response_structure(self, adapter):
        buttons = [
            {"label": "버튼1", "value": "값1"},
            {"label": "버튼2", "value": "값2"},
        ]
        resp = adapter.format_button_response("텍스트", buttons)
        assert resp["event"] == "send"
        assert "textContent" in resp
        assert resp["textContent"]["text"] == "텍스트"
        assert "quickReply" in resp["textContent"]
        btn_list = resp["textContent"]["quickReply"]["buttonList"]
        assert len(btn_list) == 2

    def test_button_text_type(self, adapter):
        buttons = [{"label": "질문", "value": "보세전시장이란?"}]
        resp = adapter.format_button_response("응답", buttons)
        btn = resp["textContent"]["quickReply"]["buttonList"][0]
        assert btn["type"] == "text"
        assert btn["data"]["label"] == "질문"
        assert btn["data"]["code"] == "보세전시장이란?"

    def test_button_weblink_type(self, adapter):
        buttons = [{"label": "사이트", "value": "https://example.com"}]
        resp = adapter.format_button_response("응답", buttons)
        btn = resp["textContent"]["quickReply"]["buttonList"][0]
        assert btn["type"] == "webLink"
        assert btn["data"]["url"] == "https://example.com"

    def test_button_max_count(self, adapter):
        buttons = [{"label": f"btn{i}", "value": f"v{i}"} for i in range(15)]
        resp = adapter.format_button_response("텍스트", buttons)
        btn_list = resp["textContent"]["quickReply"]["buttonList"]
        assert len(btn_list) == NAVER_BUTTON_MAX_COUNT

    def test_button_label_truncation(self, adapter):
        buttons = [{"label": "이것은매우긴버튼라벨입니다열여덟자이상초과", "value": "v"}]
        resp = adapter.format_button_response("텍스트", buttons)
        btn = resp["textContent"]["quickReply"]["buttonList"][0]
        assert len(btn["data"]["label"]) <= NAVER_BUTTON_LABEL_LIMIT

    def test_button_empty_list(self, adapter):
        resp = adapter.format_button_response("텍스트", [])
        btn_list = resp["textContent"]["quickReply"]["buttonList"]
        assert btn_list == []


# ──────────────────────────────────────────────
# 컴포지트 응답 포맷 테스트
# ──────────────────────────────────────────────
class TestFormatCompositeResponse:
    def test_composite_structure(self, adapter):
        resp = adapter.format_composite_response(
            title="제목",
            description="설명",
            buttons=[{"label": "버튼", "value": "값"}],
        )
        assert resp["event"] == "send"
        assert "compositeContent" in resp
        composite_list = resp["compositeContent"]["compositeList"]
        assert len(composite_list) == 1
        card = composite_list[0]
        assert card["title"] == "제목"
        assert card["description"] == "설명"
        assert len(card["buttonList"]) == 1

    def test_composite_with_image(self, adapter):
        resp = adapter.format_composite_response(
            title="제목",
            description="설명",
            buttons=[],
            image_url="https://example.com/img.png",
        )
        card = resp["compositeContent"]["compositeList"][0]
        assert card["image"]["imageUrl"] == "https://example.com/img.png"

    def test_composite_without_image(self, adapter):
        resp = adapter.format_composite_response(
            title="제목",
            description="설명",
            buttons=[],
        )
        card = resp["compositeContent"]["compositeList"][0]
        assert card["image"] is None

    def test_composite_title_truncation(self, adapter):
        long_title = "가" * 100
        resp = adapter.format_composite_response(
            title=long_title,
            description="설명",
            buttons=[],
        )
        card = resp["compositeContent"]["compositeList"][0]
        assert len(card["title"]) <= NAVER_COMPOSITE_TITLE_LIMIT

    def test_composite_description_truncation(self, adapter):
        long_desc = "나" * 300
        resp = adapter.format_composite_response(
            title="제목",
            description=long_desc,
            buttons=[],
        )
        card = resp["compositeContent"]["compositeList"][0]
        assert len(card["description"]) <= NAVER_COMPOSITE_DESCRIPTION_LIMIT


# ──────────────────────────────────────────────
# 캐러셀 포맷 테스트
# ──────────────────────────────────────────────
class TestFormatCarousel:
    def test_carousel_structure(self, adapter):
        items = [
            {"question": "Q1", "answer": "A1", "category": "CAT1"},
            {"question": "Q2", "answer": "A2", "category": "CAT2"},
        ]
        result = adapter.format_carousel(items)
        assert result["event"] == "send"
        assert "compositeContent" in result
        composite_list = result["compositeContent"]["compositeList"]
        assert len(composite_list) == 2

    def test_carousel_card_fields(self, adapter):
        items = [{"question": "보세전시장이란?", "answer": "보세전시장 설명", "category": "개요"}]
        result = adapter.format_carousel(items)
        card = result["compositeContent"]["compositeList"][0]
        assert "title" in card
        assert "description" in card
        assert "buttonList" in card
        assert len(card["buttonList"]) == 2  # 자세히 보기 + 카테고리
        assert card["buttonList"][0]["data"]["label"] == "자세히 보기"

    def test_carousel_no_category_button_when_empty(self, adapter):
        items = [{"question": "Q", "answer": "A", "category": ""}]
        result = adapter.format_carousel(items)
        card = result["compositeContent"]["compositeList"][0]
        assert len(card["buttonList"]) == 1

    def test_carousel_max_items(self, adapter):
        items = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(15)]
        result = adapter.format_carousel(items)
        composite_list = result["compositeContent"]["compositeList"]
        assert len(composite_list) == NAVER_CAROUSEL_MAX_ITEMS

    def test_carousel_empty_items(self, adapter):
        result = adapter.format_carousel([])
        assert result["compositeContent"]["compositeList"] == []

    def test_carousel_title_truncation(self, adapter):
        items = [{"question": "가" * 100, "answer": "A", "category": "C"}]
        result = adapter.format_carousel(items)
        card = result["compositeContent"]["compositeList"][0]
        assert len(card["title"]) <= NAVER_COMPOSITE_TITLE_LIMIT

    def test_carousel_description_truncation(self, adapter):
        items = [{"question": "Q", "answer": "나" * 300, "category": "C"}]
        result = adapter.format_carousel(items)
        card = result["compositeContent"]["compositeList"][0]
        assert len(card["description"]) <= NAVER_COMPOSITE_DESCRIPTION_LIMIT

    def test_carousel_with_image(self, adapter):
        items = [{"question": "Q", "answer": "A", "image_url": "https://img.com/a.jpg"}]
        result = adapter.format_carousel(items)
        card = result["compositeContent"]["compositeList"][0]
        assert card["image"]["imageUrl"] == "https://img.com/a.jpg"


# ──────────────────────────────────────────────
# build_response 테스트
# ──────────────────────────────────────────────
class TestBuildResponse:
    def test_build_text_response(self, adapter):
        resp = adapter.build_response("send", {
            "user_id": "u1",
            "text": "응답입니다",
        })
        assert resp["event"] == "send"
        assert resp["user"] == "u1"
        assert resp["textContent"]["text"] == "응답입니다"

    def test_build_button_response(self, adapter):
        resp = adapter.build_response("send", {
            "user_id": "u1",
            "text": "응답",
            "buttons": [{"label": "btn", "value": "v"}],
        })
        assert resp["event"] == "send"
        assert resp["user"] == "u1"
        assert "quickReply" in resp["textContent"]

    def test_build_response_no_buttons(self, adapter):
        resp = adapter.build_response("open", {
            "user_id": "u1",
            "text": "환영합니다",
        })
        assert "quickReply" not in resp.get("textContent", {})


# ──────────────────────────────────────────────
# 웹훅 엔드포인트 통합 테스트
# ──────────────────────────────────────────────
class TestNaverWebhookEndpoint:
    def test_post_send_event_returns_200(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_send_event("보세전시장이란?"),
            content_type="application/json",
        )
        assert res.status_code == 200

    def test_post_send_event_response_structure(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_send_event("보세전시장이란?"),
            content_type="application/json",
        )
        data = res.get_json()
        assert data["event"] == "send"
        assert "textContent" in data
        assert "user" in data

    def test_post_send_event_has_buttons(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_send_event("보세전시장이란?"),
            content_type="application/json",
        )
        data = res.get_json()
        assert "quickReply" in data["textContent"]
        assert len(data["textContent"]["quickReply"]["buttonList"]) > 0

    def test_post_open_event_returns_welcome(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_open_event(),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert "보세전시장" in data["textContent"]["text"]

    def test_post_leave_event_returns_200(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_leave_event(),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_post_friend_event_returns_200(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_friend_event(),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_post_empty_body_returns_200(self, client):
        res = client.post(
            "/api/naver/webhook",
            data="not json",
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_post_send_empty_text(self, client):
        res = client.post(
            "/api/naver/webhook",
            json=_naver_send_event(""),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert "질문을 입력해 주세요" in data["textContent"]["text"]

    def test_get_webhook_verification(self, client):
        res = client.get("/api/naver/webhook?challenge=test_challenge_123")
        assert res.status_code == 200
        assert res.data.decode("utf-8") == "test_challenge_123"

    def test_get_webhook_no_challenge(self, client):
        res = client.get("/api/naver/webhook")
        assert res.status_code == 200
        assert res.data.decode("utf-8") == ""

    def test_post_malformed_event(self, client):
        """알 수 없는 이벤트 타입은 200 OK를 반환한다."""
        res = client.post(
            "/api/naver/webhook",
            json={"event": "unknown_event", "user": "u1"},
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

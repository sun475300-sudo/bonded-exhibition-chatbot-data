"""웹 API 테스트."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestHealthEndpoint:
    def test_health_ok(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "ok"
        assert data["faq_count"] >= 7


class TestConfigEndpoint:
    def test_config_returns_persona(self, client):
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.get_json()
        assert "persona" in data
        assert "보세전시장" in data["persona"]

    def test_config_returns_categories(self, client):
        res = client.get("/api/config")
        data = res.get_json()
        assert len(data["categories"]) == 10

    def test_config_returns_contacts(self, client):
        res = client.get("/api/config")
        data = res.get_json()
        assert "customer_support" in data["contacts"]
        assert "tech_support" in data["contacts"]


class TestFaqEndpoint:
    def test_faq_list(self, client):
        res = client.get("/api/faq")
        assert res.status_code == 200
        data = res.get_json()
        assert data["count"] >= 7
        assert len(data["items"]) == data["count"]

    def test_faq_item_structure(self, client):
        res = client.get("/api/faq")
        data = res.get_json()
        item = data["items"][0]
        assert "id" in item
        assert "category" in item
        assert "question" in item


class TestChatEndpoint:
    def test_basic_question(self, client):
        res = client.post("/api/chat", json={"query": "보세전시장이 무엇인가요?"})
        assert res.status_code == 200
        data = res.get_json()
        assert "answer" in data
        assert "category" in data
        assert "categories" in data
        assert "is_escalation" in data
        assert "관세법 제190조" in data["answer"]

    def test_escalation_question(self, client):
        res = client.post("/api/chat", json={"query": "UNI-PASS 시스템 오류"})
        data = res.get_json()
        assert data["is_escalation"] is True
        assert data["escalation_target"] == "tech_support"
        assert "1544-1285" in data["answer"]

    def test_empty_query_rejected(self, client):
        res = client.post("/api/chat", json={"query": ""})
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_missing_query_rejected(self, client):
        res = client.post("/api/chat", json={})
        assert res.status_code == 400

    def test_no_json_rejected(self, client):
        res = client.post("/api/chat", data="not json",
                          content_type="text/plain")
        assert res.status_code in (400, 415)

    def test_sales_category(self, client):
        res = client.post("/api/chat", json={"query": "현장에서 판매 가능한가요?"})
        data = res.get_json()
        assert data["category"] == "SALES"

    def test_response_always_has_disclaimer(self, client):
        queries = [
            "보세전시장이란?",
            "물품 반입 절차",
            "견본품 반출 허가",
        ]
        for q in queries:
            res = client.post("/api/chat", json={"query": q})
            data = res.get_json()
            assert "안내" in data["answer"], f"'{q}' 답변에 안내 문구 누락"


class TestIndexPage:
    def test_index_returns_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"<!DOCTYPE html>" in res.data
        assert "보세전시장".encode("utf-8") in res.data

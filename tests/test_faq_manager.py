"""FAQ Manager CRUD 테스트."""

import copy
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.faq_manager import FAQManager, VALID_CATEGORIES


# --- Fixtures ---

SAMPLE_FAQ_DATA = {
    "faq_version": "3.0.0",
    "last_updated": "2026-03-27",
    "items": [
        {
            "id": "A",
            "category": "GENERAL",
            "question": "보세전시장이 무엇인가요?",
            "answer": "보세전시장은 외국물품을 전시할 수 있는 보세구역입니다.",
            "legal_basis": ["관세법 제190조"],
            "notes": "",
            "keywords": ["보세전시장", "정의", "개념"],
        },
        {
            "id": "B",
            "category": "IMPORT_EXPORT",
            "question": "물품 반입 시 신고가 필요한가요?",
            "answer": "네, 세관장에게 반출입신고를 해야 합니다.",
            "legal_basis": ["보세전시장 운영에 관한 고시 제10조"],
            "notes": "",
            "keywords": ["반입", "반출", "신고"],
        },
    ],
}


@pytest.fixture
def faq_env(tmp_path):
    """Create a temporary FAQ JSON and history DB for testing."""
    faq_file = tmp_path / "faq.json"
    faq_file.write_text(json.dumps(SAMPLE_FAQ_DATA, ensure_ascii=False), encoding="utf-8")
    history_db = tmp_path / "faq_history.db"
    return FAQManager(faq_path=str(faq_file), history_db_path=str(history_db))


# --- CRUD Tests ---

class TestListAll:
    def test_returns_all_items(self, faq_env):
        items = faq_env.list_all()
        assert len(items) == 2

    def test_items_have_metadata(self, faq_env):
        items = faq_env.list_all()
        assert "keywords_count" in items[0]
        assert items[0]["keywords_count"] == 3

    def test_items_have_last_modified(self, faq_env):
        items = faq_env.list_all()
        # No history yet, so last_modified is None
        assert items[0]["last_modified"] is None


class TestGet:
    def test_get_existing(self, faq_env):
        item = faq_env.get("A")
        assert item is not None
        assert item["question"] == "보세전시장이 무엇인가요?"

    def test_get_nonexistent(self, faq_env):
        item = faq_env.get("ZZZ")
        assert item is None


class TestCreate:
    def test_create_with_auto_id(self, faq_env):
        new_item = {
            "category": "SALES",
            "question": "현장 판매가 가능한가요?",
            "answer": "통관 절차를 거쳐야 합니다.",
            "keywords": ["판매"],
        }
        created = faq_env.create(new_item)
        assert created["id"] == "C"  # next after A, B
        assert created["category"] == "SALES"
        assert len(faq_env.list_all()) == 3

    def test_create_with_explicit_id(self, faq_env):
        new_item = {
            "id": "Z",
            "category": "CONTACT",
            "question": "어디에 문의하면 되나요?",
            "answer": "관할 세관에 문의하세요.",
        }
        created = faq_env.create(new_item)
        assert created["id"] == "Z"

    def test_create_duplicate_id_raises(self, faq_env):
        new_item = {
            "id": "A",
            "category": "GENERAL",
            "question": "중복 테스트",
            "answer": "중복",
        }
        with pytest.raises(ValueError, match="already exists"):
            faq_env.create(new_item)

    def test_create_missing_required_field(self, faq_env):
        with pytest.raises(ValueError, match="Required field"):
            faq_env.create({"category": "GENERAL", "question": "질문만"})

    def test_create_invalid_category(self, faq_env):
        with pytest.raises(ValueError, match="Invalid category"):
            faq_env.create({
                "category": "INVALID",
                "question": "질문",
                "answer": "답변",
            })

    def test_create_persists_to_file(self, faq_env):
        faq_env.create({
            "category": "GENERAL",
            "question": "영속성 테스트",
            "answer": "파일에 저장됩니다.",
        })
        # Read the file directly
        with open(faq_env.faq_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["items"]) == 3


class TestUpdate:
    def test_update_existing(self, faq_env):
        updated = faq_env.update("A", {
            "category": "GENERAL",
            "question": "수정된 질문",
            "answer": "수정된 답변",
            "keywords": ["수정"],
        })
        assert updated["question"] == "수정된 질문"
        assert updated["id"] == "A"

    def test_update_nonexistent_raises(self, faq_env):
        with pytest.raises(KeyError, match="not found"):
            faq_env.update("ZZZ", {
                "category": "GENERAL",
                "question": "없는 항목",
                "answer": "답변",
            })

    def test_update_invalid_data_raises(self, faq_env):
        with pytest.raises(ValueError):
            faq_env.update("A", {"category": "GENERAL", "question": ""})


class TestDelete:
    def test_delete_existing(self, faq_env):
        deleted = faq_env.delete("B")
        assert deleted["id"] == "B"
        assert len(faq_env.list_all()) == 1

    def test_delete_nonexistent_raises(self, faq_env):
        with pytest.raises(KeyError, match="not found"):
            faq_env.delete("ZZZ")


# --- History Tests ---

class TestHistory:
    def test_create_logs_history(self, faq_env):
        faq_env.create({
            "category": "GENERAL",
            "question": "이력 테스트",
            "answer": "이력 답변",
        })
        history = faq_env.get_history("C")
        assert len(history) == 1
        assert history[0]["action"] == "create"
        assert history[0]["new_data"]["question"] == "이력 테스트"

    def test_update_logs_history(self, faq_env):
        faq_env.update("A", {
            "category": "GENERAL",
            "question": "수정",
            "answer": "수정 답변",
        })
        history = faq_env.get_history("A")
        assert len(history) == 1
        assert history[0]["action"] == "update"
        assert history[0]["old_data"]["question"] == "보세전시장이 무엇인가요?"

    def test_delete_logs_history(self, faq_env):
        faq_env.delete("A")
        history = faq_env.get_history("A")
        assert len(history) == 1
        assert history[0]["action"] == "delete"

    def test_multiple_changes_ordered(self, faq_env):
        faq_env.update("A", {
            "category": "GENERAL",
            "question": "수정1",
            "answer": "답변1",
        })
        faq_env.update("A", {
            "category": "GENERAL",
            "question": "수정2",
            "answer": "답변2",
        })
        history = faq_env.get_history("A")
        assert len(history) == 2
        # Most recent first
        assert history[0]["new_data"]["question"] == "수정2"

    def test_last_modified_after_update(self, faq_env):
        faq_env.update("A", {
            "category": "GENERAL",
            "question": "수정",
            "answer": "답변",
        })
        item = faq_env.get("A")
        assert item["last_modified"] is not None


# --- API Endpoint Tests ---

class TestFAQAPIEndpoints:
    """Test the web API endpoints for FAQ management."""

    @pytest.fixture
    def client(self):
        os.environ["ADMIN_AUTH_DISABLED"] = "true"
        from web_server import app
        app.config["TESTING"] = True
        # Backup faq.json before tests
        faq_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "faq.json")
        with open(faq_path, "r", encoding="utf-8") as f:
            faq_backup = f.read()
        with app.test_client() as client:
            yield client
        # Restore faq.json after tests
        with open(faq_path, "w", encoding="utf-8") as f:
            f.write(faq_backup)
        os.environ.pop("ADMIN_AUTH_DISABLED", None)

    def test_get_faq_list(self, client):
        res = client.get("/api/admin/faq")
        assert res.status_code == 200
        data = res.get_json()
        assert "items" in data
        assert "count" in data
        assert data["count"] >= 1

    def test_get_faq_list_filter_category(self, client):
        res = client.get("/api/admin/faq?category=GENERAL")
        assert res.status_code == 200
        data = res.get_json()
        for item in data["items"]:
            assert item["category"] == "GENERAL"

    def test_get_faq_list_search(self, client):
        res = client.get("/api/admin/faq?search=%EB%B3%B4%EC%84%B8")
        assert res.status_code == 200
        data = res.get_json()
        assert data["count"] >= 1

    def test_create_faq(self, client):
        payload = {
            "category": "CONTACT",
            "question": "API 테스트 질문",
            "answer": "API 테스트 답변",
            "keywords": ["테스트"],
        }
        res = client.post("/api/admin/faq", json=payload)
        assert res.status_code == 201
        data = res.get_json()
        assert data["success"] is True
        assert "item" in data

    def test_create_faq_missing_field(self, client):
        payload = {"category": "GENERAL"}
        res = client.post("/api/admin/faq", json=payload)
        assert res.status_code == 400

    def test_update_faq(self, client):
        payload = {
            "category": "GENERAL",
            "question": "수정된 API 질문",
            "answer": "수정된 API 답변",
        }
        res = client.put("/api/admin/faq/A", json=payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_update_nonexistent(self, client):
        payload = {
            "category": "GENERAL",
            "question": "없는 항목",
            "answer": "답변",
        }
        res = client.put("/api/admin/faq/NONEXISTENT", json=payload)
        assert res.status_code == 404

    def test_delete_faq(self, client):
        # First create one to delete
        client.post("/api/admin/faq", json={
            "id": "DEL_TEST",
            "category": "GENERAL",
            "question": "삭제 테스트",
            "answer": "삭제될 항목",
        })
        res = client.delete("/api/admin/faq/DEL_TEST")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_delete_nonexistent(self, client):
        res = client.delete("/api/admin/faq/NONEXISTENT")
        assert res.status_code == 404

    def test_faq_history_endpoint(self, client):
        res = client.get("/api/admin/faq/A/history")
        assert res.status_code == 200
        data = res.get_json()
        assert "history" in data
        assert "count" in data

    def test_faq_manager_page(self, client):
        res = client.get("/admin/faq")
        assert res.status_code == 200

"""멀티 테넌트 관리 테스트."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tenant_manager import TenantManager


@pytest.fixture
def temp_dir():
    """테스트용 임시 디렉토리를 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def tenant_mgr(temp_dir):
    """테스트용 TenantManager 인스턴스를 반환한다."""
    db_path = os.path.join(temp_dir, "tenants.db")
    data_dir = os.path.join(temp_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    # 기본 faq.json 생성 (default 테넌트용)
    default_faq_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "faq.json"
    )
    mgr = TenantManager(db_path=db_path, data_dir=data_dir)
    return mgr


class TestDefaultTenant:
    """기본 테넌트 관련 테스트."""

    def test_default_tenant_exists(self, tenant_mgr):
        """기본 테넌트가 자동 생성된다."""
        tenant = tenant_mgr.get_tenant("default")
        assert tenant is not None
        assert tenant["id"] == "default"
        assert tenant["name"] == "기본 보세전시장"
        assert tenant["active"] is True

    def test_default_tenant_in_list(self, tenant_mgr):
        """기본 테넌트가 목록에 포함된다."""
        tenants = tenant_mgr.list_tenants()
        ids = [t["id"] for t in tenants]
        assert "default" in ids

    def test_default_tenant_cannot_be_deleted(self, tenant_mgr):
        """기본 테넌트는 삭제할 수 없다."""
        with pytest.raises(ValueError, match="기본 테넌트"):
            tenant_mgr.delete_tenant("default")

    def test_default_tenant_fallback(self, tenant_mgr):
        """존재하지 않는 테넌트 조회 시 None을 반환한다."""
        tenant = tenant_mgr.get_tenant("nonexistent")
        assert tenant is None


class TestCRUDOperations:
    """CRUD 연산 테스트."""

    def test_create_tenant(self, tenant_mgr):
        """테넌트를 생성할 수 있다."""
        tenant = tenant_mgr.create_tenant(
            "hall_a", "A 전시장", config={"region": "서울"}
        )
        assert tenant["id"] == "hall_a"
        assert tenant["name"] == "A 전시장"
        assert tenant["config"]["region"] == "서울"
        assert tenant["active"] is True

    def test_create_duplicate_tenant_fails(self, tenant_mgr):
        """중복 테넌트 생성 시 에러가 발생한다."""
        tenant_mgr.create_tenant("hall_b", "B 전시장")
        with pytest.raises(ValueError, match="이미 존재"):
            tenant_mgr.create_tenant("hall_b", "B 전시장 중복")

    def test_create_tenant_invalid_id(self, tenant_mgr):
        """빈 문자열 tenant_id로 생성 시 에러가 발생한다."""
        with pytest.raises(ValueError):
            tenant_mgr.create_tenant("", "빈 ID")

    def test_create_tenant_invalid_name(self, tenant_mgr):
        """빈 문자열 name으로 생성 시 에러가 발생한다."""
        with pytest.raises(ValueError):
            tenant_mgr.create_tenant("valid_id", "")

    def test_get_tenant(self, tenant_mgr):
        """테넌트를 조회할 수 있다."""
        tenant_mgr.create_tenant("hall_c", "C 전시장")
        tenant = tenant_mgr.get_tenant("hall_c")
        assert tenant is not None
        assert tenant["name"] == "C 전시장"

    def test_get_nonexistent_tenant(self, tenant_mgr):
        """존재하지 않는 테넌트 조회 시 None을 반환한다."""
        assert tenant_mgr.get_tenant("no_such_tenant") is None

    def test_list_tenants(self, tenant_mgr):
        """테넌트 목록을 조회할 수 있다."""
        tenant_mgr.create_tenant("hall_d", "D 전시장")
        tenant_mgr.create_tenant("hall_e", "E 전시장")
        tenants = tenant_mgr.list_tenants()
        ids = [t["id"] for t in tenants]
        assert "default" in ids
        assert "hall_d" in ids
        assert "hall_e" in ids

    def test_update_tenant_name(self, tenant_mgr):
        """테넌트 이름을 업데이트할 수 있다."""
        tenant_mgr.create_tenant("hall_f", "F 전시장")
        updated = tenant_mgr.update_tenant("hall_f", {"name": "F 전시장 (수정)"})
        assert updated["name"] == "F 전시장 (수정)"

    def test_update_tenant_config(self, tenant_mgr):
        """테넌트 설정을 업데이트할 수 있다."""
        tenant_mgr.create_tenant("hall_g", "G 전시장")
        updated = tenant_mgr.update_tenant(
            "hall_g", {"config": {"region": "부산", "capacity": 500}}
        )
        assert updated["config"]["region"] == "부산"
        assert updated["config"]["capacity"] == 500

    def test_update_tenant_active(self, tenant_mgr):
        """테넌트 활성 상태를 변경할 수 있다."""
        tenant_mgr.create_tenant("hall_h", "H 전시장")
        updated = tenant_mgr.update_tenant("hall_h", {"active": False})
        assert updated["active"] is False

        updated = tenant_mgr.update_tenant("hall_h", {"active": True})
        assert updated["active"] is True

    def test_update_nonexistent_tenant_fails(self, tenant_mgr):
        """존재하지 않는 테넌트 업데이트 시 에러가 발생한다."""
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            tenant_mgr.update_tenant("no_such", {"name": "test"})

    def test_delete_tenant(self, tenant_mgr):
        """테넌트를 삭제할 수 있다."""
        tenant_mgr.create_tenant("hall_i", "I 전시장")
        assert tenant_mgr.get_tenant("hall_i") is not None
        tenant_mgr.delete_tenant("hall_i")
        assert tenant_mgr.get_tenant("hall_i") is None

    def test_delete_nonexistent_tenant_fails(self, tenant_mgr):
        """존재하지 않는 테넌트 삭제 시 에러가 발생한다."""
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            tenant_mgr.delete_tenant("no_such")


class TestTenantIsolation:
    """테넌트 격리 테스트."""

    def test_separate_faq_files(self, tenant_mgr, temp_dir):
        """각 테넌트는 별도의 FAQ 파일을 가진다."""
        tenant_mgr.create_tenant("hall_x", "X 전시장")
        tenant_mgr.create_tenant("hall_y", "Y 전시장")

        faq_x_path = tenant_mgr._tenant_faq_path("hall_x")
        faq_y_path = tenant_mgr._tenant_faq_path("hall_y")

        assert faq_x_path != faq_y_path
        assert os.path.exists(faq_x_path)
        assert os.path.exists(faq_y_path)

    def test_separate_log_db_paths(self, tenant_mgr):
        """각 테넌트는 별도의 로그 DB 경로를 가진다."""
        log_default = tenant_mgr._tenant_log_db_path("default")
        log_custom = tenant_mgr._tenant_log_db_path("custom_hall")

        assert log_default != log_custom
        assert "chat_logs.db" in log_default
        assert "chat_logs_custom_hall.db" in log_custom

    def test_tenant_faq_independent(self, tenant_mgr, temp_dir):
        """테넌트 FAQ 데이터가 독립적이다."""
        tenant_mgr.create_tenant("hall_p", "P 전시장")
        tenant_mgr.create_tenant("hall_q", "Q 전시장")

        # P 전시장에 FAQ 항목 추가
        faq_p_path = tenant_mgr._tenant_faq_path("hall_p")
        faq_p = {"faq_version": "1.0.0", "last_updated": "2026-01-01", "items": [
            {"id": "P1", "question": "P 전시장 질문", "answer": "P 답변"}
        ]}
        with open(faq_p_path, "w", encoding="utf-8") as f:
            json.dump(faq_p, f, ensure_ascii=False)

        # P의 FAQ에는 항목이 있지만 Q에는 없다
        p_faq = tenant_mgr.get_tenant_faq("hall_p")
        q_faq = tenant_mgr.get_tenant_faq("hall_q")

        assert len(p_faq["items"]) == 1
        assert len(q_faq["items"]) == 0

    def test_delete_removes_faq_file(self, tenant_mgr):
        """테넌트 삭제 시 FAQ 파일도 삭제된다."""
        tenant_mgr.create_tenant("hall_del", "삭제 대상")
        faq_path = tenant_mgr._tenant_faq_path("hall_del")
        assert os.path.exists(faq_path)

        tenant_mgr.delete_tenant("hall_del")
        assert not os.path.exists(faq_path)


class TestTenantFAQManagement:
    """테넌트 FAQ 관리 테스트."""

    def test_get_default_tenant_faq(self, tenant_mgr):
        """기본 테넌트 FAQ를 조회할 수 있다."""
        faq = tenant_mgr.get_tenant_faq("default")
        assert "items" in faq
        # 기본 테넌트는 기존 faq.json을 사용
        assert len(faq["items"]) > 0

    def test_get_new_tenant_faq_empty(self, tenant_mgr):
        """새 테넌트 FAQ는 빈 목록이다."""
        tenant_mgr.create_tenant("hall_new", "신규 전시장")
        faq = tenant_mgr.get_tenant_faq("hall_new")
        assert faq["items"] == []

    def test_get_nonexistent_tenant_faq_fails(self, tenant_mgr):
        """존재하지 않는 테넌트 FAQ 조회 시 에러가 발생한다."""
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            tenant_mgr.get_tenant_faq("no_such")

    def test_get_tenant_config(self, tenant_mgr):
        """테넌트 설정을 조회할 수 있다."""
        tenant_mgr.create_tenant("hall_cfg", "설정 테스트", config={"key": "value"})
        cfg = tenant_mgr.get_tenant_config("hall_cfg")
        assert cfg["key"] == "value"

    def test_get_default_tenant_config(self, tenant_mgr):
        """기본 테넌트 설정을 조회할 수 있다."""
        cfg = tenant_mgr.get_tenant_config("default")
        assert isinstance(cfg, dict)

    def test_get_nonexistent_tenant_config_fails(self, tenant_mgr):
        """존재하지 않는 테넌트 설정 조회 시 에러가 발생한다."""
        with pytest.raises(ValueError, match="찾을 수 없습니다"):
            tenant_mgr.get_tenant_config("no_such")


class TestTenantAPIEndpoints:
    """웹 API 테넌트 엔드포인트 테스트."""

    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_list_tenants(self, client):
        """GET /api/admin/tenants - 테넌트 목록을 반환한다."""
        res = client.get("/api/admin/tenants")
        assert res.status_code == 200
        data = res.get_json()
        assert "tenants" in data
        assert data["count"] >= 1
        ids = [t["id"] for t in data["tenants"]]
        assert "default" in ids

    def test_create_tenant_api(self, client):
        """POST /api/admin/tenants - 테넌트를 생성한다."""
        import uuid
        tid = f"api_test_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/admin/tenants",
            json={"tenant_id": tid, "name": "API 테스트 전시장"},
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["success"] is True
        assert data["tenant"]["id"] == tid

    def test_create_tenant_missing_fields(self, client):
        """POST /api/admin/tenants - 필수 필드 누락 시 400 반환."""
        res = client.post("/api/admin/tenants", json={"name": "이름만"})
        assert res.status_code == 400

    def test_update_tenant_api(self, client):
        """PUT /api/admin/tenants/<id> - 테넌트를 업데이트한다."""
        import uuid
        tid = f"update_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/admin/tenants",
            json={"tenant_id": tid, "name": "업데이트 전"},
        )
        res = client.put(
            f"/api/admin/tenants/{tid}",
            json={"name": "업데이트 후"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["tenant"]["name"] == "업데이트 후"

    def test_update_nonexistent_tenant_api(self, client):
        """PUT /api/admin/tenants/<id> - 존재하지 않는 테넌트 404 반환."""
        res = client.put(
            "/api/admin/tenants/no_such_tenant",
            json={"name": "test"},
        )
        assert res.status_code == 404

    def test_delete_tenant_api(self, client):
        """DELETE /api/admin/tenants/<id> - 테넌트를 삭제한다."""
        import uuid
        tid = f"delete_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/admin/tenants",
            json={"tenant_id": tid, "name": "삭제 대상"},
        )
        res = client.delete(f"/api/admin/tenants/{tid}")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

    def test_delete_default_tenant_api(self, client):
        """DELETE /api/admin/tenants/default - 기본 테넌트 삭제 시 400 반환."""
        res = client.delete("/api/admin/tenants/default")
        assert res.status_code == 400

    def test_get_tenant_faq_api(self, client):
        """GET /api/admin/tenants/<id>/faq - 테넌트 FAQ를 반환한다."""
        res = client.get("/api/admin/tenants/default/faq")
        assert res.status_code == 200
        data = res.get_json()
        assert "items" in data

    def test_get_nonexistent_tenant_faq_api(self, client):
        """GET /api/admin/tenants/<id>/faq - 존재하지 않는 테넌트 404 반환."""
        res = client.get("/api/admin/tenants/no_such/faq")
        assert res.status_code == 404

    def test_chat_with_default_tenant(self, client):
        """POST /api/chat - 기본 테넌트로 채팅한다."""
        res = client.post("/api/chat", json={"query": "보세전시장이 무엇인가요?"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["tenant_id"] == "default"

    def test_chat_with_tenant_header(self, client):
        """POST /api/chat - X-Tenant-Id 헤더로 테넌트를 지정한다."""
        # 기본 테넌트로 질문
        res = client.post(
            "/api/chat",
            json={"query": "보세전시장이 무엇인가요?"},
            headers={"X-Tenant-Id": "default"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["tenant_id"] == "default"

    def test_chat_with_invalid_tenant(self, client):
        """POST /api/chat - 존재하지 않는 테넌트로 질문 시 404 반환."""
        res = client.post(
            "/api/chat",
            json={"query": "테스트"},
            headers={"X-Tenant-Id": "nonexistent_hall"},
        )
        assert res.status_code == 404

    def test_chat_with_inactive_tenant(self, client):
        """POST /api/chat - 비활성 테넌트로 질문 시 403 반환."""
        import uuid
        tid = f"inactive_{uuid.uuid4().hex[:8]}"
        # 테넌트 생성 후 비활성화
        client.post(
            "/api/admin/tenants",
            json={"tenant_id": tid, "name": "비활성 전시장"},
        )
        client.put(
            f"/api/admin/tenants/{tid}",
            json={"active": False},
        )
        res = client.post(
            "/api/chat",
            json={"query": "테스트"},
            headers={"X-Tenant-Id": tid},
        )
        assert res.status_code == 403

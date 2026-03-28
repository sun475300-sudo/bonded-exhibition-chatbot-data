"""OpenAPI 명세 및 Swagger UI 테스트."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestSwaggerUI:
    """Swagger UI 페이지 테스트."""

    def test_docs_returns_200(self, client):
        """GET /docs 가 200과 HTML을 반환한다."""
        res = client.get("/docs")
        assert res.status_code == 200
        assert b"<!DOCTYPE html>" in res.data or b"<html" in res.data
        assert b"swagger-ui" in res.data

    def test_swagger_alias_returns_200(self, client):
        """GET /swagger 도 동일하게 200을 반환한다."""
        res = client.get("/swagger")
        assert res.status_code == 200
        assert b"swagger-ui" in res.data


class TestOpenAPISpec:
    """OpenAPI 명세 파일 테스트."""

    def test_openapi_yaml_returns_200(self, client):
        """GET /api/openapi.yaml 가 200과 유효한 YAML을 반환한다."""
        res = client.get("/api/openapi.yaml")
        assert res.status_code == 200
        # 응답이 유효한 YAML인지 확인
        spec = yaml.safe_load(res.data)
        assert spec is not None
        assert isinstance(spec, dict)

    def test_openapi_version(self, client):
        """OpenAPI 버전이 3.0.3인지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert spec["openapi"] == "3.0.3"

    def test_info_section(self, client):
        """info 섹션이 올바른지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert "info" in spec
        assert "title" in spec["info"]
        assert "version" in spec["info"]
        assert "description" in spec["info"]


class TestOpenAPIPaths:
    """OpenAPI 명세에 모든 경로가 포함되어 있는지 테스트."""

    EXPECTED_PATHS = [
        "/api/chat",
        "/api/faq",
        "/api/autocomplete",
        "/api/export",
        "/api/session/new",
        "/api/session/{session_id}",
        "/api/session/{session_id}/export",
        "/api/config",
        "/api/health",
        "/api/auth/login",
        "/api/auth/me",
        "/api/kakao/chat",
        "/api/kakao/faq",
        "/api/feedback",
        "/api/related/{faq_id}",
        "/api/admin/stats",
        "/api/admin/logs",
        "/api/admin/unmatched",
        "/api/admin/recommendations",
        "/api/admin/feedback",
        "/api/admin/analytics",
        "/api/admin/report",
        "/api/admin/faq-pipeline",
        "/api/admin/faq-pipeline/approve",
        "/api/admin/faq-pipeline/reject",
        "/api/admin/monitor",
        "/api/admin/quality",
        "/api/admin/realtime",
        "/api/admin/faq-quality",
        "/api/admin/satisfaction",
        "/api/admin/cache/clear",
        "/api/admin/law-updates",
        "/api/admin/law-updates/check",
        "/api/admin/law-updates/acknowledge",
        "/metrics",
    ]

    def test_all_paths_present(self, client):
        """명세에 모든 예상 경로가 포함되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        paths = list(spec.get("paths", {}).keys())

        missing = [p for p in self.EXPECTED_PATHS if p not in paths]
        assert missing == [], f"명세에 누락된 경로: {missing}"

    def test_chat_endpoint_has_post(self, client):
        """/api/chat 에 POST 메서드가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert "post" in spec["paths"]["/api/chat"]

    def test_faq_endpoint_has_get(self, client):
        """/api/faq 에 GET 메서드가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert "get" in spec["paths"]["/api/faq"]

    def test_health_endpoint_has_get(self, client):
        """/api/health 에 GET 메서드가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert "get" in spec["paths"]["/api/health"]

    def test_metrics_endpoint_has_get(self, client):
        """/metrics 에 GET 메서드가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        assert "get" in spec["paths"]["/metrics"]


class TestOpenAPISchemas:
    """OpenAPI 명세에 스키마 정의가 있는지 테스트."""

    EXPECTED_SCHEMAS = [
        "Error",
        "ChatRequest",
        "ChatResponse",
        "FAQItem",
        "FAQListResponse",
        "ConfigResponse",
        "HealthResponse",
        "LoginRequest",
        "LoginResponse",
        "UserInfo",
        "FeedbackRequest",
        "FeedbackResponse",
        "ExportRequest",
        "KakaoSkillRequest",
        "KakaoSkillResponse",
        "SessionInfo",
        "AutocompleteResponse",
    ]

    def test_schemas_exist(self, client):
        """컴포넌트 스키마에 모든 예상 스키마가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        schemas = list(spec.get("components", {}).get("schemas", {}).keys())

        missing = [s for s in self.EXPECTED_SCHEMAS if s not in schemas]
        assert missing == [], f"명세에 누락된 스키마: {missing}"

    def test_security_scheme_exists(self, client):
        """BearerAuth 보안 스키마가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        security_schemes = spec.get("components", {}).get("securitySchemes", {})
        assert "BearerAuth" in security_schemes
        assert security_schemes["BearerAuth"]["type"] == "http"
        assert security_schemes["BearerAuth"]["scheme"] == "bearer"


class TestOpenAPITags:
    """OpenAPI 명세에 태그가 올바르게 정의되어 있는지 테스트."""

    EXPECTED_TAGS = ["챗봇", "세션", "FAQ", "인증", "카카오톡", "관리자", "모니터링"]

    def test_all_tags_defined(self, client):
        """모든 예상 태그가 정의되어 있는지 확인한다."""
        res = client.get("/api/openapi.yaml")
        spec = yaml.safe_load(res.data)
        tag_names = [t["name"] for t in spec.get("tags", [])]

        missing = [t for t in self.EXPECTED_TAGS if t not in tag_names]
        assert missing == [], f"명세에 누락된 태그: {missing}"

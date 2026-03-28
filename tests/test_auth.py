"""JWT authentication tests."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.auth import JWTAuth, hash_password, verify_password, authenticate_user


class TestPasswordHashing:
    def test_hash_password_format(self):
        h = hash_password("test123")
        parts = h.split(":")
        assert len(parts) == 3
        assert parts[0] == "sha256"
        assert len(parts[1]) == 32  # hex salt
        assert len(parts[2]) == 64  # hex sha256

    def test_hash_password_unique_salts(self):
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # Different salts produce different hashes

    def test_verify_password_correct(self):
        h = hash_password("mypassword")
        assert verify_password("mypassword", h) is True

    def test_verify_password_wrong(self):
        h = hash_password("mypassword")
        assert verify_password("wrongpassword", h) is False

    def test_verify_password_invalid_hash(self):
        assert verify_password("test", "invalid_hash") is False
        assert verify_password("test", "") is False
        assert verify_password("test", "sha256:short") is False

    def test_verify_password_tampered_hash(self):
        h = hash_password("mypassword")
        parts = h.split(":")
        tampered = f"{parts[0]}:{parts[1]}:{'0' * 64}"
        assert verify_password("mypassword", tampered) is False


class TestJWTTokenGeneration:
    def setup_method(self):
        self.auth = JWTAuth(secret_key="test-secret-key")

    def test_generate_token_returns_string(self):
        token = self.auth.generate_token("admin")
        assert isinstance(token, str)
        assert token.count(".") == 2

    def test_generate_token_with_role(self):
        token = self.auth.generate_token("admin", role="superadmin")
        payload = self.auth.verify_token(token)
        assert payload is not None
        assert payload["role"] == "superadmin"

    def test_generate_token_default_role(self):
        token = self.auth.generate_token("admin")
        payload = self.auth.verify_token(token)
        assert payload["role"] == "admin"


class TestJWTTokenVerification:
    def setup_method(self):
        self.auth = JWTAuth(secret_key="test-secret-key")

    def test_verify_valid_token(self):
        token = self.auth.generate_token("testuser", role="admin")
        payload = self.auth.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "testuser"
        assert payload["role"] == "admin"
        assert "iat" in payload
        assert "exp" in payload

    def test_verify_expired_token(self):
        token = self.auth.generate_token("testuser", expires_hours=0)
        # Token with 0 hours expiry should expire immediately
        # We need to wait a tiny bit or set it in the past
        # Actually expires_hours=0 means exp = iat, so time.time() > exp
        time.sleep(0.1)
        payload = self.auth.verify_token(token)
        assert payload is None

    def test_verify_invalid_token(self):
        assert self.auth.verify_token("invalid.token.here") is None
        assert self.auth.verify_token("not-a-jwt") is None
        assert self.auth.verify_token("") is None

    def test_verify_tampered_token(self):
        token = self.auth.generate_token("admin")
        parts = token.split(".")
        # Tamper with the payload
        tampered = f"{parts[0]}.dGVzdA.{parts[2]}"
        assert self.auth.verify_token(tampered) is None

    def test_verify_wrong_secret(self):
        token = self.auth.generate_token("admin")
        other_auth = JWTAuth(secret_key="different-secret")
        assert other_auth.verify_token(token) is None

    def test_token_payload_fields(self):
        token = self.auth.generate_token("admin", role="editor", expires_hours=2)
        payload = self.auth.verify_token(token)
        assert payload["sub"] == "admin"
        assert payload["role"] == "editor"
        assert payload["exp"] - payload["iat"] == 2 * 3600


class TestAuthenticateUser:
    def test_authenticate_default_admin(self):
        # Clear ADMIN_USERS to use default
        old_val = os.environ.pop("ADMIN_USERS", None)
        try:
            user = authenticate_user("admin", "admin123")
            assert user is not None
            assert user["username"] == "admin"
            assert user["role"] == "admin"
        finally:
            if old_val is not None:
                os.environ["ADMIN_USERS"] = old_val

    def test_authenticate_wrong_password(self):
        old_val = os.environ.pop("ADMIN_USERS", None)
        try:
            user = authenticate_user("admin", "wrongpassword")
            assert user is None
        finally:
            if old_val is not None:
                os.environ["ADMIN_USERS"] = old_val

    def test_authenticate_unknown_user(self):
        old_val = os.environ.pop("ADMIN_USERS", None)
        try:
            user = authenticate_user("unknown", "admin123")
            assert user is None
        finally:
            if old_val is not None:
                os.environ["ADMIN_USERS"] = old_val


class TestLoginEndpoint:
    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        app.config["AUTH_TESTING"] = True  # Force auth enforcement
        old_auth = os.environ.pop("ADMIN_AUTH_DISABLED", None)
        old_testing = os.environ.pop("TESTING", None)
        with app.test_client() as client:
            yield client
        app.config["AUTH_TESTING"] = False
        if old_auth is not None:
            os.environ["ADMIN_AUTH_DISABLED"] = old_auth
        if old_testing is not None:
            os.environ["TESTING"] = old_testing

    def test_login_success(self, client):
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert "token" in data
        assert data["expires_in"] == 86400

    def test_login_wrong_password(self, client):
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong",
        })
        assert res.status_code == 401
        data = res.get_json()
        assert "error" in data

    def test_login_missing_fields(self, client):
        res = client.post("/api/auth/login", json={"username": "admin"})
        assert res.status_code == 400

    def test_login_no_body(self, client):
        res = client.post("/api/auth/login")
        assert res.status_code == 400


class TestProtectedEndpoints:
    @pytest.fixture(autouse=True)
    def _clear_auth_env(self):
        """Ensure auth env vars are cleared for protected endpoint tests."""
        old_auth = os.environ.pop("ADMIN_AUTH_DISABLED", None)
        old_testing = os.environ.pop("TESTING", None)
        yield
        if old_auth is not None:
            os.environ["ADMIN_AUTH_DISABLED"] = old_auth
        if old_testing is not None:
            os.environ["TESTING"] = old_testing

    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        app.config["AUTH_TESTING"] = True  # Force auth enforcement
        os.environ.pop("ADMIN_AUTH_DISABLED", None)
        os.environ.pop("TESTING", None)
        with app.test_client() as client:
            yield client
        app.config["AUTH_TESTING"] = False

    def _get_token(self, client):
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        return res.get_json()["token"]

    def test_admin_stats_without_token(self, client):
        res = client.get("/api/admin/stats")
        assert res.status_code == 401

    def test_admin_stats_with_valid_token(self, client):
        token = self._get_token(client)
        res = client.get("/api/admin/stats", headers={
            "Authorization": f"Bearer {token}",
        })
        assert res.status_code == 200

    def test_admin_endpoint_with_invalid_token(self, client):
        res = client.get("/api/admin/stats", headers={
            "Authorization": "Bearer invalid.token.here",
        })
        assert res.status_code == 401

    def test_admin_endpoint_no_bearer_prefix(self, client):
        token = self._get_token(client)
        res = client.get("/api/admin/stats", headers={
            "Authorization": token,
        })
        assert res.status_code == 401

    def test_auth_me_with_token(self, client):
        token = self._get_token(client)
        res = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_auth_me_without_token(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401

    def test_expired_token_rejected(self, client):
        from src.auth import JWTAuth
        auth = JWTAuth()
        token = auth.generate_token("admin", expires_hours=0)
        time.sleep(0.1)
        res = client.get("/api/admin/stats", headers={
            "Authorization": f"Bearer {token}",
        })
        assert res.status_code == 401


class TestAuthBypass:
    """Test that TESTING=True and ADMIN_AUTH_DISABLED=true skip auth."""

    @pytest.fixture
    def client_with_testing(self):
        from web_server import app
        app.config["TESTING"] = True
        os.environ["TESTING"] = "true"
        with app.test_client() as client:
            yield client
        os.environ.pop("TESTING", None)

    @pytest.fixture
    def client_with_auth_disabled(self):
        from web_server import app
        app.config["TESTING"] = True
        os.environ["ADMIN_AUTH_DISABLED"] = "true"
        with app.test_client() as client:
            yield client
        os.environ.pop("ADMIN_AUTH_DISABLED", None)

    def test_testing_env_bypasses_auth(self, client_with_testing):
        res = client_with_testing.get("/api/admin/stats")
        assert res.status_code == 200

    def test_auth_disabled_bypasses_auth(self, client_with_auth_disabled):
        res = client_with_auth_disabled.get("/api/admin/stats")
        assert res.status_code == 200


class TestLoginPage:
    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_login_page_returns_html(self, client):
        res = client.get("/login")
        assert res.status_code == 200
        assert b"<!DOCTYPE html>" in res.data

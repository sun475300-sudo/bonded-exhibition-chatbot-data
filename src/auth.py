"""JWT-based admin authentication module.

Pure Python JWT implementation using hmac+hashlib for HS256 signing.
No external JWT libraries required.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import wraps

from flask import request, jsonify


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with padding restoration."""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def hash_password(password: str) -> str:
    """Hash a password with SHA256 + random salt.

    Returns a string in the format: sha256:<hex_salt>:<hex_hash>
    """
    salt = secrets.token_hex(16)
    hash_val = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"sha256:{salt}:{hash_val}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash.

    Args:
        password: The plaintext password to check.
        password_hash: The stored hash in format sha256:<salt>:<hash>.

    Returns:
        True if the password matches.
    """
    try:
        parts = password_hash.split(":")
        if len(parts) != 3 or parts[0] != "sha256":
            return False
        salt = parts[1]
        expected_hash = parts[2]
        actual_hash = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False


# Default admin account (used when ADMIN_USERS env var is not set)
_DEFAULT_ADMIN_HASH = hash_password("admin123")
_DEFAULT_ADMIN_USERS = [
    {"username": "admin", "password_hash": _DEFAULT_ADMIN_HASH, "role": "admin"}
]


def _get_admin_users() -> list:
    """Load admin users from ADMIN_USERS env var or use defaults."""
    env_val = os.environ.get("ADMIN_USERS")
    if env_val:
        try:
            return json.loads(env_val)
        except (json.JSONDecodeError, TypeError):
            return _DEFAULT_ADMIN_USERS
    return _DEFAULT_ADMIN_USERS


def _get_secret_key() -> str:
    """Get the JWT secret key from env or generate a default."""
    return os.environ.get("JWT_SECRET_KEY", "bonded-exhibition-chatbot-secret-key")


class JWTAuth:
    """JWT authentication handler using HS256."""

    def __init__(self, secret_key: str | None = None):
        self.secret_key = secret_key or _get_secret_key()

    def generate_token(self, username: str, role: str = "admin", expires_hours: int = 24) -> str:
        """Generate a JWT token.

        Args:
            username: The username to encode in the token.
            role: The user role (default: "admin").
            expires_hours: Token expiration in hours (default: 24).

        Returns:
            JWT token string.
        """
        header = {"alg": "HS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "sub": username,
            "role": role,
            "iat": now,
            "exp": now + (expires_hours * 3600),
        }

        header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature_b64 = _b64url_encode(signature)

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def verify_token(self, token: str) -> dict | None:
        """Verify a JWT token and return its payload.

        Args:
            token: The JWT token string.

        Returns:
            Payload dict if valid, None if invalid or expired.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, payload_b64, signature_b64 = parts

            # Verify signature
            signing_input = f"{header_b64}.{payload_b64}"
            expected_sig = hmac.new(
                self.secret_key.encode("utf-8"),
                signing_input.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            actual_sig = _b64url_decode(signature_b64)

            if not hmac.compare_digest(expected_sig, actual_sig):
                return None

            # Decode payload
            payload = json.loads(_b64url_decode(payload_b64))

            # Check expiration
            exp = payload.get("exp", 0)
            if time.time() > exp:
                return None

            return payload
        except Exception:
            return None

    def require_auth(self, role: str | None = None):
        """Flask decorator that checks Authorization: Bearer header.

        Args:
            role: If set, require this role in the token payload.

        Returns:
            Decorator function.
        """
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                # Skip auth when ADMIN_AUTH_DISABLED or TESTING
                if os.environ.get("ADMIN_AUTH_DISABLED", "").lower() == "true":
                    return f(*args, **kwargs)
                if os.environ.get("TESTING", "").lower() == "true":
                    return f(*args, **kwargs)
                # Also check Flask's TESTING config for test client usage
                try:
                    from flask import current_app
                    if current_app.config.get("TESTING") and not current_app.config.get("AUTH_TESTING"):
                        return f(*args, **kwargs)
                except RuntimeError:
                    pass

                auth_header = request.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    return jsonify({"error": "Authorization header required"}), 401

                token = auth_header[7:]  # Strip "Bearer "
                payload = self.verify_token(token)
                if payload is None:
                    return jsonify({"error": "Invalid or expired token"}), 401

                if role and payload.get("role") != role:
                    return jsonify({"error": "Insufficient permissions"}), 403

                # Attach user info to request
                request.jwt_payload = payload
                return f(*args, **kwargs)
            return decorated_function
        return decorator


def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user against the admin user list.

    Args:
        username: The username.
        password: The plaintext password.

    Returns:
        User dict (without password_hash) if authenticated, None otherwise.
    """
    users = _get_admin_users()
    for user in users:
        if user.get("username") == username:
            if verify_password(password, user.get("password_hash", "")):
                return {"username": user["username"], "role": user.get("role", "admin")}
    return None

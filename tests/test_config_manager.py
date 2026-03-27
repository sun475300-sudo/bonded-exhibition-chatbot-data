"""ConfigManager 테스트."""

import json
import os
import tempfile

import pytest

from src.config_manager import ConfigManager


class TestConfigManagerDefaults:
    """기본값 테스트."""

    def test_default_port(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_PORT") == 8080

    def test_default_host(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_HOST") == "0.0.0.0"

    def test_default_debug(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is False

    def test_default_db_path(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DB_PATH") == "logs/chat_logs.db"

    def test_default_log_level(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_LOG_LEVEL") == "INFO"

    def test_default_api_keys(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_API_KEYS") == ""

    def test_unknown_key_returns_default(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("NONEXISTENT_KEY", "fallback") == "fallback"

    def test_unknown_key_returns_none(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("NONEXISTENT_KEY") is None


class TestConfigManagerEnvVars:
    """환경변수 우선순위 테스트."""

    def test_env_var_port(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_PORT", "9090")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_PORT") == 9090

    def test_env_var_host(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_HOST", "localhost")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_HOST") == "localhost"

    def test_env_var_debug_true(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_DEBUG", "true")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is True

    def test_env_var_debug_false(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_DEBUG", "false")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is False

    def test_env_var_debug_1(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_DEBUG", "1")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is True

    def test_env_var_debug_yes(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_DEBUG", "yes")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is True

    def test_env_var_debug_on(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_DEBUG", "on")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_DEBUG") is True

    def test_env_var_invalid_port(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_PORT", "not_a_number")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_PORT") == 8080  # 기본값으로 폴백

    def test_env_var_log_level(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_LOG_LEVEL", "DEBUG")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_LOG_LEVEL") == "DEBUG"

    def test_env_var_api_keys(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_API_KEYS", "key1,key2,key3")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CHATBOT_API_KEYS") == "key1,key2,key3"

    def test_unknown_env_var_via_get(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_VAR", "custom_value")
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        assert cm.get("CUSTOM_VAR") == "custom_value"


class TestConfigManagerFileConfig:
    """config 파일 폴백 테스트."""

    def test_file_config_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"CHATBOT_PORT": 3000, "CHATBOT_HOST": "192.168.1.1"}
            with open(os.path.join(tmpdir, "chatbot_config.json"), "w") as f:
                json.dump(config, f)

            cm = ConfigManager(config_dir=tmpdir)
            cm.load()
            assert cm.get("CHATBOT_PORT") == 3000
            assert cm.get("CHATBOT_HOST") == "192.168.1.1"

    def test_env_overrides_file(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_PORT", "7777")
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"CHATBOT_PORT": 3000}
            with open(os.path.join(tmpdir, "chatbot_config.json"), "w") as f:
                json.dump(config, f)

            cm = ConfigManager(config_dir=tmpdir)
            cm.load()
            assert cm.get("CHATBOT_PORT") == 7777

    def test_invalid_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "chatbot_config.json"), "w") as f:
                f.write("{invalid json")

            cm = ConfigManager(config_dir=tmpdir)
            cm.load()
            assert cm.get("CHATBOT_PORT") == 8080  # 기본값

    def test_missing_config_dir(self):
        cm = ConfigManager(config_dir="/nonexistent/path")
        cm.load()
        assert cm.get("CHATBOT_PORT") == 8080  # 기본값


class TestConfigManagerGetAll:
    """get_all 메서드 테스트."""

    def test_get_all_returns_dict(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        result = cm.get_all()
        assert isinstance(result, dict)
        assert "CHATBOT_PORT" in result
        assert "CHATBOT_HOST" in result

    def test_get_all_contains_all_supported(self):
        cm = ConfigManager(config_dir="/nonexistent")
        cm.load()
        result = cm.get_all()
        for key in ConfigManager.SUPPORTED_VARS:
            assert key in result


class TestConfigManagerAutoLoad:
    """자동 로드 테스트."""

    def test_get_without_load(self):
        cm = ConfigManager(config_dir="/nonexistent")
        # load()를 호출하지 않아도 get()에서 자동 로드
        assert cm.get("CHATBOT_PORT") == 8080

    def test_get_all_without_load(self):
        cm = ConfigManager(config_dir="/nonexistent")
        result = cm.get_all()
        assert "CHATBOT_PORT" in result


class TestConfigManagerCasting:
    """타입 캐스팅 테스트."""

    def test_cast_int(self):
        cm = ConfigManager(config_dir="/nonexistent")
        assert cm._cast("CHATBOT_PORT", "3000") == 3000

    def test_cast_bool_true(self):
        cm = ConfigManager(config_dir="/nonexistent")
        assert cm._cast("CHATBOT_DEBUG", "true") is True

    def test_cast_bool_false(self):
        cm = ConfigManager(config_dir="/nonexistent")
        assert cm._cast("CHATBOT_DEBUG", "false") is False

    def test_cast_bool_already_bool(self):
        cm = ConfigManager(config_dir="/nonexistent")
        assert cm._cast("CHATBOT_DEBUG", True) is True

    def test_cast_str(self):
        cm = ConfigManager(config_dir="/nonexistent")
        assert cm._cast("CHATBOT_HOST", "localhost") == "localhost"

    def test_cast_int_invalid(self):
        cm = ConfigManager(config_dir="/nonexistent")
        result = cm._cast("CHATBOT_PORT", "abc")
        assert result == 8080  # 기본값 폴백

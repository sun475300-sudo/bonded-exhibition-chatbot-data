"""환경변수 기반 설정 관리 모듈.

환경변수를 우선으로 사용하고, 미설정 시 config 파일에서 폴백한다.

사용법:
    from src.config_manager import ConfigManager

    config = ConfigManager()
    config.load()
    port = config.get("CHATBOT_PORT", 8080)
"""

import json
import os


class ConfigManager:
    """환경변수 우선, config 파일 폴백 방식의 설정 관리자."""

    # 지원하는 환경변수와 기본값
    SUPPORTED_VARS = {
        "CHATBOT_PORT": 8080,
        "CHATBOT_HOST": "0.0.0.0",
        "CHATBOT_DEBUG": False,
        "CHATBOT_DB_PATH": "logs/chat_logs.db",
        "CHATBOT_LOG_LEVEL": "INFO",
        "CHATBOT_API_KEYS": "",
    }

    # 타입 매핑 (기본값의 타입을 기준으로 자동 캐스팅)
    _TYPE_MAP = {
        "CHATBOT_PORT": int,
        "CHATBOT_HOST": str,
        "CHATBOT_DEBUG": bool,
        "CHATBOT_DB_PATH": str,
        "CHATBOT_LOG_LEVEL": str,
        "CHATBOT_API_KEYS": str,
    }

    def __init__(self, config_dir=None):
        """ConfigManager 초기화.

        Args:
            config_dir: config 파일 디렉토리 경로. 미지정 시 프로젝트 루트의 config/.
        """
        if config_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_dir = os.path.join(base, "config")
        self._config_dir = config_dir
        self._values = {}
        self._file_config = {}
        self._loaded = False

    def load(self):
        """설정을 로드한다. 환경변수 우선, config 파일 폴백."""
        # 1. config 파일에서 로드
        self._file_config = self._load_config_file()

        # 2. 각 지원 변수에 대해 환경변수 > config 파일 > 기본값 순서로 결정
        for key, default in self.SUPPORTED_VARS.items():
            env_val = os.environ.get(key)
            if env_val is not None:
                self._values[key] = self._cast(key, env_val)
            elif key in self._file_config:
                self._values[key] = self._file_config[key]
            else:
                self._values[key] = default

        self._loaded = True
        return self

    def get(self, key, default=None):
        """설정값을 반환한다.

        Args:
            key: 설정 키 (예: 'CHATBOT_PORT')
            default: 키가 없을 때 반환할 기본값

        Returns:
            적절한 타입으로 캐스팅된 설정값
        """
        if not self._loaded:
            self.load()

        if key in self._values:
            return self._values[key]

        # 등록되지 않은 키는 환경변수에서 직접 조회
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val

        return default

    def get_all(self):
        """모든 설정값을 딕셔너리로 반환한다."""
        if not self._loaded:
            self.load()
        return dict(self._values)

    def _cast(self, key, value):
        """값을 해당 키의 타입으로 캐스팅한다.

        Args:
            key: 설정 키
            value: 문자열 값

        Returns:
            캐스팅된 값
        """
        target_type = self._TYPE_MAP.get(key, str)

        if target_type is bool:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes", "on")

        if target_type is int:
            try:
                return int(value)
            except (ValueError, TypeError):
                return self.SUPPORTED_VARS.get(key, 0)

        return str(value)

    def _load_config_file(self):
        """config/chatbot_config.json 파일에서 설정을 로드한다."""
        config_path = os.path.join(self._config_dir, "chatbot_config.json")
        if not os.path.exists(config_path):
            return {}

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # config 파일의 플랫 키-값만 추출 (중첩 객체는 무시)
            result = {}
            for key in self.SUPPORTED_VARS:
                if key in data:
                    result[key] = data[key]
            return result
        except (json.JSONDecodeError, OSError):
            return {}

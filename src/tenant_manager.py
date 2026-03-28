"""멀티 테넌트 관리 모듈.

보세전시장별 독립적인 FAQ 및 설정을 관리한다.
각 테넌트는 별도의 FAQ 컬렉션과 로그 데이터베이스를 가진다.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "tenants.db")
DEFAULT_FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")


class TenantManager:
    """멀티 테넌트 관리 클래스.

    SQLite를 사용하여 테넌트 메타데이터를 저장하고,
    각 테넌트에 대해 독립적인 FAQ 및 설정을 제공한다.
    """

    def __init__(self, db_path=None, data_dir=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.data_dir = data_dir or os.path.join(BASE_DIR, "data")
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        self._init_db()
        self._ensure_default_tenant()

    def _get_conn(self):
        """스레드별 SQLite 연결을 반환한다."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """테넌트 테이블을 초기화한다."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()

    def _ensure_default_tenant(self):
        """기본 테넌트가 없으면 생성한다."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM tenants WHERE id = ?", ("default",)
        ).fetchone()
        if row is None:
            now = datetime.utcnow().isoformat()
            default_config = json.dumps({
                "faq_path": "data/faq.json",
                "description": "기본 보세전시장 테넌트",
            })
            conn.execute(
                "INSERT INTO tenants (id, name, created_at, config, active) VALUES (?, ?, ?, ?, ?)",
                ("default", "기본 보세전시장", now, default_config, 1),
            )
            conn.commit()

    def _tenant_faq_path(self, tenant_id):
        """테넌트별 FAQ 파일 경로를 반환한다."""
        if tenant_id == "default":
            return os.path.join(BASE_DIR, "data", "faq.json")
        return os.path.join(self.data_dir, f"faq_{tenant_id}.json")

    def _tenant_log_db_path(self, tenant_id):
        """테넌트별 로그 DB 경로를 반환한다."""
        if tenant_id == "default":
            return os.path.join(BASE_DIR, "logs", "chat_logs.db")
        logs_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        return os.path.join(logs_dir, f"chat_logs_{tenant_id}.db")

    def _row_to_dict(self, row):
        """SQLite Row 객체를 딕셔너리로 변환한다."""
        if row is None:
            return None
        d = dict(row)
        d["config"] = json.loads(d["config"])
        d["active"] = bool(d["active"])
        return d

    def create_tenant(self, tenant_id, name, config=None):
        """새 테넌트를 생성한다.

        Args:
            tenant_id: 테넌트 고유 ID.
            name: 테넌트 이름.
            config: 테넌트 설정 딕셔너리 (선택).

        Returns:
            생성된 테넌트 정보 딕셔너리.

        Raises:
            ValueError: 이미 존재하는 tenant_id이거나 유효하지 않은 입력.
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id는 비어 있지 않은 문자열이어야 합니다.")
        if not name or not isinstance(name, str):
            raise ValueError("name은 비어 있지 않은 문자열이어야 합니다.")

        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"테넌트 '{tenant_id}'가 이미 존재합니다.")

        now = datetime.utcnow().isoformat()
        tenant_config = config or {}
        config_json = json.dumps(tenant_config, ensure_ascii=False)

        conn.execute(
            "INSERT INTO tenants (id, name, created_at, config, active) VALUES (?, ?, ?, ?, ?)",
            (tenant_id, name, now, config_json, 1),
        )
        conn.commit()

        # 테넌트별 FAQ 파일 초기화 (빈 FAQ)
        faq_path = self._tenant_faq_path(tenant_id)
        if not os.path.exists(faq_path):
            default_faq = {
                "faq_version": "1.0.0",
                "last_updated": now[:10],
                "items": [],
            }
            with open(faq_path, "w", encoding="utf-8") as f:
                json.dump(default_faq, f, ensure_ascii=False, indent=2)

        return self.get_tenant(tenant_id)

    def get_tenant(self, tenant_id):
        """테넌트 정보를 반환한다.

        Args:
            tenant_id: 테넌트 ID.

        Returns:
            테넌트 정보 딕셔너리 또는 None.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_tenants(self):
        """모든 테넌트 목록을 반환한다.

        Returns:
            테넌트 정보 딕셔너리 리스트.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tenants ORDER BY created_at"
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def update_tenant(self, tenant_id, updates):
        """테넌트 설정을 업데이트한다.

        Args:
            tenant_id: 테넌트 ID.
            updates: 업데이트할 필드 딕셔너리 (name, config, active 지원).

        Returns:
            업데이트된 테넌트 정보 딕셔너리.

        Raises:
            ValueError: 존재하지 않는 테넌트.
        """
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"테넌트 '{tenant_id}'를 찾을 수 없습니다.")

        conn = self._get_conn()

        if "name" in updates:
            conn.execute(
                "UPDATE tenants SET name = ? WHERE id = ?",
                (updates["name"], tenant_id),
            )

        if "config" in updates:
            config_json = json.dumps(updates["config"], ensure_ascii=False)
            conn.execute(
                "UPDATE tenants SET config = ? WHERE id = ?",
                (config_json, tenant_id),
            )

        if "active" in updates:
            conn.execute(
                "UPDATE tenants SET active = ? WHERE id = ?",
                (1 if updates["active"] else 0, tenant_id),
            )

        conn.commit()
        return self.get_tenant(tenant_id)

    def delete_tenant(self, tenant_id):
        """테넌트를 삭제한다.

        Args:
            tenant_id: 테넌트 ID.

        Returns:
            True if deleted.

        Raises:
            ValueError: 기본 테넌트 삭제 시도 또는 존재하지 않는 테넌트.
        """
        if tenant_id == "default":
            raise ValueError("기본 테넌트는 삭제할 수 없습니다.")

        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"테넌트 '{tenant_id}'를 찾을 수 없습니다.")

        conn = self._get_conn()
        conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
        conn.commit()

        # 테넌트 FAQ 파일 삭제
        faq_path = self._tenant_faq_path(tenant_id)
        if os.path.exists(faq_path):
            os.remove(faq_path)

        return True

    def get_tenant_faq(self, tenant_id):
        """테넌트별 FAQ 데이터를 반환한다.

        Args:
            tenant_id: 테넌트 ID.

        Returns:
            FAQ 딕셔너리.

        Raises:
            ValueError: 존재하지 않는 테넌트.
        """
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"테넌트 '{tenant_id}'를 찾을 수 없습니다.")

        faq_path = self._tenant_faq_path(tenant_id)
        if not os.path.exists(faq_path):
            return {"faq_version": "1.0.0", "last_updated": "", "items": []}

        with open(faq_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_tenant_config(self, tenant_id):
        """테넌트별 설정을 반환한다.

        Args:
            tenant_id: 테넌트 ID.

        Returns:
            설정 딕셔너리.

        Raises:
            ValueError: 존재하지 않는 테넌트.
        """
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"테넌트 '{tenant_id}'를 찾을 수 없습니다.")

        return tenant["config"]

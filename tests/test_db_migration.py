"""Tests for the database migration system."""

import os
import sys
import tempfile
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db_migration import MigrationManager


@pytest.fixture
def migration_env(tmp_path):
    """Create an isolated migration environment with temp dirs/db."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    db_path = str(tmp_path / "migrations.db")

    # Copy the real migration files into the temp directory
    real_migrations = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "migrations",
    )
    for fname in os.listdir(real_migrations):
        if fname.endswith(".py"):
            shutil.copy(os.path.join(real_migrations, fname), str(migrations_dir))

    mgr = MigrationManager(
        db_path=db_path,
        migrations_dir=str(migrations_dir),
    )
    yield mgr, migrations_dir, db_path
    mgr.close()


class TestMigrationApply:
    """Test applying migrations."""

    def test_initial_version_is_zero(self, migration_env):
        mgr, _, _ = migration_env
        assert mgr.get_current_version() == 0

    def test_apply_all_migrations(self, migration_env):
        mgr, _, _ = migration_env
        applied = mgr.migrate()
        assert applied == [1, 2]
        assert mgr.get_current_version() == 2

    def test_apply_up_to_target_version(self, migration_env):
        mgr, _, _ = migration_env
        applied = mgr.migrate(target_version=1)
        assert applied == [1]
        assert mgr.get_current_version() == 1

    def test_apply_is_idempotent(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        applied = mgr.migrate()
        assert applied == []
        assert mgr.get_current_version() == 2

    def test_tables_created_after_migration(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate(target_version=1)
        conn = mgr._get_target_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        expected = {
            "chat_logs", "feedback", "faq_candidates",
            "subscriptions", "delivery_log", "tenants",
            "audit_logs", "alerts", "satisfaction",
            "faq_history", "law_versions", "update_notifications",
        }
        assert expected.issubset(table_names)

    def test_indexes_created_after_migration_2(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        conn = mgr._get_target_conn()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {row["name"] for row in indexes}
        assert "idx_chat_logs_timestamp" in index_names
        assert "idx_audit_timestamp" in index_names
        assert "idx_feedback_query_id" in index_names


class TestRollback:
    """Test rolling back migrations."""

    def test_rollback_last(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        rolled = mgr.rollback(steps=1)
        assert rolled == [2]
        assert mgr.get_current_version() == 1

    def test_rollback_multiple(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        rolled = mgr.rollback(steps=2)
        assert rolled == [2, 1]
        assert mgr.get_current_version() == 0

    def test_rollback_removes_indexes(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        mgr.rollback(steps=1)
        conn = mgr._get_target_conn()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_chat%'"
        ).fetchall()
        assert len(indexes) == 0

    def test_rollback_removes_tables(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        mgr.rollback(steps=2)
        conn = mgr._get_target_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('schema_migrations', 'sqlite_sequence')"
        ).fetchall()
        assert len(tables) == 0

    def test_rollback_then_reapply(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        mgr.rollback(steps=2)
        applied = mgr.migrate()
        assert applied == [1, 2]
        assert mgr.get_current_version() == 2


class TestMigrationHistory:
    """Test migration history tracking."""

    def test_empty_history(self, migration_env):
        mgr, _, _ = migration_env
        assert mgr.get_migration_history() == []

    def test_history_after_apply(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        history = mgr.get_migration_history()
        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[0]["name"] == "initial_schema"
        assert history[1]["version"] == 2
        assert history[1]["name"] == "add_indexes"
        assert "applied_at" in history[0]

    def test_history_after_rollback(self, migration_env):
        mgr, _, _ = migration_env
        mgr.migrate()
        mgr.rollback(steps=1)
        history = mgr.get_migration_history()
        assert len(history) == 1
        assert history[0]["version"] == 1

    def test_pending_migrations(self, migration_env):
        mgr, _, _ = migration_env
        pending = mgr.get_pending_migrations()
        assert len(pending) == 2
        mgr.migrate(target_version=1)
        pending = mgr.get_pending_migrations()
        assert len(pending) == 1
        assert pending[0][0] == 2


class TestValidateChain:
    """Test migration chain validation."""

    def test_valid_chain(self, migration_env):
        mgr, _, _ = migration_env
        result = mgr.validate_migrations()
        assert result["valid"] is True
        assert result["errors"] == []

    def test_gap_detected(self, migration_env):
        mgr, migrations_dir, _ = migration_env
        # Remove migration 001 to create a gap
        os.remove(os.path.join(str(migrations_dir), "001_initial_schema.py"))
        result = mgr.validate_migrations()
        assert result["valid"] is False
        assert any("gap" in e.lower() for e in result["errors"])

    def test_orphaned_applied_version(self, migration_env):
        mgr, migrations_dir, _ = migration_env
        mgr.migrate()
        # Remove migration file after applying
        os.remove(os.path.join(str(migrations_dir), "002_add_indexes.py"))
        result = mgr.validate_migrations()
        assert result["valid"] is False
        assert any("missing" in e.lower() for e in result["errors"])

    def test_missing_attributes_detected(self, migration_env):
        mgr, migrations_dir, _ = migration_env
        # Create a broken migration
        bad_path = os.path.join(str(migrations_dir), "003_broken.py")
        with open(bad_path, "w") as f:
            f.write("# Missing required attributes\nVERSION = 3\nNAME = 'broken'\n")
        result = mgr.validate_migrations()
        assert result["valid"] is False
        assert any("missing attribute" in e.lower() for e in result["errors"])


class TestCreateMigration:
    """Test programmatic migration creation."""

    def test_create_new_migration(self, migration_env):
        mgr, migrations_dir, _ = migration_env
        path = mgr.create_migration(
            "add_users",
            "\n        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);\n    ",
            "\n        DROP TABLE IF EXISTS users;\n    ",
        )
        assert os.path.exists(path)
        assert "003_add_users.py" in path

        # Verify it can be loaded and applied
        pending = mgr.get_pending_migrations()
        versions = [p[0] for p in pending]
        assert 3 in versions


class TestAPIEndpoints:
    """Test migration API endpoints in web_server."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """Create a test client with an isolated migration manager."""
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        db_path = str(tmp_path / "test_migrations.db")

        real_migrations = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "migrations",
        )
        for fname in os.listdir(real_migrations):
            if fname.endswith(".py"):
                shutil.copy(os.path.join(real_migrations, fname), str(migrations_dir))

        test_mgr = MigrationManager(
            db_path=db_path,
            migrations_dir=str(migrations_dir),
        )

        import web_server
        monkeypatch.setattr(web_server, "migration_manager", test_mgr)

        web_server.app.config["TESTING"] = True
        with web_server.app.test_client() as client:
            yield client
        test_mgr.close()

    def test_get_migrations_status(self, client):
        res = client.get("/api/admin/migrations")
        assert res.status_code == 200
        data = res.get_json()
        assert data["current_version"] == 0
        assert len(data["pending"]) == 2
        assert data["valid"] is True

    def test_apply_migrations(self, client):
        res = client.post(
            "/api/admin/migrations/apply",
            json={},
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["applied"] == [1, 2]
        assert data["current_version"] == 2

    def test_apply_with_target(self, client):
        res = client.post(
            "/api/admin/migrations/apply",
            json={"target_version": 1},
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["applied"] == [1]
        assert data["current_version"] == 1

    def test_rollback_migration(self, client):
        client.post(
            "/api/admin/migrations/apply",
            json={},
            content_type="application/json",
        )
        res = client.post(
            "/api/admin/migrations/rollback",
            json={"steps": 1},
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["rolled_back"] == [2]
        assert data["current_version"] == 1

    def test_status_after_apply(self, client):
        client.post(
            "/api/admin/migrations/apply",
            json={},
            content_type="application/json",
        )
        res = client.get("/api/admin/migrations")
        data = res.get_json()
        assert data["current_version"] == 2
        assert len(data["pending"]) == 0
        assert len(data["history"]) == 2

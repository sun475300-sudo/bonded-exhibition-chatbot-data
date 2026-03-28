"""백업 관리자 테스트."""

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backup_manager import BackupManager, _file_hash


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project directory with sample data files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create sample data files
    faq = {"items": [{"id": "1", "question": "test?", "answer": "yes"}]}
    (data_dir / "faq.json").write_text(json.dumps(faq), encoding="utf-8")

    legal = {"references": [{"id": "L1", "title": "test law"}]}
    (data_dir / "legal_references.json").write_text(json.dumps(legal), encoding="utf-8")

    escalation = {"rules": [{"pattern": "urgent", "target": "admin"}]}
    (data_dir / "escalation_rules.json").write_text(json.dumps(escalation), encoding="utf-8")

    # Create a sample .db file (just some bytes)
    (data_dir / "test.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

    return tmp_path


@pytest.fixture
def manager(temp_project):
    """Create a BackupManager pointed at the temp project."""
    return BackupManager(base_dir=str(temp_project))


class TestCreateBackup:
    def test_creates_zip_file(self, manager, temp_project):
        path = manager.create_backup()
        assert os.path.isfile(path)
        assert path.endswith(".zip")

    def test_backup_contains_data_files(self, manager, temp_project):
        path = manager.create_backup()
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert os.path.join("data", "faq.json") in names
            assert os.path.join("data", "legal_references.json") in names
            assert os.path.join("data", "escalation_rules.json") in names
            assert os.path.join("data", "test.db") in names

    def test_backup_manifest_has_checksums(self, manager, temp_project):
        path = manager.create_backup()
        with zipfile.ZipFile(path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["type"] == "full"
            for rel, info in manifest["files"].items():
                assert "sha256" in info
                assert "size" in info
                assert info["size"] > 0


class TestIncrementalBackup:
    def test_no_changes_returns_none(self, manager, temp_project):
        # First full backup stores hashes
        manager.create_backup()
        # No changes => incremental returns None
        result = manager.create_incremental_backup()
        assert result is None

    def test_changed_file_backed_up(self, manager, temp_project):
        manager.create_backup()

        # Modify a file
        faq_path = os.path.join(str(temp_project), "data", "faq.json")
        with open(faq_path, "w") as f:
            json.dump({"items": [{"id": "2", "question": "new?", "answer": "no"}]}, f)

        path = manager.create_incremental_backup()
        assert path is not None
        assert "incr" in os.path.basename(path)

        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            assert os.path.join("data", "faq.json") in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["type"] == "incremental"
            # Only changed file should be in manifest
            assert len(manifest["files"]) == 1


class TestEncryptDecrypt:
    def test_roundtrip(self, manager, temp_project):
        backup_path = manager.create_backup()
        password = "test-secret-123"

        enc_path = manager.encrypt_backup(backup_path, password)
        assert os.path.isfile(enc_path)
        assert enc_path.endswith(".enc")

        # Encrypted file should differ from original
        with open(backup_path, "rb") as f:
            original = f.read()
        with open(enc_path, "rb") as f:
            encrypted_data = f.read()
        assert original != encrypted_data[48:]  # Skip salt+mac

        # Remove original so decrypt creates it
        os.remove(backup_path)
        dec_path = manager.decrypt_backup(enc_path, password)
        assert os.path.isfile(dec_path)

        with open(dec_path, "rb") as f:
            decrypted = f.read()
        assert decrypted == original

    def test_wrong_password_fails(self, manager, temp_project):
        backup_path = manager.create_backup()
        enc_path = manager.encrypt_backup(backup_path, "correct")

        with pytest.raises(ValueError, match="invalid password"):
            manager.decrypt_backup(enc_path, "wrong")


class TestVerifyBackup:
    def test_valid_backup(self, manager, temp_project):
        path = manager.create_backup()
        result = manager.verify_backup(path)
        assert result["valid"] is True

    def test_corrupted_backup(self, manager, temp_project):
        path = manager.create_backup()
        # Corrupt the zip
        with open(path, "wb") as f:
            f.write(b"not a zip")
        result = manager.verify_backup(path)
        assert result["valid"] is False

    def test_missing_backup(self, manager):
        result = manager.verify_backup("/nonexistent/backup.zip")
        assert result["valid"] is False

    def test_tampered_file_detected(self, manager, temp_project):
        path = manager.create_backup()

        # Tamper with a file inside the zip
        import io
        with zipfile.ZipFile(path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            names = [n for n in zf.namelist() if n != "manifest.json"]
            contents = {n: zf.read(n) for n in names}

        # Rewrite zip with modified content
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for n, data in contents.items():
                if n.endswith("faq.json"):
                    zf.writestr(n, b"tampered content")
                else:
                    zf.writestr(n, data)

        result = manager.verify_backup(path)
        assert result["valid"] is False


class TestListBackups:
    def test_empty_dir(self, manager):
        backups = manager.list_backups()
        assert backups == []

    def test_lists_created_backups(self, manager, temp_project):
        manager.create_backup()
        time.sleep(0.05)
        manager.create_backup()

        backups = manager.list_backups()
        assert len(backups) == 2
        for b in backups:
            assert "filename" in b
            assert "size" in b
            assert "type" in b


class TestRestoreFromBackup:
    def test_restore_files(self, manager, temp_project):
        path = manager.create_backup()

        # Delete original data
        faq_path = os.path.join(str(temp_project), "data", "faq.json")
        original_content = open(faq_path).read()
        os.remove(faq_path)
        assert not os.path.isfile(faq_path)

        result = manager.restore_from_backup(path)
        assert result["count"] > 0
        assert os.path.isfile(faq_path)
        assert open(faq_path).read() == original_content

    def test_restore_nonexistent_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.restore_from_backup("/nonexistent.zip")


class TestCleanupOldBackups:
    def test_keeps_recent_deletes_old(self, manager, temp_project):
        # Create 5 backups
        paths = []
        for i in range(5):
            p = manager.create_backup()
            paths.append(p)
            time.sleep(0.05)

        deleted = manager.cleanup_old_backups(keep_count=2)
        assert len(deleted) == 3

        remaining = manager.list_backups()
        assert len(remaining) == 2

    def test_nothing_to_delete(self, manager, temp_project):
        manager.create_backup()
        deleted = manager.cleanup_old_backups(keep_count=10)
        assert deleted == []


class TestScheduleBackup:
    def test_schedule_creates_timer(self, manager, temp_project):
        timer = manager.schedule_backup(interval_hours=1)
        assert timer is not None
        assert timer.is_alive()
        manager.cancel_scheduled_backup()

    def test_cancel_scheduled_backup(self, manager, temp_project):
        timer = manager.schedule_backup(interval_hours=1)
        manager.cancel_scheduled_backup()
        time.sleep(0.1)  # Allow thread to finish
        assert not timer.is_alive()


# --- API endpoint tests ---

from web_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestBackupAPI:
    def test_create_backup_endpoint(self, client):
        res = client.post("/api/admin/backup")
        assert res.status_code == 201
        data = res.get_json()
        assert data["success"] is True
        assert "filename" in data

        # Clean up
        backup_path = data.get("backup_path")
        if backup_path and os.path.isfile(backup_path):
            os.remove(backup_path)

    def test_list_backups_endpoint(self, client):
        # Create a backup first
        create_res = client.post("/api/admin/backup")
        assert create_res.status_code == 201

        res = client.get("/api/admin/backups")
        assert res.status_code == 200
        data = res.get_json()
        assert "backups" in data
        assert data["count"] >= 1

        # Clean up
        for b in data["backups"]:
            path = b.get("path")
            if path and os.path.isfile(path):
                os.remove(path)

    def test_restore_endpoint_missing_filename(self, client):
        res = client.post("/api/admin/restore", json={})
        assert res.status_code == 400

    def test_restore_endpoint_not_found(self, client):
        res = client.post("/api/admin/restore", json={"filename": "nonexistent.zip"})
        assert res.status_code == 404

    def test_restore_endpoint_success(self, client):
        # Create backup
        create_res = client.post("/api/admin/backup")
        filename = create_res.get_json()["filename"]

        res = client.post("/api/admin/restore", json={"filename": filename})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["count"] > 0

        # Clean up
        backup_path = create_res.get_json().get("backup_path")
        if backup_path and os.path.isfile(backup_path):
            os.remove(backup_path)

    def test_delete_backup_endpoint(self, client):
        # Create backup
        create_res = client.post("/api/admin/backup")
        filename = create_res.get_json()["filename"]

        res = client.delete(f"/api/admin/backup/{filename}")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["deleted"] == filename

    def test_delete_backup_not_found(self, client):
        res = client.delete("/api/admin/backup/nonexistent.zip")
        assert res.status_code == 404

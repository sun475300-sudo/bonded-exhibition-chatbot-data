"""백업/복원 자동화 모듈.

데이터 파일(faq.json, legal_references.json, escalation_rules.json, *.db)의
전체/증분 백업, 암호화, 무결성 검증, 스케줄링을 지원한다.
"""

import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import shutil
import threading
import zipfile

logger = logging.getLogger("backup_manager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Data files to back up
DATA_FILES = [
    os.path.join("data", "faq.json"),
    os.path.join("data", "legal_references.json"),
    os.path.join("data", "escalation_rules.json"),
]

# Pattern for DB files
DATA_DIR = os.path.join(BASE_DIR, "data")


def _find_data_files(base_dir=None):
    """Return list of relative paths for all data files to back up."""
    base = base_dir or BASE_DIR
    files = []
    for rel in DATA_FILES:
        full = os.path.join(base, rel)
        if os.path.isfile(full):
            files.append(rel)
    # Add *.db files from data/
    data_dir = os.path.join(base, "data")
    if os.path.isdir(data_dir):
        for name in os.listdir(data_dir):
            if name.endswith(".db"):
                rel = os.path.join("data", name)
                if rel not in files:
                    files.append(rel)
    return sorted(files)


def _file_hash(filepath):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from password + salt using PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)


class BackupManager:
    """데이터 파일 백업/복원 관리자."""

    def __init__(self, base_dir=None):
        self.base_dir = base_dir or BASE_DIR
        self._schedule_timer = None
        self._hash_store_path = os.path.join(self.base_dir, "backups", ".last_hashes.json")

    def create_backup(self, backup_dir="backups/"):
        """Create a full timestamped backup of all data files.

        Returns the path to the created backup zip file.
        """
        backup_base = os.path.join(self.base_dir, backup_dir)
        os.makedirs(backup_base, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        zip_name = f"backup_{timestamp}.zip"
        zip_path = os.path.join(backup_base, zip_name)

        data_files = _find_data_files(self.base_dir)
        manifest = {
            "timestamp": timestamp,
            "type": "full",
            "files": {},
        }

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in data_files:
                full = os.path.join(self.base_dir, rel)
                if os.path.isfile(full):
                    zf.write(full, rel)
                    manifest["files"][rel] = {
                        "size": os.path.getsize(full),
                        "sha256": _file_hash(full),
                    }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # Update stored hashes for incremental backups
        self._save_hashes(manifest["files"])

        logger.info(f"Full backup created: {zip_path}")
        return zip_path

    def create_incremental_backup(self, backup_dir="backups/"):
        """Create an incremental backup containing only changed files.

        Compares current file hashes against last backup hashes.
        Returns the backup path, or None if no files changed.
        """
        backup_base = os.path.join(self.base_dir, backup_dir)
        os.makedirs(backup_base, exist_ok=True)

        old_hashes = self._load_hashes()
        data_files = _find_data_files(self.base_dir)

        changed = []
        current_hashes = {}
        for rel in data_files:
            full = os.path.join(self.base_dir, rel)
            if os.path.isfile(full):
                h = _file_hash(full)
                current_hashes[rel] = {
                    "size": os.path.getsize(full),
                    "sha256": h,
                }
                if old_hashes.get(rel, {}).get("sha256") != h:
                    changed.append(rel)

        if not changed:
            logger.info("No files changed since last backup; skipping incremental.")
            return None

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        zip_name = f"backup_incr_{timestamp}.zip"
        zip_path = os.path.join(backup_base, zip_name)

        manifest = {
            "timestamp": timestamp,
            "type": "incremental",
            "files": {},
        }

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in changed:
                full = os.path.join(self.base_dir, rel)
                zf.write(full, rel)
                manifest["files"][rel] = current_hashes[rel]
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        self._save_hashes(current_hashes)

        logger.info(f"Incremental backup created: {zip_path} ({len(changed)} files)")
        return zip_path

    def encrypt_backup(self, backup_path, password):
        """Encrypt a backup file using AES-like scheme (HMAC+XOR with PBKDF2 key).

        Returns the path to the encrypted file (.enc).
        """
        salt = os.urandom(16)
        key = _derive_key(password, salt)

        with open(backup_path, "rb") as f:
            plaintext = f.read()

        # Generate a keystream via HMAC-SHA256 in counter mode
        encrypted = self._xor_crypt(plaintext, key)

        # HMAC for integrity
        mac = hmac.new(key, encrypted, hashlib.sha256).digest()

        enc_path = backup_path + ".enc"
        with open(enc_path, "wb") as f:
            # Format: 16-byte salt | 32-byte HMAC | encrypted data
            f.write(salt)
            f.write(mac)
            f.write(encrypted)

        logger.info(f"Backup encrypted: {enc_path}")
        return enc_path

    def decrypt_backup(self, encrypted_path, password):
        """Decrypt a previously encrypted backup file.

        Returns the path to the decrypted file.
        """
        with open(encrypted_path, "rb") as f:
            salt = f.read(16)
            stored_mac = f.read(32)
            encrypted = f.read()

        key = _derive_key(password, salt)

        # Verify HMAC
        computed_mac = hmac.new(key, encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(stored_mac, computed_mac):
            raise ValueError("Decryption failed: invalid password or corrupted file")

        plaintext = self._xor_crypt(encrypted, key)

        # Output path: strip .enc
        if encrypted_path.endswith(".enc"):
            dec_path = encrypted_path[:-4]
        else:
            dec_path = encrypted_path + ".dec"

        with open(dec_path, "wb") as f:
            f.write(plaintext)

        logger.info(f"Backup decrypted: {dec_path}")
        return dec_path

    def verify_backup(self, backup_path):
        """Verify integrity of a backup by checking checksums in manifest.

        Returns dict with 'valid' bool and 'details'.
        """
        if not os.path.isfile(backup_path):
            return {"valid": False, "details": "Backup file not found"}

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    return {"valid": False, "details": "Missing manifest.json"}

                manifest = json.loads(zf.read("manifest.json"))
                errors = []

                for rel, info in manifest.get("files", {}).items():
                    if rel not in zf.namelist():
                        errors.append(f"Missing file: {rel}")
                        continue

                    data = zf.read(rel)
                    actual_hash = hashlib.sha256(data).hexdigest()
                    expected_hash = info.get("sha256", "")

                    if actual_hash != expected_hash:
                        errors.append(f"Hash mismatch: {rel}")

                    actual_size = len(data)
                    expected_size = info.get("size", 0)
                    if actual_size != expected_size:
                        errors.append(f"Size mismatch: {rel} (expected {expected_size}, got {actual_size})")

                if errors:
                    return {"valid": False, "details": "; ".join(errors)}

                return {"valid": True, "details": f"All {len(manifest.get('files', {}))} files verified"}

        except zipfile.BadZipFile:
            return {"valid": False, "details": "Corrupted zip file"}
        except Exception as e:
            return {"valid": False, "details": str(e)}

    def list_backups(self, backup_dir="backups/"):
        """Return a list of available backups with metadata."""
        backup_base = os.path.join(self.base_dir, backup_dir)
        if not os.path.isdir(backup_base):
            return []

        backups = []
        for name in sorted(os.listdir(backup_base)):
            if not name.endswith(".zip"):
                continue
            path = os.path.join(backup_base, name)
            info = {
                "filename": name,
                "path": path,
                "size": os.path.getsize(path),
                "created": os.path.getmtime(path),
            }
            # Try to read manifest
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    if "manifest.json" in zf.namelist():
                        manifest = json.loads(zf.read("manifest.json"))
                        info["timestamp"] = manifest.get("timestamp", "")
                        info["type"] = manifest.get("type", "unknown")
                        info["file_count"] = len(manifest.get("files", {}))
            except Exception:
                info["type"] = "unknown"
            backups.append(info)

        return backups

    def restore_from_backup(self, backup_path):
        """Restore data files from a backup zip.

        Returns dict with restore results.
        """
        if not os.path.isfile(backup_path):
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        restored = []
        with zipfile.ZipFile(backup_path, "r") as zf:
            for name in zf.namelist():
                if name == "manifest.json":
                    continue
                target = os.path.join(self.base_dir, name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                restored.append(name)

        logger.info(f"Restored {len(restored)} files from {backup_path}")
        return {"restored_files": restored, "count": len(restored)}

    def cleanup_old_backups(self, keep_count=10, backup_dir="backups/"):
        """Remove oldest backups beyond keep_count.

        Returns list of deleted backup filenames.
        """
        backup_base = os.path.join(self.base_dir, backup_dir)
        if not os.path.isdir(backup_base):
            return []

        zips = []
        for name in os.listdir(backup_base):
            if name.endswith(".zip"):
                path = os.path.join(backup_base, name)
                zips.append((name, path, os.path.getmtime(path)))

        # Sort by modification time, newest first
        zips.sort(key=lambda x: x[2], reverse=True)

        deleted = []
        for name, path, _ in zips[keep_count:]:
            os.remove(path)
            # Also remove .enc if exists
            enc_path = path + ".enc"
            if os.path.isfile(enc_path):
                os.remove(enc_path)
            deleted.append(name)

        if deleted:
            logger.info(f"Cleaned up {len(deleted)} old backups")
        return deleted

    def schedule_backup(self, interval_hours=24, backup_dir="backups/"):
        """Schedule periodic full backups using threading.Timer.

        Returns the timer object.
        """
        self.cancel_scheduled_backup()

        def _run():
            try:
                self.create_backup(backup_dir=backup_dir)
            except Exception as e:
                logger.error(f"Scheduled backup failed: {e}")
            # Re-schedule
            self._schedule_timer = threading.Timer(
                interval_hours * 3600, _run
            )
            self._schedule_timer.daemon = True
            self._schedule_timer.start()

        self._schedule_timer = threading.Timer(interval_hours * 3600, _run)
        self._schedule_timer.daemon = True
        self._schedule_timer.start()

        logger.info(f"Backup scheduled every {interval_hours} hours")
        return self._schedule_timer

    def cancel_scheduled_backup(self):
        """Cancel any scheduled backup timer."""
        if self._schedule_timer is not None:
            self._schedule_timer.cancel()
            self._schedule_timer = None

    # --- Internal helpers ---

    def _xor_crypt(self, data, key):
        """XOR encryption using HMAC-SHA256 in counter mode as keystream."""
        result = bytearray(len(data))
        block_size = 32  # SHA256 output
        for i in range(0, len(data), block_size):
            counter = i // block_size
            ks_block = hmac.new(
                key,
                counter.to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
            chunk = data[i:i + block_size]
            for j, b in enumerate(chunk):
                result[i + j] = b ^ ks_block[j]
        return bytes(result)

    def _save_hashes(self, file_hashes):
        """Save file hashes for incremental backup comparison."""
        os.makedirs(os.path.dirname(self._hash_store_path), exist_ok=True)
        with open(self._hash_store_path, "w") as f:
            json.dump(file_hashes, f, indent=2)

    def _load_hashes(self):
        """Load previously saved file hashes."""
        if not os.path.isfile(self._hash_store_path):
            return {}
        try:
            with open(self._hash_store_path) as f:
                return json.load(f)
        except Exception:
            return {}

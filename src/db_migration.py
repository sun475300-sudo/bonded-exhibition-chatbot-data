"""Database schema migration system.

Manages versioned schema migrations stored as Python files in the
``migrations/`` directory.  Migration metadata (applied versions, timestamps)
is persisted in a dedicated SQLite database (``data/migrations.db``).

Usage::

    from src.db_migration import MigrationManager

    mgr = MigrationManager()
    mgr.migrate()           # apply all pending migrations
    mgr.rollback(steps=1)   # undo the last migration
"""

import importlib
import importlib.util
import logging
import os
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "migrations.db")


class MigrationManager:
    """Manages database schema migrations.

    Parameters:
        db_path: Path to the migration metadata SQLite database.
        migrations_dir: Directory containing migration Python files.
        target_db_path: Path to the application database that migrations
            modify.  When *None* the manager operates on the metadata db
            itself (useful for self-contained testing).
    """

    def __init__(self, db_path=None, migrations_dir=None, target_db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
        self.target_db_path = target_db_path or self.db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_metadata_table()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self):
        """Return a thread-local connection to the metadata database."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _get_target_conn(self):
        """Return a connection to the target application database."""
        if self.target_db_path == self.db_path:
            return self._get_conn()
        if not hasattr(self._local, "target_conn") or self._local.target_conn is None:
            self._local.target_conn = sqlite3.connect(self.target_db_path)
            self._local.target_conn.row_factory = sqlite3.Row
        return self._local.target_conn

    def _init_metadata_table(self):
        """Create the ``schema_migrations`` table if it does not exist."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def _load_migration_module(self, filepath):
        """Dynamically load a migration Python file and return the module."""
        module_name = os.path.splitext(os.path.basename(filepath))[0]
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _discover_migrations(self):
        """Return a sorted list of ``(version, name, filepath)`` tuples.

        Only files matching the pattern ``NNN_<name>.py`` are considered.
        """
        migrations = []
        if not os.path.isdir(self.migrations_dir):
            return migrations
        for fname in sorted(os.listdir(self.migrations_dir)):
            if fname.startswith("__") or not fname.endswith(".py"):
                continue
            parts = fname.split("_", 1)
            if not parts[0].isdigit():
                continue
            version = int(parts[0])
            name = os.path.splitext(parts[1])[0] if len(parts) > 1 else ""
            filepath = os.path.join(self.migrations_dir, fname)
            migrations.append((version, name, filepath))
        migrations.sort(key=lambda m: m[0])
        return migrations

    def _applied_versions(self):
        """Return a set of already-applied version numbers."""
        conn = self._get_conn()
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {row["version"] for row in rows}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_version(self):
        """Return the current (highest applied) schema version, or 0."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_migrations"
        ).fetchone()
        return row["v"] if row and row["v"] is not None else 0

    def get_pending_migrations(self):
        """Return a list of ``(version, name, filepath)`` not yet applied."""
        applied = self._applied_versions()
        return [m for m in self._discover_migrations() if m[0] not in applied]

    def migrate(self, target_version=None):
        """Apply pending migrations up to *target_version* (inclusive).

        If *target_version* is ``None``, all pending migrations are applied.

        Returns:
            list[int]: Versions that were applied.
        """
        pending = self.get_pending_migrations()
        applied = []
        for version, name, filepath in pending:
            if target_version is not None and version > target_version:
                break
            mod = self._load_migration_module(filepath)
            target_conn = self._get_target_conn()
            try:
                mod.up(target_conn)
                target_conn.commit()
            except Exception:
                target_conn.rollback()
                raise
            # Record in metadata
            meta_conn = self._get_conn()
            meta_conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now().isoformat()),
            )
            meta_conn.commit()
            applied.append(version)
            logger.info("Applied migration %03d_%s", version, name)
        return applied

    def rollback(self, steps=1):
        """Roll back the last *steps* applied migrations.

        Returns:
            list[int]: Versions that were rolled back.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT ?",
            (steps,),
        ).fetchall()
        rolled_back = []
        for row in rows:
            version = row["version"]
            name = row["name"]
            # Find the migration file
            migrations = self._discover_migrations()
            match = [m for m in migrations if m[0] == version]
            if not match:
                logger.warning("Migration file for version %d not found, skipping down()", version)
            else:
                mod = self._load_migration_module(match[0][2])
                target_conn = self._get_target_conn()
                try:
                    mod.down(target_conn)
                    target_conn.commit()
                except Exception:
                    target_conn.rollback()
                    raise
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
            conn.commit()
            rolled_back.append(version)
            logger.info("Rolled back migration %03d_%s", version, name)
        return rolled_back

    def get_migration_history(self):
        """Return a list of dicts describing applied migrations."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_migration(self, name, up_sql, down_sql):
        """Create a new migration file in the migrations directory.

        Parameters:
            name: Short descriptive name (snake_case).
            up_sql: SQL to run when applying the migration.
            down_sql: SQL to run when rolling back.

        Returns:
            str: Path to the created migration file.
        """
        migrations = self._discover_migrations()
        next_version = max((m[0] for m in migrations), default=0) + 1
        filename = f"{next_version:03d}_{name}.py"
        filepath = os.path.join(self.migrations_dir, filename)

        content = f'''"""Migration: {name}."""

VERSION = {next_version}
NAME = "{name}"


def up(conn):
    """Apply migration."""
    conn.executescript("""{up_sql}""")


def down(conn):
    """Rollback migration."""
    conn.executescript("""{down_sql}""")
'''
        os.makedirs(self.migrations_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info("Created migration file: %s", filepath)
        return filepath

    def validate_migrations(self):
        """Check migration chain integrity.

        Validates that:
        - All migration files have unique version numbers.
        - Version numbers form a contiguous sequence starting from 1.
        - All applied versions have corresponding migration files.
        - Each migration file exposes ``VERSION``, ``NAME``, ``up``, ``down``.

        Returns:
            dict: ``{"valid": bool, "errors": list[str]}``
        """
        errors = []
        migrations = self._discover_migrations()
        versions = [m[0] for m in migrations]

        # Check for duplicates
        if len(versions) != len(set(versions)):
            errors.append("Duplicate version numbers detected")

        # Check contiguous sequence
        if versions:
            expected = list(range(1, max(versions) + 1))
            if versions != expected:
                errors.append(
                    f"Version gap detected: expected {expected}, got {versions}"
                )

        # Check applied versions have files
        applied = self._applied_versions()
        file_versions = set(versions)
        orphaned = applied - file_versions
        if orphaned:
            errors.append(f"Applied versions missing migration files: {sorted(orphaned)}")

        # Validate each migration module
        for version, name, filepath in migrations:
            try:
                mod = self._load_migration_module(filepath)
                for attr in ("VERSION", "NAME", "up", "down"):
                    if not hasattr(mod, attr):
                        errors.append(f"Migration {version} missing attribute: {attr}")
                if hasattr(mod, "VERSION") and mod.VERSION != version:
                    errors.append(
                        f"Migration file {filepath} VERSION={mod.VERSION} "
                        f"does not match filename version {version}"
                    )
            except Exception as exc:
                errors.append(f"Error loading migration {version}: {exc}")

        return {"valid": len(errors) == 0, "errors": errors}

    def close(self):
        """Close database connections."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
        if hasattr(self._local, "target_conn") and self._local.target_conn:
            self._local.target_conn.close()
            self._local.target_conn = None

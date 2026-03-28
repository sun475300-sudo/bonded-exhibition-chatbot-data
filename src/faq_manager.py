"""FAQ CRUD management module.

Provides FAQManager class for creating, reading, updating, and deleting
FAQ items, with atomic file writes and SQLite-based change history tracking.
"""

import copy
import json
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VALID_CATEGORIES = [
    "GENERAL", "LICENSE", "IMPORT_EXPORT", "EXHIBITION",
    "SALES", "SAMPLE", "FOOD_TASTING", "DOCUMENTS",
    "PENALTIES", "CONTACT",
]

REQUIRED_FIELDS = ["category", "question", "answer"]


class FAQManager:
    """Manages FAQ items with CRUD operations, atomic persistence, and history."""

    def __init__(self, faq_path: str | None = None, history_db_path: str | None = None):
        self.faq_path = faq_path or os.path.join(BASE_DIR, "data", "faq.json")
        self.history_db_path = history_db_path or os.path.join(BASE_DIR, "logs", "faq_history.db")
        self._lock = threading.Lock()
        self._init_history_db()
        self._load()

    def _init_history_db(self):
        """Create the history table if it does not exist."""
        os.makedirs(os.path.dirname(self.history_db_path), exist_ok=True)
        conn = sqlite3.connect(self.history_db_path)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS faq_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    faq_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_data TEXT,
                    new_data TEXT,
                    timestamp TEXT NOT NULL,
                    user TEXT DEFAULT 'admin'
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    def _load(self):
        """Load FAQ data from the JSON file."""
        with open(self.faq_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        if "items" not in self._data:
            self._data["items"] = []

    def list_all(self) -> list[dict]:
        """Return all FAQ items with metadata."""
        with self._lock:
            items = []
            for item in self._data["items"]:
                enriched = dict(item)
                enriched["keywords_count"] = len(item.get("keywords", []))
                enriched["last_modified"] = self._get_last_modified(item["id"])
                items.append(enriched)
            return items

    def get(self, faq_id: str) -> dict | None:
        """Return a single FAQ item by ID, or None."""
        with self._lock:
            for item in self._data["items"]:
                if item.get("id") == faq_id:
                    enriched = dict(item)
                    enriched["last_modified"] = self._get_last_modified(faq_id)
                    return enriched
            return None

    def create(self, item: dict) -> dict:
        """Validate and add a new FAQ item. Returns the created item.

        Raises ValueError on validation failure or duplicate ID.
        """
        with self._lock:
            self._validate(item)

            # Assign next ID if not provided
            faq_id = item.get("id")
            if not faq_id:
                faq_id = self._next_id()
                item["id"] = faq_id

            # Check duplicate
            existing_ids = {it.get("id") for it in self._data["items"]}
            if faq_id in existing_ids:
                raise ValueError(f"FAQ ID '{faq_id}' already exists")

            new_item = self._normalize(item)
            self._data["items"].append(new_item)
            self._data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._save()
            self._log_change("create", faq_id, None, new_item)
            return dict(new_item)

    def update(self, faq_id: str, item: dict) -> dict:
        """Validate and update an existing FAQ item. Returns updated item.

        Raises ValueError on validation failure.
        Raises KeyError if faq_id not found.
        """
        with self._lock:
            self._validate(item)

            idx = self._find_index(faq_id)
            if idx is None:
                raise KeyError(f"FAQ ID '{faq_id}' not found")

            old_item = copy.deepcopy(self._data["items"][idx])
            updated = self._normalize(item)
            updated["id"] = faq_id  # preserve original ID
            self._data["items"][idx] = updated
            self._data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._save()
            self._log_change("update", faq_id, old_item, updated)
            return dict(updated)

    def delete(self, faq_id: str) -> dict:
        """Remove a FAQ item. Returns the deleted item.

        Raises KeyError if faq_id not found.
        """
        with self._lock:
            idx = self._find_index(faq_id)
            if idx is None:
                raise KeyError(f"FAQ ID '{faq_id}' not found")

            old_item = self._data["items"].pop(idx)
            self._data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._save()
            self._log_change("delete", faq_id, old_item, None)
            return dict(old_item)

    def get_history(self, faq_id: str) -> list[dict]:
        """Return change history for a FAQ item."""
        conn = sqlite3.connect(self.history_db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM faq_history WHERE faq_id = ? ORDER BY timestamp DESC",
                (faq_id,),
            ).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                if entry.get("old_data"):
                    entry["old_data"] = json.loads(entry["old_data"])
                if entry.get("new_data"):
                    entry["new_data"] = json.loads(entry["new_data"])
                result.append(entry)
            return result
        finally:
            conn.close()

    # --- internal helpers ---

    def _find_index(self, faq_id: str) -> int | None:
        for i, item in enumerate(self._data["items"]):
            if item.get("id") == faq_id:
                return i
        return None

    def _next_id(self) -> str:
        """Generate the next alphabetical ID (A, B, ... Z, AA, AB, ...)."""
        existing = {it.get("id", "") for it in self._data["items"]}
        # Try single letters first, then double
        for length in range(1, 4):
            for i in range(26 ** length):
                candidate = ""
                val = i
                for _ in range(length):
                    candidate = chr(ord("A") + val % 26) + candidate
                    val //= 26
                if candidate not in existing:
                    return candidate
        raise ValueError("Cannot generate next ID")

    def _validate(self, item: dict):
        """Validate required fields and category."""
        for field in REQUIRED_FIELDS:
            if not item.get(field):
                raise ValueError(f"Required field '{field}' is missing or empty")

        category = item.get("category", "")
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}"
            )

    def _normalize(self, item: dict) -> dict:
        """Return a cleaned FAQ item dict with all expected keys."""
        return {
            "id": item.get("id", ""),
            "category": item["category"],
            "question": item["question"],
            "answer": item["answer"],
            "legal_basis": item.get("legal_basis", []),
            "notes": item.get("notes", ""),
            "keywords": item.get("keywords", []),
        }

    def _save(self):
        """Write FAQ data to file atomically."""
        dir_name = os.path.dirname(self.faq_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_path, self.faq_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _log_change(self, action: str, faq_id: str, old: dict | None, new: dict | None):
        """Record a change in the SQLite history database."""
        conn = sqlite3.connect(self.history_db_path)
        try:
            conn.execute(
                "INSERT INTO faq_history (faq_id, action, old_data, new_data, timestamp) VALUES (?, ?, ?, ?, ?)",
                (
                    faq_id,
                    action,
                    json.dumps(old, ensure_ascii=False) if old else None,
                    json.dumps(new, ensure_ascii=False) if new else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_last_modified(self, faq_id: str) -> str | None:
        """Get the timestamp of the most recent change for a FAQ item."""
        conn = sqlite3.connect(self.history_db_path)
        try:
            row = conn.execute(
                "SELECT timestamp FROM faq_history WHERE faq_id = ? ORDER BY timestamp DESC LIMIT 1",
                (faq_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

"""FAQ bulk import/export functionality.

Provides FAQImporter and FAQExporter classes for importing and exporting
FAQ items in CSV and JSON formats, with validation and merge strategies.
"""

import csv
import json
import os
from datetime import datetime, timezone

from src.faq_manager import FAQManager, VALID_CATEGORIES, REQUIRED_FIELDS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSV_COLUMNS = ["id", "category", "question", "answer", "keywords", "legal_basis", "notes"]
KEYWORDS_SEPARATOR = ";"


class FAQImporter:
    """Import FAQ items from CSV or JSON files."""

    def __init__(self, faq_manager: FAQManager | None = None):
        self.faq_manager = faq_manager or FAQManager()

    def import_csv(self, file_path: str) -> list[dict]:
        """Import FAQ items from a CSV file.

        Args:
            file_path: Path to the CSV file.

        Returns:
            List of imported FAQ item dicts.
        """
        items = self._parse_csv(file_path)
        errors = self.validate_import(items)
        if errors:
            raise ValueError(f"Validation errors: {errors}")
        return items

    def import_json(self, file_path: str) -> list[dict]:
        """Import FAQ items from a JSON file.

        Supports both our format (with 'items' key) and generic (plain list).

        Args:
            file_path: Path to the JSON file.

        Returns:
            List of imported FAQ item dicts.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and "items" in data:
            items = data["items"]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError("JSON must be a list of items or an object with 'items' key")

        # Normalize keywords/legal_basis that may be strings
        for item in items:
            if isinstance(item.get("keywords"), str):
                item["keywords"] = [k.strip() for k in item["keywords"].split(KEYWORDS_SEPARATOR) if k.strip()]
            if isinstance(item.get("legal_basis"), str):
                item["legal_basis"] = [lb.strip() for lb in item["legal_basis"].split(KEYWORDS_SEPARATOR) if lb.strip()]

        errors = self.validate_import(items)
        if errors:
            raise ValueError(f"Validation errors: {errors}")
        return items

    def validate_import(self, items: list[dict]) -> list[str]:
        """Validate all items before import.

        Checks required fields, valid categories, and duplicate IDs within the batch.

        Args:
            items: List of FAQ item dicts to validate.

        Returns:
            List of error strings. Empty list means all valid.
        """
        errors = []
        seen_ids = set()

        for i, item in enumerate(items):
            row_label = f"Row {i + 1}"

            # Required fields
            for field in REQUIRED_FIELDS:
                if not item.get(field):
                    errors.append(f"{row_label}: missing required field '{field}'")

            # Valid category
            category = item.get("category", "")
            if category and category not in VALID_CATEGORIES:
                errors.append(f"{row_label}: invalid category '{category}'")

            # Duplicate IDs within batch
            item_id = item.get("id", "")
            if item_id:
                if item_id in seen_ids:
                    errors.append(f"{row_label}: duplicate ID '{item_id}' in import batch")
                seen_ids.add(item_id)

        return errors

    def preview_import(self, file_path: str, format: str = "csv") -> dict:
        """Preview import without actually applying changes.

        Args:
            file_path: Path to the file.
            format: 'csv' or 'json'.

        Returns:
            Dict with 'items', 'errors', 'count', and 'valid' keys.
        """
        try:
            if format == "csv":
                items = self._parse_csv(file_path)
            elif format == "json":
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "items" in data:
                    items = data["items"]
                elif isinstance(data, list):
                    items = data
                else:
                    return {
                        "items": [],
                        "errors": ["JSON must be a list or object with 'items' key"],
                        "count": 0,
                        "valid": False,
                    }
                # Normalize
                for item in items:
                    if isinstance(item.get("keywords"), str):
                        item["keywords"] = [k.strip() for k in item["keywords"].split(KEYWORDS_SEPARATOR) if k.strip()]
                    if isinstance(item.get("legal_basis"), str):
                        item["legal_basis"] = [lb.strip() for lb in item["legal_basis"].split(KEYWORDS_SEPARATOR) if lb.strip()]
            else:
                return {
                    "items": [],
                    "errors": [f"Unsupported format: {format}"],
                    "count": 0,
                    "valid": False,
                }

            errors = self.validate_import(items)
            return {
                "items": items,
                "errors": errors,
                "count": len(items),
                "valid": len(errors) == 0,
            }
        except Exception as e:
            return {
                "items": [],
                "errors": [str(e)],
                "count": 0,
                "valid": False,
            }

    def merge_import(self, items: list[dict], strategy: str = "skip") -> dict:
        """Merge imported items with existing FAQ data.

        Args:
            items: List of FAQ item dicts to merge.
            strategy: 'skip' (skip existing), 'overwrite' (update existing),
                      or 'append' (always create new with new IDs).

        Returns:
            Dict with 'created', 'updated', 'skipped' counts and 'errors' list.
        """
        if strategy not in ("skip", "overwrite", "append"):
            raise ValueError(f"Invalid merge strategy: {strategy}")

        result = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        for item in items:
            try:
                item_id = item.get("id", "")
                existing = self.faq_manager.get(item_id) if item_id else None

                if existing:
                    if strategy == "skip":
                        result["skipped"] += 1
                    elif strategy == "overwrite":
                        self.faq_manager.update(item_id, item)
                        result["updated"] += 1
                    elif strategy == "append":
                        # Create with new ID
                        new_item = dict(item)
                        new_item.pop("id", None)
                        self.faq_manager.create(new_item)
                        result["created"] += 1
                else:
                    self.faq_manager.create(item)
                    result["created"] += 1
            except Exception as e:
                result["errors"].append(f"Item '{item.get('id', '?')}': {e}")

        return result

    def _parse_csv(self, file_path: str) -> list[dict]:
        """Parse a CSV file into a list of FAQ item dicts."""
        items = []
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item = {
                    "id": row.get("id", "").strip(),
                    "category": row.get("category", "").strip(),
                    "question": row.get("question", "").strip(),
                    "answer": row.get("answer", "").strip(),
                    "keywords": [
                        k.strip()
                        for k in row.get("keywords", "").split(KEYWORDS_SEPARATOR)
                        if k.strip()
                    ],
                    "legal_basis": [
                        lb.strip()
                        for lb in row.get("legal_basis", "").split(KEYWORDS_SEPARATOR)
                        if lb.strip()
                    ],
                    "notes": row.get("notes", "").strip(),
                }
                items.append(item)
        return items


class FAQExporter:
    """Export FAQ items to CSV or JSON files."""

    def __init__(self, faq_manager: FAQManager | None = None):
        self.faq_manager = faq_manager or FAQManager()

    def export_csv(self, output_path: str, items: list[dict] | None = None) -> str:
        """Export FAQ items to a CSV file.

        Args:
            output_path: Path for the output CSV file.
            items: Optional list of items. If None, exports all from manager.

        Returns:
            The output path.
        """
        if items is None:
            items = self.faq_manager.list_all()

        self._write_csv(output_path, items, bom=False)
        return output_path

    def export_json(self, output_path: str, items: list[dict] | None = None) -> str:
        """Export FAQ items to a JSON file.

        Args:
            output_path: Path for the output JSON file.
            items: Optional list of items. If None, exports all from manager.

        Returns:
            The output path.
        """
        if items is None:
            items = self.faq_manager.list_all()

        # Clean items (remove enriched fields like keywords_count, last_modified)
        clean_items = []
        for item in items:
            clean = {
                "id": item.get("id", ""),
                "category": item.get("category", ""),
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "keywords": item.get("keywords", []),
                "legal_basis": item.get("legal_basis", []),
                "notes": item.get("notes", ""),
            }
            clean_items.append(clean)

        data = {
            "faq_version": "3.0.0",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "items": clean_items,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

        return output_path

    def export_excel_csv(self, output_path: str, items: list[dict] | None = None) -> str:
        """Export FAQ items to a CSV file with BOM for Excel compatibility.

        Args:
            output_path: Path for the output CSV file.
            items: Optional list of items. If None, exports all from manager.

        Returns:
            The output path.
        """
        if items is None:
            items = self.faq_manager.list_all()

        self._write_csv(output_path, items, bom=True)
        return output_path

    def _write_csv(self, output_path: str, items: list[dict], bom: bool = False):
        """Write items to a CSV file.

        Args:
            output_path: Output file path.
            items: List of FAQ item dicts.
            bom: If True, write UTF-8 BOM at the start for Excel compatibility.
        """
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            if bom:
                f.write("\ufeff")

            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for item in items:
                keywords = item.get("keywords", [])
                if isinstance(keywords, list):
                    keywords = KEYWORDS_SEPARATOR.join(keywords)

                legal_basis = item.get("legal_basis", [])
                if isinstance(legal_basis, list):
                    legal_basis = KEYWORDS_SEPARATOR.join(legal_basis)

                writer.writerow({
                    "id": item.get("id", ""),
                    "category": item.get("category", ""),
                    "question": item.get("question", ""),
                    "answer": item.get("answer", ""),
                    "keywords": keywords,
                    "legal_basis": legal_basis,
                    "notes": item.get("notes", ""),
                })

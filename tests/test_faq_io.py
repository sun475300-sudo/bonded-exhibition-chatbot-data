"""FAQ Import/Export tests."""

import csv
import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.faq_manager import FAQManager, VALID_CATEGORIES
from src.faq_io import FAQImporter, FAQExporter, CSV_COLUMNS, KEYWORDS_SEPARATOR


# --- Fixtures ---

SAMPLE_FAQ_DATA = {
    "faq_version": "3.0.0",
    "last_updated": "2026-03-27",
    "items": [
        {
            "id": "A",
            "category": "GENERAL",
            "question": "What is a bonded exhibition hall?",
            "answer": "A bonded exhibition hall is a bonded area for exhibitions.",
            "legal_basis": ["Article 190"],
            "notes": "",
            "keywords": ["bonded", "exhibition", "definition"],
        },
        {
            "id": "B",
            "category": "IMPORT_EXPORT",
            "question": "Do I need to file a report for imports?",
            "answer": "Yes, you must file a report with customs.",
            "legal_basis": ["Notice Article 10"],
            "notes": "important",
            "keywords": ["import", "report", "customs"],
        },
    ],
}


@pytest.fixture
def faq_env(tmp_path):
    """Set up a temporary FAQ environment with manager, importer, and exporter."""
    faq_file = tmp_path / "faq.json"
    faq_file.write_text(json.dumps(SAMPLE_FAQ_DATA, ensure_ascii=False), encoding="utf-8")
    history_db = tmp_path / "history.db"
    manager = FAQManager(faq_path=str(faq_file), history_db_path=str(history_db))
    importer = FAQImporter(manager)
    exporter = FAQExporter(manager)
    return manager, importer, exporter, tmp_path


@pytest.fixture
def csv_file(tmp_path):
    """Create a sample CSV file for import."""
    path = tmp_path / "import.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "id": "X",
            "category": "GENERAL",
            "question": "Test question 1?",
            "answer": "Test answer 1.",
            "keywords": "test;question;one",
            "legal_basis": "Law 1;Law 2",
            "notes": "note1",
        })
        writer.writerow({
            "id": "Y",
            "category": "SALES",
            "question": "Test question 2?",
            "answer": "Test answer 2.",
            "keywords": "test;two",
            "legal_basis": "",
            "notes": "",
        })
    return str(path)


@pytest.fixture
def json_file(tmp_path):
    """Create a sample JSON file for import."""
    path = tmp_path / "import.json"
    data = {
        "items": [
            {
                "id": "X",
                "category": "GENERAL",
                "question": "Test question 1?",
                "answer": "Test answer 1.",
                "keywords": ["test", "question", "one"],
                "legal_basis": ["Law 1", "Law 2"],
                "notes": "note1",
            },
            {
                "id": "Y",
                "category": "SALES",
                "question": "Test question 2?",
                "answer": "Test answer 2.",
                "keywords": ["test", "two"],
                "legal_basis": [],
                "notes": "",
            },
        ]
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


# --- CSV Import/Export roundtrip ---

class TestCSVRoundtrip:
    def test_export_then_import_csv(self, faq_env):
        manager, importer, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "exported.csv")

        # Export
        exporter.export_csv(export_path)
        assert os.path.exists(export_path)

        # Import the exported file
        items = importer.import_csv(export_path)
        assert len(items) == 2
        assert items[0]["id"] == "A"
        assert items[0]["category"] == "GENERAL"
        assert "bonded" in items[0]["keywords"]
        assert items[1]["id"] == "B"

    def test_csv_columns_present(self, faq_env):
        manager, importer, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "exported.csv")
        exporter.export_csv(export_path)

        with open(export_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == set(CSV_COLUMNS)

    def test_csv_keywords_semicolon_separated(self, faq_env):
        manager, importer, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "exported.csv")
        exporter.export_csv(export_path)

        with open(export_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            # Keywords should be semicolon-separated
            assert ";" in row["keywords"]


# --- JSON Import/Export roundtrip ---

class TestJSONRoundtrip:
    def test_export_then_import_json(self, faq_env):
        manager, importer, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "exported.json")

        exporter.export_json(export_path)
        assert os.path.exists(export_path)

        items = importer.import_json(export_path)
        assert len(items) == 2
        assert items[0]["id"] == "A"
        assert items[0]["keywords"] == ["bonded", "exhibition", "definition"]

    def test_json_our_format(self, faq_env, json_file):
        _, importer, _, _ = faq_env
        items = importer.import_json(json_file)
        assert len(items) == 2
        assert items[0]["keywords"] == ["test", "question", "one"]

    def test_json_plain_list(self, faq_env, tmp_path):
        _, importer, _, _ = faq_env
        path = tmp_path / "plain.json"
        data = [
            {"id": "Z", "category": "GENERAL", "question": "Q?", "answer": "A.", "keywords": [], "legal_basis": []},
        ]
        path.write_text(json.dumps(data), encoding="utf-8")
        items = importer.import_json(str(path))
        assert len(items) == 1
        assert items[0]["id"] == "Z"

    def test_json_export_structure(self, faq_env):
        manager, _, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "exported.json")
        exporter.export_json(export_path)

        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "faq_version" in data
        assert "last_updated" in data
        assert "items" in data
        assert len(data["items"]) == 2
        # Should not have enriched fields
        assert "keywords_count" not in data["items"][0]
        assert "last_modified" not in data["items"][0]


# --- Validation ---

class TestValidation:
    def test_missing_required_field(self, faq_env):
        _, importer, _, _ = faq_env
        items = [{"id": "X", "category": "GENERAL", "question": "Q?"}]  # missing answer
        errors = importer.validate_import(items)
        assert len(errors) == 1
        assert "answer" in errors[0]

    def test_invalid_category(self, faq_env):
        _, importer, _, _ = faq_env
        items = [{"id": "X", "category": "INVALID", "question": "Q?", "answer": "A."}]
        errors = importer.validate_import(items)
        assert len(errors) == 1
        assert "INVALID" in errors[0]

    def test_duplicate_ids_in_batch(self, faq_env):
        _, importer, _, _ = faq_env
        items = [
            {"id": "X", "category": "GENERAL", "question": "Q1?", "answer": "A1."},
            {"id": "X", "category": "GENERAL", "question": "Q2?", "answer": "A2."},
        ]
        errors = importer.validate_import(items)
        assert any("duplicate" in e.lower() for e in errors)

    def test_valid_items_no_errors(self, faq_env):
        _, importer, _, _ = faq_env
        items = [
            {"id": "X", "category": "GENERAL", "question": "Q?", "answer": "A."},
            {"id": "Y", "category": "SALES", "question": "Q2?", "answer": "A2."},
        ]
        errors = importer.validate_import(items)
        assert errors == []

    def test_csv_import_validation_error(self, faq_env, tmp_path):
        _, importer, _, _ = faq_env
        path = tmp_path / "bad.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "id": "X", "category": "BADCAT", "question": "Q?",
                "answer": "A.", "keywords": "", "legal_basis": "", "notes": "",
            })
        with pytest.raises(ValueError, match="Validation errors"):
            importer.import_csv(str(path))


# --- Merge strategies ---

class TestMergeStrategies:
    def test_merge_skip(self, faq_env):
        manager, importer, _, _ = faq_env
        items = [
            {"id": "A", "category": "GENERAL", "question": "Updated Q?", "answer": "Updated A."},
            {"id": "C", "category": "GENERAL", "question": "New Q?", "answer": "New A."},
        ]
        result = importer.merge_import(items, strategy="skip")
        assert result["skipped"] == 1  # A exists, skipped
        assert result["created"] == 1  # C is new
        assert result["updated"] == 0

        # A should not be changed
        item_a = manager.get("A")
        assert "bonded" in item_a["answer"].lower()

    def test_merge_overwrite(self, faq_env):
        manager, importer, _, _ = faq_env
        items = [
            {"id": "A", "category": "GENERAL", "question": "Updated Q?", "answer": "Updated A."},
            {"id": "C", "category": "GENERAL", "question": "New Q?", "answer": "New A."},
        ]
        result = importer.merge_import(items, strategy="overwrite")
        assert result["updated"] == 1  # A updated
        assert result["created"] == 1  # C is new

        item_a = manager.get("A")
        assert item_a["question"] == "Updated Q?"

    def test_merge_append(self, faq_env):
        manager, importer, _, _ = faq_env
        items = [
            {"id": "A", "category": "GENERAL", "question": "Duplicate Q?", "answer": "Duplicate A."},
        ]
        result = importer.merge_import(items, strategy="append")
        assert result["created"] == 1  # Created with new ID

        all_items = manager.list_all()
        assert len(all_items) == 3  # Original 2 + 1 new

    def test_invalid_strategy(self, faq_env):
        _, importer, _, _ = faq_env
        with pytest.raises(ValueError, match="Invalid merge strategy"):
            importer.merge_import([], strategy="invalid")


# --- Preview ---

class TestPreview:
    def test_preview_csv(self, faq_env, csv_file):
        _, importer, _, _ = faq_env
        preview = importer.preview_import(csv_file, format="csv")
        assert preview["valid"] is True
        assert preview["count"] == 2
        assert len(preview["items"]) == 2
        assert preview["errors"] == []

    def test_preview_json(self, faq_env, json_file):
        _, importer, _, _ = faq_env
        preview = importer.preview_import(json_file, format="json")
        assert preview["valid"] is True
        assert preview["count"] == 2

    def test_preview_invalid_csv(self, faq_env, tmp_path):
        _, importer, _, _ = faq_env
        path = tmp_path / "bad.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "id": "X", "category": "BADCAT", "question": "",
                "answer": "", "keywords": "", "legal_basis": "", "notes": "",
            })
        preview = importer.preview_import(str(path), format="csv")
        assert preview["valid"] is False
        assert len(preview["errors"]) > 0

    def test_preview_does_not_modify_data(self, faq_env, csv_file):
        manager, importer, _, _ = faq_env
        before_count = len(manager.list_all())
        importer.preview_import(csv_file, format="csv")
        after_count = len(manager.list_all())
        assert before_count == after_count


# --- Excel BOM CSV ---

class TestExcelBOMCSV:
    def test_excel_csv_has_bom(self, faq_env):
        manager, _, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "excel.csv")
        exporter.export_excel_csv(export_path)

        with open(export_path, "rb") as f:
            first_bytes = f.read(3)
        assert first_bytes == b"\xef\xbb\xbf", "Excel CSV should start with UTF-8 BOM"

    def test_excel_csv_readable(self, faq_env):
        manager, importer, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "excel.csv")
        exporter.export_excel_csv(export_path)

        # Should be importable (utf-8-sig handles BOM)
        items = importer.import_csv(export_path)
        assert len(items) == 2

    def test_regular_csv_no_bom(self, faq_env):
        manager, _, exporter, tmp_path = faq_env
        export_path = str(tmp_path / "regular.csv")
        exporter.export_csv(export_path)

        with open(export_path, "rb") as f:
            first_bytes = f.read(3)
        assert first_bytes != b"\xef\xbb\xbf", "Regular CSV should not have BOM"


# --- API endpoint tests ---

class TestFAQIOAPI:
    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_export_csv(self, client):
        res = client.get("/api/admin/faq/export?format=csv")
        assert res.status_code == 200
        assert "text/csv" in res.content_type
        assert "Content-Disposition" in res.headers
        content = res.data.decode("utf-8")
        assert "id,category,question" in content

    def test_export_json(self, client):
        res = client.get("/api/admin/faq/export?format=json")
        assert res.status_code == 200
        assert "application/json" in res.content_type
        data = json.loads(res.data)
        assert "items" in data
        assert len(data["items"]) > 0

    def test_import_no_file(self, client):
        res = client.post("/api/admin/faq/import")
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_import_preview_no_file(self, client):
        res = client.post("/api/admin/faq/import/preview")
        assert res.status_code == 400

    def test_import_preview_csv(self, client, tmp_path):
        # Create a CSV in memory
        csv_content = "id,category,question,answer,keywords,legal_basis,notes\n"
        csv_content += "ZZ,GENERAL,Preview Q?,Preview A.,kw1;kw2,,\n"
        data = {"file": (io.BytesIO(csv_content.encode("utf-8")), "test.csv")}
        res = client.post(
            "/api/admin/faq/import/preview",
            data=data,
            content_type="multipart/form-data",
        )
        assert res.status_code == 200
        result = res.get_json()
        assert result["count"] == 1
        assert result["valid"] is True

    def test_import_csv_file(self, client, tmp_path):
        csv_content = "id,category,question,answer,keywords,legal_basis,notes\n"
        csv_content += "ZZ,GENERAL,Import Q?,Import A.,kw1;kw2,,\n"
        data = {
            "file": (io.BytesIO(csv_content.encode("utf-8")), "test.csv"),
            "strategy": "skip",
        }
        res = client.post(
            "/api/admin/faq/import",
            data=data,
            content_type="multipart/form-data",
        )
        assert res.status_code == 200
        result = res.get_json()
        assert result["success"] is True

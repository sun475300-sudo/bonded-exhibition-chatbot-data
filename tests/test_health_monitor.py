"""헬스 모니터 테스트."""

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.health_monitor import HealthMonitor
from web_server import app


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def monitor(tmp_path):
    """테스트용 HealthMonitor 인스턴스를 생성한다."""
    faq_items = [
        {"id": "1", "question": "Q1", "answer": "A1", "category": "cat1"},
        {"id": "2", "question": "Q2", "answer": "A2", "category": "cat1"},
        {"id": "3", "question": "Q3", "answer": "A3", "category": "cat2"},
    ]
    return HealthMonitor(base_dir=str(tmp_path), faq_items=faq_items)


@pytest.fixture
def monitor_with_db(tmp_path):
    """DB가 있는 테스트용 HealthMonitor."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = logs_dir / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO test_table VALUES (1, 'test')")
    conn.commit()
    conn.close()

    faq_items = [
        {"id": "1", "question": "Q1", "answer": "A1", "category": "cat1"},
    ]
    return HealthMonitor(base_dir=str(tmp_path), faq_items=faq_items)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestCheckDatabase:
    def test_no_db_files(self, monitor):
        result = monitor.check_database()
        assert result["status"] == "healthy"
        assert "timestamp" in result
        assert isinstance(result["details"], dict)

    def test_with_valid_db(self, monitor_with_db):
        result = monitor_with_db.check_database()
        assert result["status"] == "healthy"
        dbs = result["details"]["databases"]
        assert len(dbs) == 1
        assert dbs[0]["integrity"] == "ok"
        assert dbs[0]["table_count"] >= 1

    def test_with_corrupt_db(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        db_path = logs_dir / "bad.db"
        db_path.write_text("this is not a valid sqlite file")
        monitor = HealthMonitor(base_dir=str(tmp_path))
        result = monitor.check_database()
        assert result["status"] == "unhealthy"


class TestCheckFaqData:
    def test_with_faq_items(self, monitor):
        result = monitor.check_faq_data()
        assert result["status"] in ("healthy", "degraded")
        assert result["details"]["count"] == 3
        assert result["details"]["category_count"] == 2

    def test_empty_faq(self, tmp_path):
        monitor = HealthMonitor(base_dir=str(tmp_path), faq_items=[])
        result = monitor.check_faq_data()
        assert result["status"] == "unhealthy"
        assert result["details"]["count"] == 0

    def test_incomplete_faq(self, tmp_path):
        items = [
            {"id": "1", "question": "Q1", "answer": ""},
            {"id": "2", "question": "Q2", "answer": "A2", "category": "c"},
        ]
        monitor = HealthMonitor(base_dir=str(tmp_path), faq_items=items)
        result = monitor.check_faq_data()
        assert result["status"] == "degraded"
        assert result["details"]["incomplete"] == 1

    def test_sufficient_faq(self, tmp_path):
        items = [
            {"id": str(i), "question": f"Q{i}", "answer": f"A{i}", "category": "c"}
            for i in range(20)
        ]
        monitor = HealthMonitor(base_dir=str(tmp_path), faq_items=items)
        result = monitor.check_faq_data()
        assert result["status"] == "healthy"


class TestCheckDiskSpace:
    def test_disk_space(self, monitor):
        result = monitor.check_disk_space()
        assert result["status"] in ("healthy", "degraded", "unhealthy")
        assert "usage_percent" in result["details"]
        assert "free_gb" in result["details"]
        assert "timestamp" in result


class TestCheckMemoryUsage:
    def test_memory_usage(self, monitor):
        result = monitor.check_memory_usage()
        assert result["status"] in ("healthy", "degraded", "unhealthy")
        assert "timestamp" in result


class TestCheckResponseTimes:
    def test_no_data(self, monitor):
        result = monitor.check_response_times()
        assert result["status"] == "healthy"
        assert result["details"]["count"] == 0

    def test_with_recorded_times(self, monitor):
        for _ in range(10):
            monitor.record_request(0.1)
        result = monitor.check_response_times()
        assert result["status"] == "healthy"
        assert result["details"]["avg_ms"] == pytest.approx(100.0, abs=5)

    def test_slow_response_degraded(self, monitor):
        for _ in range(10):
            monitor.record_request(3.5)
        result = monitor.check_response_times()
        assert result["status"] == "degraded"

    def test_very_slow_response_unhealthy(self, monitor):
        for _ in range(10):
            monitor.record_request(6.0)
        result = monitor.check_response_times()
        assert result["status"] == "unhealthy"


class TestCheckErrorRate:
    def test_no_requests(self, monitor):
        result = monitor.check_error_rate()
        assert result["status"] == "healthy"
        assert result["details"]["total_requests"] == 0

    def test_low_error_rate(self, monitor):
        for _ in range(100):
            monitor.record_request(0.1, is_error=False)
        monitor.record_request(0.1, is_error=True)
        result = monitor.check_error_rate()
        assert result["status"] == "healthy"
        assert result["details"]["error_rate_percent"] < 5

    def test_high_error_rate(self, monitor):
        for _ in range(5):
            monitor.record_request(0.1, is_error=False)
        for _ in range(5):
            monitor.record_request(0.1, is_error=True)
        result = monitor.check_error_rate()
        assert result["status"] == "unhealthy"
        assert result["details"]["error_rate_percent"] >= 10


class TestCheckAll:
    def test_overall_healthy(self, monitor):
        result = monitor.check_all()
        assert result["status"] in ("healthy", "degraded")
        assert "components" in result
        assert "timestamp" in result
        assert result["total_components"] == 6
        assert result["healthy_components"] <= result["total_components"]

    def test_unhealthy_propagation(self, tmp_path):
        """unhealthy 상태인 컴포넌트가 있으면 전체가 unhealthy."""
        monitor = HealthMonitor(base_dir=str(tmp_path), faq_items=[])
        result = monitor.check_all()
        assert result["status"] in ("unhealthy", "degraded")

    def test_degraded_propagation(self, tmp_path):
        """degraded만 있으면 전체가 degraded."""
        items = [
            {"id": "1", "question": "Q1", "answer": "", "category": "c"},
            {"id": "2", "question": "Q2", "answer": "A2", "category": "c"},
        ]
        monitor = HealthMonitor(base_dir=str(tmp_path), faq_items=items)
        result = monitor.check_all()
        assert result["status"] in ("degraded", "unhealthy")


class TestGetSystemInfo:
    def test_system_info_fields(self, monitor):
        info = monitor.get_system_info()
        assert "python_version" in info
        assert "platform" in info
        assert "os" in info
        assert "uptime_seconds" in info
        assert "uptime_formatted" in info
        assert "faq_count" in info
        assert "pid" in info
        assert info["faq_count"] == 3

    def test_uptime_increases(self, monitor):
        info1 = monitor.get_system_info()
        time.sleep(0.05)
        info2 = monitor.get_system_info()
        assert info2["uptime_seconds"] >= info1["uptime_seconds"]


class TestResultFormat:
    def test_result_has_required_fields(self, monitor):
        """모든 검사 결과에 status, message, details, timestamp가 있어야 한다."""
        checks = [
            monitor.check_database(),
            monitor.check_faq_data(),
            monitor.check_disk_space(),
            monitor.check_memory_usage(),
            monitor.check_response_times(),
            monitor.check_error_rate(),
        ]
        for result in checks:
            assert "status" in result
            assert result["status"] in ("healthy", "degraded", "unhealthy")
            assert "message" in result
            assert "details" in result
            assert "timestamp" in result


class TestRecordRequest:
    def test_record_limits_to_1000(self, monitor):
        for i in range(1100):
            monitor.record_request(0.01)
        assert len(monitor._response_times) == 1000


class TestHealthAPIEndpoints:
    def test_health_detailed(self, client):
        res = client.get("/api/admin/health/detailed")
        assert res.status_code == 200
        data = res.get_json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert "components" in data
        assert "system_info" in data
        assert "timestamp" in data

    def test_health_components(self, client):
        res = client.get("/api/admin/health/components")
        assert res.status_code == 200
        data = res.get_json()
        assert "database" in data
        assert "faq_data" in data
        assert "disk_space" in data

    def test_health_single_component(self, client):
        res = client.get("/api/admin/health/components?component=database")
        assert res.status_code == 200
        data = res.get_json()
        assert "database" in data
        assert "status" in data["database"]

    def test_health_unknown_component(self, client):
        res = client.get("/api/admin/health/components?component=unknown")
        assert res.status_code == 400

    def test_health_dashboard_page(self, client):
        res = client.get("/health-dashboard")
        assert res.status_code == 200
        assert b"Health Dashboard" in res.data

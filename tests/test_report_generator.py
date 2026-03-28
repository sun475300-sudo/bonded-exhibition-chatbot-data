"""ReportGenerator 테스트."""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.feedback import FeedbackManager
from src.logger_db import ChatLogger
from src.report_generator import ReportGenerator


@pytest.fixture
def temp_dbs():
    """임시 DB 파일로 ChatLogger와 FeedbackManager를 생성한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_db = ChatLogger(db_path=os.path.join(tmpdir, "chat_logs.db"))
        fb_db = FeedbackManager(db_path=os.path.join(tmpdir, "feedback.db"))
        yield log_db, fb_db
        log_db.close()
        fb_db.close()


@pytest.fixture
def report_gen(temp_dbs):
    """ReportGenerator 인스턴스를 생성한다."""
    log_db, fb_db = temp_dbs
    return ReportGenerator(log_db, fb_db)


@pytest.fixture
def report_gen_no_feedback(temp_dbs):
    """피드백 DB 없는 ReportGenerator 인스턴스를 생성한다."""
    log_db, _ = temp_dbs
    return ReportGenerator(log_db, feedback_db=None)


@pytest.fixture
def report_gen_with_data(temp_dbs):
    """데이터가 있는 ReportGenerator 인스턴스를 생성한다."""
    log_db, fb_db = temp_dbs
    today = datetime.now().strftime("%Y-%m-%d")

    # Insert log data using direct SQL to set specific timestamps
    conn = log_db._get_conn()
    entries = [
        (f"{today} 09:15:00", "보세전시장이 무엇인가요?", "GENERAL", "FAQ_001", 0),
        (f"{today} 09:30:00", "물품 반입 절차는?", "IMPORT_EXPORT", "FAQ_002", 0),
        (f"{today} 10:00:00", "관세 납부 방법은?", "TAX", None, 0),
        (f"{today} 10:15:00", "담당자와 통화하고 싶습니다", "GENERAL", None, 1),
        (f"{today} 14:00:00", "전시 기간은 얼마나 되나요?", "EXHIBITION", "FAQ_003", 0),
        (f"{today} 14:30:00", "보세전시장이 무엇인가요?", "GENERAL", "FAQ_001", 0),
        (f"{today} 15:00:00", "반출 절차가 궁금합니다", "IMPORT_EXPORT", None, 0),
        (f"{today} 16:00:00", "보세전시장이 무엇인가요?", "GENERAL", "FAQ_001", 0),
    ]
    for ts, query, cat, faq_id, esc in entries:
        conn.execute(
            """INSERT INTO chat_logs
               (timestamp, query, category, faq_id, is_escalation, response_preview)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, query, cat, faq_id, esc, None),
        )
    conn.commit()

    # Feedback data
    fb_conn = fb_db._get_conn()
    for qid, rating in [("Q1", "helpful"), ("Q2", "helpful"), ("Q3", "unhelpful"),
                         ("Q4", "helpful"), ("Q5", "helpful")]:
        fb_conn.execute(
            "INSERT INTO feedback (query_id, timestamp, rating, comment) VALUES (?, ?, ?, ?)",
            (qid, f"{today} 12:00:00", rating, ""),
        )
    fb_conn.commit()

    return ReportGenerator(log_db, fb_db)


class TestDailyReport:
    """일별 리포트 생성 테스트."""

    def test_daily_report_structure(self, report_gen_with_data):
        """일별 리포트에 필수 키가 존재하는지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        assert report["period"] == "daily"
        assert "total_queries" in report
        assert "unique_sessions" in report
        assert "avg_queries_per_session" in report
        assert "category_distribution" in report
        assert "top_questions" in report
        assert "escalation_rate" in report
        assert "escalation_reasons" in report
        assert "match_rate" in report
        assert "satisfaction" in report
        assert "peak_hours" in report
        assert "unmatched_queries" in report
        assert "generated_at" in report
        assert "start_date" in report
        assert "end_date" in report

    def test_daily_report_counts(self, report_gen_with_data):
        """일별 리포트의 질문 수가 정확한지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        assert report["total_queries"] == 8

    def test_daily_report_category_distribution(self, report_gen_with_data):
        """카테고리 분포가 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        cats = {c["category"]: c["count"] for c in report["category_distribution"]}
        assert cats["GENERAL"] == 4
        assert cats["IMPORT_EXPORT"] == 2
        assert cats["TAX"] == 1
        assert cats["EXHIBITION"] == 1

    def test_daily_report_top_questions(self, report_gen_with_data):
        """상위 질문이 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        top = report["top_questions"]
        assert len(top) > 0
        # 가장 많이 물어본 질문
        assert top[0]["query"] == "보세전시장이 무엇인가요?"
        assert top[0]["count"] == 3

    def test_daily_report_escalation(self, report_gen_with_data):
        """에스컬레이션 비율이 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        assert report["escalation_count"] == 1
        assert report["escalation_rate"] == 12.5  # 1/8 * 100

    def test_daily_report_match_rate(self, report_gen_with_data):
        """매칭률이 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        mr = report["match_rate"]
        assert mr["matched"] == 5
        assert mr["unmatched"] == 3

    def test_daily_report_satisfaction(self, report_gen_with_data):
        """만족도 데이터가 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        sat = report["satisfaction"]
        assert sat is not None
        assert sat["total_feedback"] == 5
        assert sat["helpful_count"] == 4
        assert sat["unhelpful_count"] == 1
        assert sat["helpful_rate"] == 80.0

    def test_daily_report_peak_hours(self, report_gen_with_data):
        """피크 시간대가 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        peak = report["peak_hours"]
        assert len(peak["hours"]) == 24
        assert peak["peak_hour"] == 9  # 9시에 2건
        assert peak["peak_count"] == 2

    def test_daily_report_specific_date(self, report_gen_with_data):
        """특정 날짜의 리포트를 생성할 수 있는지 확인한다."""
        report = report_gen_with_data.generate_daily_report(date="2020-01-01")
        assert report["total_queries"] == 0
        assert report["start_date"] == "2020-01-01"
        assert report["end_date"] == "2020-01-01"

    def test_daily_report_unmatched_queries(self, report_gen_with_data):
        """미매칭 질문 요약이 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        unmatched = report["unmatched_queries"]
        assert len(unmatched) == 3
        queries = {u["query"] for u in unmatched}
        assert "관세 납부 방법은?" in queries
        assert "담당자와 통화하고 싶습니다" in queries
        assert "반출 절차가 궁금합니다" in queries


class TestWeeklyReport:
    """주별 리포트 생성 테스트."""

    def test_weekly_report_structure(self, report_gen_with_data):
        """주별 리포트가 올바른 기간을 포함하는지 확인한다."""
        report = report_gen_with_data.generate_weekly_report()
        assert report["period"] == "weekly"
        # start_date와 end_date는 7일 차이
        start = datetime.strptime(report["start_date"], "%Y-%m-%d")
        end = datetime.strptime(report["end_date"], "%Y-%m-%d")
        assert (end - start).days == 6

    def test_weekly_report_with_start(self, report_gen_with_data):
        """특정 시작일로 주별 리포트를 생성할 수 있는지 확인한다."""
        today = datetime.now().strftime("%Y-%m-%d")
        report = report_gen_with_data.generate_weekly_report(week_start=today)
        assert report["start_date"] == today

    def test_weekly_report_has_data(self, report_gen_with_data):
        """이번 주 시작일 기준으로 데이터가 포함되는지 확인한다."""
        # 오늘 날짜를 포함하는 주의 월요일로 시작
        today = datetime.now()
        monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        report = report_gen_with_data.generate_weekly_report(week_start=monday)
        assert report["total_queries"] == 8


class TestMonthlyReport:
    """월별 리포트 생성 테스트."""

    def test_monthly_report_structure(self, report_gen_with_data):
        """월별 리포트의 기간이 올바른지 확인한다."""
        now = datetime.now()
        report = report_gen_with_data.generate_monthly_report(now.year, now.month)
        assert report["period"] == "monthly"
        assert report["start_date"] == f"{now.year:04d}-{now.month:02d}-01"

    def test_monthly_report_has_data(self, report_gen_with_data):
        """이번 달 리포트에 데이터가 포함되는지 확인한다."""
        now = datetime.now()
        report = report_gen_with_data.generate_monthly_report(now.year, now.month)
        assert report["total_queries"] == 8

    def test_monthly_report_empty_month(self, report_gen_with_data):
        """데이터 없는 월 리포트가 빈 결과를 반환하는지 확인한다."""
        report = report_gen_with_data.generate_monthly_report(2020, 1)
        assert report["total_queries"] == 0

    def test_monthly_report_december(self, report_gen):
        """12월 리포트의 종료일이 올바른지 확인한다."""
        report = report_gen.generate_monthly_report(2024, 12)
        assert report["end_date"] == "2024-12-31"

    def test_monthly_report_february(self, report_gen):
        """2월 리포트 종료일이 올바른지 확인한다 (윤년/비윤년)."""
        report = report_gen.generate_monthly_report(2024, 2)
        assert report["end_date"] == "2024-02-29"  # 윤년

        report = report_gen.generate_monthly_report(2023, 2)
        assert report["end_date"] == "2023-02-28"  # 비윤년


class TestEmptyData:
    """빈 데이터 처리 테스트."""

    def test_empty_daily_report(self, report_gen):
        """데이터 없는 일별 리포트가 정상 생성되는지 확인한다."""
        report = report_gen.generate_daily_report(date="2020-01-01")
        assert report["total_queries"] == 0
        assert report["unique_sessions"] == 0
        assert report["avg_queries_per_session"] == 0
        assert report["category_distribution"] == []
        assert report["top_questions"] == []
        assert report["escalation_rate"] == 0
        assert report["escalation_count"] == 0
        assert report["escalation_reasons"] == []
        assert report["match_rate"]["matched"] == 0
        assert report["match_rate"]["unmatched"] == 0
        assert report["satisfaction"] is None
        assert report["unmatched_queries"] == []

    def test_empty_weekly_report(self, report_gen):
        """데이터 없는 주별 리포트가 정상 생성되는지 확인한다."""
        report = report_gen.generate_weekly_report(week_start="2020-01-06")
        assert report["total_queries"] == 0

    def test_empty_monthly_report(self, report_gen):
        """데이터 없는 월별 리포트가 정상 생성되는지 확인한다."""
        report = report_gen.generate_monthly_report(2020, 1)
        assert report["total_queries"] == 0

    def test_no_feedback_db(self, report_gen_no_feedback):
        """피드백 DB 없이도 리포트가 생성되는지 확인한다."""
        report = report_gen_no_feedback.generate_daily_report()
        assert report["satisfaction"] is None


class TestHTMLExport:
    """HTML 내보내기 테스트."""

    def test_export_html_creates_file(self, report_gen_with_data):
        """HTML 파일이 생성되는지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.html")
            report_gen_with_data.export_html(report, path)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            assert len(html) > 0

    def test_html_contains_sections(self, report_gen_with_data):
        """HTML에 주요 섹션이 포함되는지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.html")
            report_gen_with_data.export_html(report, path)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            assert "<!DOCTYPE html>" in html
            assert "Category Distribution" in html
            assert "Top 10 Most Asked Questions" in html
            assert "Escalation Analysis" in html
            assert "Match Rate" in html
            assert "Peak Hours" in html
            assert "Unmatched Queries" in html
            assert "Total Queries" in html
            assert "Satisfaction Score" in html

    def test_html_contains_data(self, report_gen_with_data):
        """HTML에 실제 데이터가 포함되는지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.html")
            report_gen_with_data.export_html(report, path)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            assert "GENERAL" in html
            assert "IMPORT_EXPORT" in html

    def test_html_empty_data(self, report_gen):
        """빈 데이터 HTML이 정상 생성되는지 확인한다."""
        report = report_gen.generate_daily_report(date="2020-01-01")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.html")
            report_gen.export_html(report, path)
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            assert "<!DOCTYPE html>" in html
            assert "No data" in html

    def test_html_creates_directory(self, report_gen):
        """출력 경로의 디렉토리가 자동 생성되는지 확인한다."""
        report = report_gen.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "report.html")
            report_gen.export_html(report, path)
            assert os.path.exists(path)


class TestJSONExport:
    """JSON 내보내기 테스트."""

    def test_export_json_creates_file(self, report_gen_with_data):
        """JSON 파일이 생성되는지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.json")
            report_gen_with_data.export_json(report, path)
            assert os.path.exists(path)

    def test_json_structure(self, report_gen_with_data):
        """JSON 구조가 올바른지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.json")
            report_gen_with_data.export_json(report, path)
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["period"] == "daily"
            assert loaded["total_queries"] == 8
            assert isinstance(loaded["category_distribution"], list)
            assert isinstance(loaded["top_questions"], list)
            assert isinstance(loaded["match_rate"], dict)
            assert isinstance(loaded["peak_hours"], dict)

    def test_json_roundtrip(self, report_gen_with_data):
        """JSON 내보내기 후 다시 읽을 때 데이터가 동일한지 확인한다."""
        report = report_gen_with_data.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.json")
            report_gen_with_data.export_json(report, path)
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["total_queries"] == report["total_queries"]
            assert loaded["escalation_rate"] == report["escalation_rate"]
            assert len(loaded["category_distribution"]) == len(report["category_distribution"])

    def test_json_creates_directory(self, report_gen):
        """출력 경로의 디렉토리가 자동 생성되는지 확인한다."""
        report = report_gen.generate_daily_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "report.json")
            report_gen.export_json(report, path)
            assert os.path.exists(path)

    def test_json_empty_data(self, report_gen):
        """빈 데이터 JSON이 정상 생성되는지 확인한다."""
        report = report_gen.generate_daily_report(date="2020-01-01")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "report.json")
            report_gen.export_json(report, path)
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["total_queries"] == 0


class TestAPIEndpoints:
    """API 엔드포인트 테스트."""

    @pytest.fixture
    def client(self):
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    @pytest.fixture
    def auth_header(self):
        """테스트용 인증 헤더 (JWT 인증 우회)."""
        from web_server import jwt_auth
        token = jwt_auth.generate_token("admin")
        return {"Authorization": f"Bearer {token}"}

    def test_daily_report_endpoint(self, client, auth_header):
        """일별 리포트 엔드포인트가 정상 응답하는지 확인한다."""
        res = client.get("/api/admin/reports/daily", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert "total_queries" in data
        assert "category_distribution" in data
        assert data["period"] == "daily"

    def test_daily_report_with_date(self, client, auth_header):
        """날짜 파라미터가 적용되는지 확인한다."""
        res = client.get("/api/admin/reports/daily?date=2020-01-01",
                         headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert data["start_date"] == "2020-01-01"

    def test_weekly_report_endpoint(self, client, auth_header):
        """주별 리포트 엔드포인트가 정상 응답하는지 확인한다."""
        res = client.get("/api/admin/reports/weekly", headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert data["period"] == "weekly"
        assert "total_queries" in data

    def test_weekly_report_with_start(self, client, auth_header):
        """시작일 파라미터가 적용되는지 확인한다."""
        res = client.get("/api/admin/reports/weekly?start=2024-01-01",
                         headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert data["start_date"] == "2024-01-01"

    def test_monthly_report_endpoint(self, client, auth_header):
        """월별 리포트 엔드포인트가 정상 응답하는지 확인한다."""
        res = client.get("/api/admin/reports/monthly?year=2024&month=6",
                         headers=auth_header)
        assert res.status_code == 200
        data = res.get_json()
        assert data["period"] == "monthly"
        assert data["start_date"] == "2024-06-01"

    def test_monthly_report_missing_params(self, client, auth_header):
        """year/month 누락 시 400 에러를 반환하는지 확인한다."""
        res = client.get("/api/admin/reports/monthly", headers=auth_header)
        assert res.status_code == 400

    def test_monthly_report_invalid_month(self, client, auth_header):
        """잘못된 month 값에 400 에러를 반환하는지 확인한다."""
        res = client.get("/api/admin/reports/monthly?year=2024&month=13",
                         headers=auth_header)
        assert res.status_code == 400

    def test_html_report_endpoint(self, client, auth_header):
        """HTML 리포트 다운로드 엔드포인트가 정상 응답하는지 확인한다."""
        res = client.get("/api/admin/reports/html?type=daily",
                         headers=auth_header)
        assert res.status_code == 200
        assert "text/html" in res.content_type
        html = res.data.decode("utf-8")
        assert "<!DOCTYPE html>" in html
        assert "Category Distribution" in html

    def test_html_report_weekly(self, client, auth_header):
        """주별 HTML 리포트가 정상 생성되는지 확인한다."""
        res = client.get("/api/admin/reports/html?type=weekly",
                         headers=auth_header)
        assert res.status_code == 200
        assert "text/html" in res.content_type

    def test_html_report_invalid_type(self, client, auth_header):
        """잘못된 타입에 400 에러를 반환하는지 확인한다."""
        res = client.get("/api/admin/reports/html?type=invalid",
                         headers=auth_header)
        assert res.status_code == 400

    def test_unauthenticated_request(self):
        """인증 없는 요청이 401을 반환하는지 확인한다."""
        from web_server import app
        app.config["TESTING"] = True
        app.config["AUTH_TESTING"] = True
        with app.test_client() as client:
            res = client.get("/api/admin/reports/daily")
            assert res.status_code == 401
        app.config["AUTH_TESTING"] = False

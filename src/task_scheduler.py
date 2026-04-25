"""주기적 작업 스케줄러 모듈.

CronParser를 사용하여 cron 표현식을 파싱하고,
등록된 작업을 주기적으로 실행한다.
실행 기록은 SQLite에 저장된다.
"""

import logging
import os
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta

logger = logging.getLogger("task_scheduler")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "scheduler.db")


class CronParser:
    """순수 파이썬 cron 표현식 파서.

    지원 형식: minute hour day month weekday
    지원 패턴: *, 범위(1-5), 리스트(1,3,5), 스텝(*/5, 1-10/2)
    """

    FIELD_NAMES = ["minute", "hour", "day", "month", "weekday"]
    FIELD_RANGES = {
        "minute": (0, 59),
        "hour": (0, 23),
        "day": (1, 31),
        "month": (1, 12),
        "weekday": (0, 6),  # 0=Monday, 6=Sunday
    }

    @classmethod
    def parse(cls, expr):
        """cron 표현식을 파싱하여 각 필드의 허용 값 집합을 반환한다.

        Args:
            expr: cron 표현식 문자열 (5개 필드)

        Returns:
            dict: 각 필드 이름을 키로, 허용 값의 set을 값으로 갖는 딕셔너리
        """
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: expected 5 fields, got {len(parts)}")

        result = {}
        for i, field_name in enumerate(cls.FIELD_NAMES):
            low, high = cls.FIELD_RANGES[field_name]
            result[field_name] = cls._parse_field(parts[i], low, high)
        return result

    @classmethod
    def _parse_field(cls, field, low, high):
        """단일 필드를 파싱하여 허용 값의 set을 반환한다."""
        values = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                range_part, step_str = part.split("/", 1)
                step = int(step_str)
                if step <= 0:
                    raise ValueError(f"Invalid step value: {step}")
            else:
                range_part = part

            if range_part == "*":
                start, end = low, high
            elif "-" in range_part:
                s, e = range_part.split("-", 1)
                start, end = int(s), int(e)
            else:
                start = int(range_part)
                end = start

            if start < low or end > high:
                raise ValueError(
                    f"Value out of range [{low}-{high}]: {range_part}"
                )

            for v in range(start, end + 1, step):
                values.add(v)

        return values

    @classmethod
    def matches(cls, expr, dt):
        """datetime이 cron 표현식과 일치하는지 확인한다.

        Args:
            expr: cron 표현식 문자열
            dt: datetime 객체

        Returns:
            bool: 일치 여부
        """
        parsed = cls.parse(expr)
        # Python weekday: 0=Monday, 6=Sunday
        return (
            dt.minute in parsed["minute"]
            and dt.hour in parsed["hour"]
            and dt.day in parsed["day"]
            and dt.month in parsed["month"]
            and dt.weekday() in parsed["weekday"]
        )

    @classmethod
    def next_run(cls, expr, after=None):
        """다음 실행 시각을 계산한다.

        Args:
            expr: cron 표현식 문자열
            after: 기준 시각 (기본: 현재 시각)

        Returns:
            datetime: 다음 실행 시각
        """
        parsed = cls.parse(expr)
        if after is None:
            after = datetime.now()

        # Start from next minute
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Search up to ~4 years to find a match
        max_iterations = 525960  # ~366 days * 24 * 60
        for _ in range(max_iterations):
            if (
                candidate.minute in parsed["minute"]
                and candidate.hour in parsed["hour"]
                and candidate.day in parsed["day"]
                and candidate.month in parsed["month"]
                and candidate.weekday() in parsed["weekday"]
            ):
                return candidate

            # Smart advancement: skip ahead when possible
            if candidate.month not in parsed["month"]:
                # Jump to next valid month
                candidate = cls._advance_month(candidate, parsed["month"])
                continue
            if candidate.day not in parsed["day"]:
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if candidate.hour not in parsed["hour"]:
                candidate = candidate.replace(minute=0) + timedelta(hours=1)
                continue

            candidate += timedelta(minutes=1)

        raise ValueError(f"Could not find next run time for expression: {expr}")

    @classmethod
    def _advance_month(cls, dt, valid_months):
        """다음 유효한 월로 건너뛴다."""
        month = dt.month
        year = dt.year
        for _ in range(24):  # max 2 years
            month += 1
            if month > 12:
                month = 1
                year += 1
            if month in valid_months:
                return datetime(year, month, 1, 0, 0)
        return dt + timedelta(days=31)


class TaskScheduler:
    """주기적 작업 스케줄러.

    cron 표현식 기반으로 등록된 작업을 주기적으로 실행하고,
    실행 이력을 SQLite에 기록한다.
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._tasks = {}
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """실행 로그 테이블을 초기화한다."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exec_log_task
                ON execution_log(task_name, started_at)
            """)
            conn.commit()
        finally:
            conn.close()

    def register_task(self, name, func, cron_expr, enabled=True):
        """주기적 작업을 등록한다.

        Args:
            name: 작업 이름 (고유 식별자)
            func: 실행할 callable
            cron_expr: cron 표현식
            enabled: 활성화 여부
        """
        # Validate cron expression
        CronParser.parse(cron_expr)

        with self._lock:
            self._tasks[name] = {
                "func": func,
                "cron_expr": cron_expr,
                "enabled": enabled,
                "last_run": None,
                "success_count": 0,
                "fail_count": 0,
            }
        logger.info(f"Task registered: {name} ({cron_expr}), enabled={enabled}")

    def unregister_task(self, name):
        """작업을 제거한다.

        Args:
            name: 제거할 작업 이름

        Raises:
            KeyError: 작업이 존재하지 않을 경우
        """
        with self._lock:
            if name not in self._tasks:
                raise KeyError(f"Task not found: {name}")
            del self._tasks[name]
        logger.info(f"Task unregistered: {name}")

    def list_tasks(self):
        """등록된 모든 작업 목록을 반환한다.

        Returns:
            list[dict]: 작업 정보 리스트
        """
        result = []
        with self._lock:
            for name, task in self._tasks.items():
                try:
                    next_time = CronParser.next_run(task["cron_expr"])
                    next_run_str = next_time.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    next_run_str = None

                result.append({
                    "name": name,
                    "cron_expr": task["cron_expr"],
                    "enabled": task["enabled"],
                    "last_run": task["last_run"],
                    "next_run": next_run_str,
                    "success_count": task["success_count"],
                    "fail_count": task["fail_count"],
                })
        return result

    def run_task(self, name):
        """작업을 수동으로 실행한다.

        Args:
            name: 실행할 작업 이름

        Returns:
            dict: 실행 결과 (status, result 또는 error)

        Raises:
            KeyError: 작업이 존재하지 않을 경우
        """
        with self._lock:
            if name not in self._tasks:
                raise KeyError(f"Task not found: {name}")
            task = self._tasks[name]

        return self._execute_task(name, task)

    def _execute_task(self, name, task):
        """작업을 실행하고 결과를 DB에 기록한다."""
        started_at = datetime.now()
        started_str = started_at.strftime("%Y-%m-%d %H:%M:%S")

        try:
            result = task["func"]()
            finished_at = datetime.now()
            finished_str = finished_at.strftime("%Y-%m-%d %H:%M:%S")
            result_str = str(result) if result is not None else None

            self._log_execution(name, started_str, finished_str, "success", result_str, None)

            with self._lock:
                if name in self._tasks:
                    self._tasks[name]["last_run"] = started_str
                    self._tasks[name]["success_count"] += 1

            logger.info(f"Task '{name}' completed successfully")
            return {"status": "success", "result": result_str}

        except Exception as e:
            finished_at = datetime.now()
            finished_str = finished_at.strftime("%Y-%m-%d %H:%M:%S")
            error_str = traceback.format_exc()

            self._log_execution(name, started_str, finished_str, "error", None, error_str)

            with self._lock:
                if name in self._tasks:
                    self._tasks[name]["last_run"] = started_str
                    self._tasks[name]["fail_count"] += 1

            logger.error(f"Task '{name}' failed: {e}")
            return {"status": "error", "error": str(e)}

    def _log_execution(self, task_name, started_at, finished_at, status, result, error):
        """실행 결과를 DB에 기록한다."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT INTO execution_log
                       (task_name, started_at, finished_at, status, result, error)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (task_name, started_at, finished_at, status, result, error),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Failed to log execution for '{task_name}': {e}")

    def get_execution_log(self, task_name=None, limit=50):
        """실행 이력을 조회한다.

        Args:
            task_name: 특정 작업만 조회 (None이면 전체)
            limit: 최대 반환 수

        Returns:
            list[dict]: 실행 이력
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if task_name:
                rows = conn.execute(
                    """SELECT * FROM execution_log
                       WHERE task_name = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (task_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM execution_log
                       ORDER BY started_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()

            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_task_status(self, name):
        """작업의 현재 상태를 반환한다.

        Args:
            name: 작업 이름

        Returns:
            dict: 상태 정보

        Raises:
            KeyError: 작업이 존재하지 않을 경우
        """
        with self._lock:
            if name not in self._tasks:
                raise KeyError(f"Task not found: {name}")
            task = self._tasks[name]

            try:
                next_time = CronParser.next_run(task["cron_expr"])
                next_run_str = next_time.strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run_str = None

            return {
                "name": name,
                "cron_expr": task["cron_expr"],
                "enabled": task["enabled"],
                "last_run": task["last_run"],
                "next_run": next_run_str,
                "success_count": task["success_count"],
                "fail_count": task["fail_count"],
            }

    def start(self):
        """스케줄러를 시작한다 (60초 간격으로 작업 확인)."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("TaskScheduler started")

    def stop(self):
        """스케줄러를 중지한다."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("TaskScheduler stopped")

    def _run_loop(self):
        """메인 스케줄러 루프."""
        while self._running:
            try:
                self._check_and_run()
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")

            # Sleep in small increments so stop() works quickly
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_run(self):
        """현재 시각에 실행할 작업을 확인하고 실행한다."""
        now = datetime.now()
        with self._lock:
            tasks_snapshot = [
                (name, dict(task))
                for name, task in self._tasks.items()
                if task["enabled"]
            ]

        for name, task in tasks_snapshot:
            try:
                if CronParser.matches(task["cron_expr"], now):
                    logger.info(f"Cron match for task '{name}', executing...")
                    self._execute_task(name, task)
            except Exception as e:
                logger.error(f"Error checking task '{name}': {e}")

    def set_task_enabled(self, name, enabled):
        """작업의 활성화 상태를 변경한다.

        Args:
            name: 작업 이름
            enabled: 활성화 여부

        Raises:
            KeyError: 작업이 존재하지 않을 경우
        """
        with self._lock:
            if name not in self._tasks:
                raise KeyError(f"Task not found: {name}")
            self._tasks[name]["enabled"] = enabled
        logger.info(f"Task '{name}' enabled={enabled}")


def create_default_scheduler(db_path=None):
    """기본 작업이 등록된 스케줄러를 생성한다.

    Pre-registered tasks:
    - daily_backup: 매일 02:00 백업
    - weekly_report: 매주 월요일 08:00 리포트
    - law_check: 매일 06:00 법령 업데이트 확인
    - cleanup_old_logs: 매월 1일 03:00 오래된 로그 정리
    - faq_quality_check: 매일 09:00 FAQ 품질 확인
    """
    scheduler = TaskScheduler(db_path=db_path)

    def _daily_backup():
        try:
            bm = BackupManager()
            return bm.create_backup()
        except Exception as e:
            logger.error(f"Backup task failed: {e}")
            raise

    def _weekly_report():
        try:
            chat_logger = ChatLogger()
            rg = ReportGenerator(chat_logger)
            return rg.generate_weekly_report()
        except Exception as e:
            logger.error(f"Weekly report task failed: {e}")
            raise

    def _law_check():
        try:
            lus = LawUpdateScheduler()
            return lus.check_for_updates()
        except Exception as e:
            logger.error(f"Law check task failed: {e}")
            raise

    def _cleanup_old_logs():
        try:
            db_path_log = os.path.join(BASE_DIR, "data", "scheduler.db")
            conn = sqlite3.connect(db_path_log)
            try:
                cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
                cursor = conn.execute(
                    "DELETE FROM execution_log WHERE started_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return f"Deleted {cursor.rowcount} old log entries"
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cleanup task failed: {e}")
            raise

    def _faq_quality_check():
        try:
            from src.utils import load_json
            faq_data = load_json(os.path.join(BASE_DIR, "data", "faq.json"))
            legal_data = load_json(os.path.join(BASE_DIR, "data", "legal_references.json"))
            faq_items = faq_data if isinstance(faq_data, list) else faq_data.get("items", [])
            checker = FAQQualityChecker(faq_items, legal_data)
            return checker.check_all()
        except Exception as e:
            logger.error(f"FAQ quality check task failed: {e}")
            raise

    # 매일 02:00
    scheduler.register_task("daily_backup", _daily_backup, "0 2 * * *")
    # 매주 월요일 08:00 (weekday 0 = Monday)
    scheduler.register_task("weekly_report", _weekly_report, "0 8 * * 0")
    # 매일 06:00
    scheduler.register_task("law_check", _law_check, "0 6 * * *")
    # 매월 1일 03:00
    scheduler.register_task("cleanup_old_logs", _cleanup_old_logs, "0 3 1 * *")
    # 매일 09:00
    scheduler.register_task("faq_quality_check", _faq_quality_check, "0 9 * * *")

    return scheduler


# Lazy imports for default task functions
from src.backup_manager import BackupManager  # noqa: E402
from src.logger_db import ChatLogger  # noqa: E402
from src.report_generator import ReportGenerator  # noqa: E402
from src.law_updater import LawUpdateScheduler  # noqa: E402
from src.faq_quality_checker import FAQQualityChecker  # noqa: E402

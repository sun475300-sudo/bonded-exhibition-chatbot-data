"""에러 복구 및 복원력 시스템.

재시도, 폴백, 서킷 브레이커 패턴을 제공하고 에러를 SQLite에 기록한다.
"""

import enum
import functools
import os
import sqlite3
import threading
import time
import traceback
from datetime import datetime


class CircuitState(enum.Enum):
    """서킷 브레이커 상태."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """서킷 브레이커 패턴 구현.

    - CLOSED: 정상 상태, 호출 허용
    - OPEN: 실패 임계치 초과, 호출 거부
    - HALF_OPEN: reset_timeout 이후 테스트 호출 허용
    """

    def __init__(self, name="default", failure_threshold=5, reset_timeout=60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._lock = threading.Lock()

    @property
    def state(self):
        with self._lock:
            if self._state == CircuitState.OPEN and self._last_failure_time is not None:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.reset_timeout:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def call(self, func, *args, **kwargs):
        """서킷 브레이커 로직으로 함수를 실행한다."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN. Calls are rejected."
            )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
            self._success_count += 1

    def _on_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def reset(self):
        """서킷을 CLOSED 상태로 강제 리셋한다."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None

    def get_status(self):
        """현재 서킷 상태 정보를 반환한다."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "last_failure_time": self._last_failure_time,
        }


class CircuitBreakerOpenError(Exception):
    """서킷 브레이커가 OPEN 상태일 때 발생하는 예외."""
    pass


class ErrorLogger:
    """SQLite 기반 에러 로거."""

    def __init__(self, db_path="logs/error_logs.db"):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_table()

    def _get_conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_table(self):
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                error_type TEXT NOT NULL,
                endpoint TEXT,
                message TEXT NOT NULL,
                stack_trace TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_error_logs_type ON error_logs(error_type)"
        )
        conn.commit()

    def log_error(self, error_type, endpoint, message, stack_trace=None):
        """에러를 SQLite에 기록한다."""
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO error_logs (timestamp, error_type, endpoint, message, stack_trace, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, error_type, endpoint, message, stack_trace, time.time()),
        )
        conn.commit()

    def get_recent_errors(self, limit=50):
        """최근 에러 목록을 반환한다."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM error_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_error_rate(self, minutes=60):
        """지정된 시간(분) 동안의 에러 비율 정보를 반환한다."""
        conn = self._get_conn()
        cutoff = time.time() - (minutes * 60)
        row = conn.execute(
            "SELECT COUNT(*) as count FROM error_logs WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()
        total = row["count"]
        return {
            "period_minutes": minutes,
            "total_errors": total,
            "errors_per_minute": round(total / max(minutes, 1), 2),
        }

    def get_error_stats(self):
        """에러 유형별, 엔드포인트별 통계를 반환한다."""
        conn = self._get_conn()
        # 유형별
        by_type = conn.execute(
            "SELECT error_type, COUNT(*) as count FROM error_logs GROUP BY error_type ORDER BY count DESC"
        ).fetchall()
        # 엔드포인트별
        by_endpoint = conn.execute(
            "SELECT endpoint, COUNT(*) as count FROM error_logs WHERE endpoint IS NOT NULL GROUP BY endpoint ORDER BY count DESC"
        ).fetchall()
        # 최근 1시간
        cutoff_1h = time.time() - 3600
        recent = conn.execute(
            "SELECT COUNT(*) as count FROM error_logs WHERE created_at >= ?",
            (cutoff_1h,),
        ).fetchone()
        # 최근 24시간
        cutoff_24h = time.time() - 86400
        daily = conn.execute(
            "SELECT COUNT(*) as count FROM error_logs WHERE created_at >= ?",
            (cutoff_24h,),
        ).fetchone()

        return {
            "by_type": {row["error_type"]: row["count"] for row in by_type},
            "by_endpoint": {row["endpoint"]: row["count"] for row in by_endpoint},
            "last_hour": recent["count"],
            "last_24h": daily["count"],
            "total": sum(row["count"] for row in by_type),
        }

    def cleanup(self, days=30):
        """지정된 일수보다 오래된 에러 로그를 삭제한다."""
        conn = self._get_conn()
        cutoff = time.time() - (days * 86400)
        cursor = conn.execute(
            "DELETE FROM error_logs WHERE created_at < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount


class ErrorRecovery:
    """에러 복구 및 복원력 통합 클래스."""

    def __init__(self, db_path="logs/error_logs.db"):
        self.error_logger = ErrorLogger(db_path=db_path)
        self._circuit_breakers = {}
        self._lock = threading.Lock()

    def with_retry(self, func=None, max_retries=3, backoff=1.0):
        """지수 백오프를 적용한 재시도 래퍼/데코레이터.

        데코레이터로 사용:
            @recovery.with_retry(max_retries=3)
            def my_func(): ...

        래퍼로 사용:
            result = recovery.with_retry(my_func, max_retries=3)()
        """
        def decorator(f):
            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                last_exception = None
                for attempt in range(max_retries + 1):
                    try:
                        return f(*args, **kwargs)
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries:
                            sleep_time = backoff * (2 ** attempt)
                            time.sleep(sleep_time)
                        else:
                            self.error_logger.log_error(
                                error_type=type(e).__name__,
                                endpoint=getattr(f, "__name__", "unknown"),
                                message=str(e),
                                stack_trace=traceback.format_exc(),
                            )
                raise last_exception
            return wrapper

        if func is not None:
            return decorator(func)
        return decorator

    def with_fallback(self, func, fallback_func):
        """기본 함수 실패 시 폴백 함수를 실행하는 래퍼를 반환한다."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                self.error_logger.log_error(
                    error_type=type(e).__name__,
                    endpoint=getattr(func, "__name__", "unknown"),
                    message=f"Primary failed, using fallback: {e}",
                    stack_trace=traceback.format_exc(),
                )
                return fallback_func(*args, **kwargs)

        return wrapper

    def with_circuit_breaker(self, func=None, name=None, failure_threshold=5, reset_timeout=60):
        """서킷 브레이커 패턴을 적용하는 래퍼/데코레이터."""
        def decorator(f):
            cb_name = name or getattr(f, "__name__", "unknown")
            cb = self._get_or_create_breaker(cb_name, failure_threshold, reset_timeout)

            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                try:
                    return cb.call(f, *args, **kwargs)
                except CircuitBreakerOpenError:
                    self.error_logger.log_error(
                        error_type="CircuitBreakerOpen",
                        endpoint=cb_name,
                        message=f"Circuit breaker '{cb_name}' is OPEN",
                    )
                    raise
                except Exception as e:
                    self.error_logger.log_error(
                        error_type=type(e).__name__,
                        endpoint=cb_name,
                        message=str(e),
                        stack_trace=traceback.format_exc(),
                    )
                    raise

            return wrapper

        if func is not None:
            return decorator(func)
        return decorator

    def _get_or_create_breaker(self, name, failure_threshold, reset_timeout):
        with self._lock:
            if name not in self._circuit_breakers:
                self._circuit_breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    reset_timeout=reset_timeout,
                )
            return self._circuit_breakers[name]

    def get_error_stats(self):
        """에러 통계를 반환한다."""
        return self.error_logger.get_error_stats()

    def get_circuit_status(self):
        """모든 서킷 브레이커의 상태를 반환한다."""
        with self._lock:
            return {
                name: cb.get_status()
                for name, cb in self._circuit_breakers.items()
            }

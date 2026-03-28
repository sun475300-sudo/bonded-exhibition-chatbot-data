"""통합 헬스 모니터링 모듈.

시스템의 각 구성 요소를 점검하고 종합 상태를 반환한다.
상태: "healthy", "degraded", "unhealthy"
"""

import os
import platform
import sqlite3
import time
from datetime import datetime


class HealthMonitor:
    """시스템 헬스 모니터링 클래스.

    각 구성 요소(DB, FAQ, 디스크, 메모리, 응답 시간, 에러율)를
    점검하고 종합 건강 상태를 반환한다.
    """

    def __init__(self, base_dir=None, faq_items=None, chat_logger=None):
        """HealthMonitor를 초기화한다.

        Args:
            base_dir: 프로젝트 루트 디렉토리
            faq_items: FAQ 항목 리스트
            chat_logger: ChatLogger 인스턴스
        """
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_dir = base_dir
        self.faq_items = faq_items or []
        self.chat_logger = chat_logger
        self._start_time = time.time()
        self._response_times = []
        self._error_count = 0
        self._request_count = 0

    def _make_result(self, status, message, details=None):
        """표준 검사 결과 dict를 생성한다."""
        return {
            "status": status,
            "message": message,
            "details": details or {},
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def record_request(self, response_time, is_error=False):
        """요청 응답 시간과 에러 여부를 기록한다.

        Args:
            response_time: 응답 시간(초)
            is_error: 에러 여부
        """
        self._response_times.append(response_time)
        self._request_count += 1
        if is_error:
            self._error_count += 1
        # 최근 1000건만 유지
        if len(self._response_times) > 1000:
            self._response_times = self._response_times[-1000:]

    def check_database(self):
        """모든 SQLite DB 파일의 연결, 크기, 테이블 수, 무결성을 검사한다.

        Returns:
            dict: status, message, details, timestamp
        """
        db_dirs = [
            os.path.join(self.base_dir, "logs"),
            os.path.join(self.base_dir, "data"),
        ]
        db_files = []
        for d in db_dirs:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith(".db"):
                        db_files.append(os.path.join(d, f))

        if not db_files:
            return self._make_result("healthy", "DB 파일 없음 (신규 설치)", {"databases": []})

        results = []
        has_error = False
        has_warning = False

        for db_path in sorted(db_files):
            db_info = {"path": os.path.relpath(db_path, self.base_dir)}
            try:
                file_size = os.path.getsize(db_path)
                db_info["size_bytes"] = file_size
                db_info["size_mb"] = round(file_size / (1024 * 1024), 2)

                conn = sqlite3.connect(db_path, timeout=5)
                cursor = conn.cursor()

                # 연결 테스트
                cursor.execute("SELECT 1")

                # 테이블 수
                cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
                table_count = cursor.fetchone()[0]
                db_info["table_count"] = table_count

                # 무결성 검사
                cursor.execute("PRAGMA quick_check")
                integrity = cursor.fetchone()[0]
                db_info["integrity"] = integrity

                conn.close()

                if integrity != "ok":
                    db_info["status"] = "unhealthy"
                    has_error = True
                else:
                    db_info["status"] = "healthy"

            except sqlite3.Error as e:
                db_info["status"] = "unhealthy"
                db_info["error"] = str(e)
                has_error = True
            except OSError as e:
                db_info["status"] = "unhealthy"
                db_info["error"] = str(e)
                has_error = True

            results.append(db_info)

        if has_error:
            status = "unhealthy"
            message = "일부 DB에서 오류 발견"
        elif has_warning:
            status = "degraded"
            message = "일부 DB에서 경고 발견"
        else:
            status = "healthy"
            message = f"모든 DB 정상 ({len(results)}개)"

        return self._make_result(status, message, {"databases": results})

    def check_faq_data(self):
        """FAQ 데이터의 수, 카테고리, 완전성을 검증한다.

        Returns:
            dict: status, message, details, timestamp
        """
        faq_count = len(self.faq_items)
        if faq_count == 0:
            return self._make_result("unhealthy", "FAQ 데이터 없음", {
                "count": 0, "categories": [], "incomplete": 0,
            })

        categories = set()
        incomplete = 0
        for item in self.faq_items:
            cat = item.get("category", "")
            if cat:
                categories.add(cat)
            # 필수 필드 체크
            if not item.get("question") or not item.get("answer"):
                incomplete += 1

        details = {
            "count": faq_count,
            "categories": sorted(categories),
            "category_count": len(categories),
            "incomplete": incomplete,
        }

        if faq_count < 10:
            status = "degraded"
            message = f"FAQ 데이터 부족 ({faq_count}개)"
        elif incomplete > 0:
            status = "degraded"
            message = f"불완전한 FAQ 항목 {incomplete}개 발견"
        else:
            status = "healthy"
            message = f"FAQ 데이터 정상 ({faq_count}개, {len(categories)}개 카테고리)"

        return self._make_result(status, message, details)

    def check_disk_space(self):
        """프로젝트 디렉토리의 디스크 여유 공간을 확인한다.

        Returns:
            dict: status, message, details, timestamp
        """
        try:
            stat = os.statvfs(self.base_dir)
            total = stat.f_frsize * stat.f_blocks
            free = stat.f_frsize * stat.f_bavail
            used = total - free
            usage_pct = round((used / total) * 100, 1) if total > 0 else 0

            details = {
                "total_gb": round(total / (1024 ** 3), 2),
                "free_gb": round(free / (1024 ** 3), 2),
                "used_gb": round(used / (1024 ** 3), 2),
                "usage_percent": usage_pct,
            }

            if usage_pct >= 95:
                status = "unhealthy"
                message = f"디스크 공간 부족 ({usage_pct}% 사용)"
            elif usage_pct >= 85:
                status = "degraded"
                message = f"디스크 공간 경고 ({usage_pct}% 사용)"
            else:
                status = "healthy"
                message = f"디스크 공간 정상 ({usage_pct}% 사용, {details['free_gb']}GB 여유)"

            return self._make_result(status, message, details)

        except OSError as e:
            return self._make_result("unhealthy", f"디스크 점검 실패: {e}")

    def check_memory_usage(self):
        """현재 프로세스의 메모리 사용량을 확인한다.

        Returns:
            dict: status, message, details, timestamp
        """
        try:
            import resource
            rusage = resource.getrusage(resource.RUSAGE_SELF)
            # maxrss is in KB on Linux
            rss_mb = round(rusage.ru_maxrss / 1024, 2)

            details = {
                "rss_mb": rss_mb,
                "max_rss_kb": rusage.ru_maxrss,
            }

            if rss_mb >= 1024:
                status = "unhealthy"
                message = f"메모리 사용량 과다 ({rss_mb}MB)"
            elif rss_mb >= 512:
                status = "degraded"
                message = f"메모리 사용량 경고 ({rss_mb}MB)"
            else:
                status = "healthy"
                message = f"메모리 사용량 정상 ({rss_mb}MB)"

            return self._make_result(status, message, details)

        except ImportError:
            # Windows 등 resource 모듈 미지원
            import sys
            details = {"note": "resource 모듈 미지원 플랫폼"}
            return self._make_result("healthy", "메모리 점검 스킵 (플랫폼 미지원)", details)
        except Exception as e:
            return self._make_result("degraded", f"메모리 점검 실패: {e}")

    def check_response_times(self):
        """최근 기록된 응답 시간의 평균을 확인한다.

        Returns:
            dict: status, message, details, timestamp
        """
        if not self._response_times:
            return self._make_result("healthy", "응답 시간 데이터 없음 (아직 요청 없음)", {
                "avg_ms": 0, "count": 0,
            })

        recent = self._response_times[-100:]
        avg_time = sum(recent) / len(recent)
        max_time = max(recent)
        min_time = min(recent)

        details = {
            "avg_ms": round(avg_time * 1000, 2),
            "max_ms": round(max_time * 1000, 2),
            "min_ms": round(min_time * 1000, 2),
            "count": len(recent),
            "total_recorded": len(self._response_times),
        }

        if avg_time > 5.0:
            status = "unhealthy"
            message = f"평균 응답 시간 초과 ({details['avg_ms']}ms)"
        elif avg_time > 3.0:
            status = "degraded"
            message = f"평균 응답 시간 경고 ({details['avg_ms']}ms)"
        else:
            status = "healthy"
            message = f"평균 응답 시간 정상 ({details['avg_ms']}ms)"

        return self._make_result(status, message, details)

    def check_error_rate(self):
        """최근 에러 비율을 확인한다.

        Returns:
            dict: status, message, details, timestamp
        """
        if self._request_count == 0:
            return self._make_result("healthy", "에러율 데이터 없음 (아직 요청 없음)", {
                "error_rate_percent": 0, "total_requests": 0, "total_errors": 0,
            })

        error_rate = (self._error_count / self._request_count) * 100

        details = {
            "error_rate_percent": round(error_rate, 2),
            "total_requests": self._request_count,
            "total_errors": self._error_count,
        }

        if error_rate >= 10:
            status = "unhealthy"
            message = f"에러율 과다 ({error_rate:.1f}%)"
        elif error_rate >= 5:
            status = "degraded"
            message = f"에러율 경고 ({error_rate:.1f}%)"
        else:
            status = "healthy"
            message = f"에러율 정상 ({error_rate:.1f}%)"

        return self._make_result(status, message, details)

    def check_all(self):
        """모든 검사를 실행하고 종합 상태를 반환한다.

        Returns:
            dict: overall status, components dict, timestamp
        """
        checks = {
            "database": self.check_database,
            "faq_data": self.check_faq_data,
            "disk_space": self.check_disk_space,
            "memory_usage": self.check_memory_usage,
            "response_times": self.check_response_times,
            "error_rate": self.check_error_rate,
        }

        components = {}
        overall = "healthy"

        for name, check_fn in checks.items():
            try:
                result = check_fn()
            except Exception as e:
                result = self._make_result("unhealthy", f"검사 실패: {e}")
            components[name] = result

            if result["status"] == "unhealthy":
                overall = "unhealthy"
            elif result["status"] == "degraded" and overall != "unhealthy":
                overall = "degraded"

        healthy_count = sum(1 for c in components.values() if c["status"] == "healthy")
        total_count = len(components)

        return {
            "status": overall,
            "healthy_components": healthy_count,
            "total_components": total_count,
            "components": components,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def get_system_info(self):
        """시스템 정보를 반환한다.

        Returns:
            dict: Python 버전, OS, 업타임 등
        """
        import sys

        uptime_seconds = time.time() - self._start_time

        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        return {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "os": platform.system(),
            "architecture": platform.machine(),
            "hostname": platform.node(),
            "uptime_seconds": round(uptime_seconds, 1),
            "uptime_formatted": uptime_str,
            "base_dir": self.base_dir,
            "faq_count": len(self.faq_items),
            "pid": os.getpid(),
        }

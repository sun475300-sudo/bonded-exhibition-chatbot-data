#!/usr/bin/env python3
"""보세전시장 챗봇 헬스체크 스크립트.

Docker HEALTHCHECK 또는 모니터링 시스템과 연동하여 사용한다.
/api/health 엔드포인트를 호출하고, FAQ 수와 응답 시간을 검증한다.
추가로 DB 연결, FAQ 데이터 적재 상태, 응답 시간 등을 종합 점검한다.

사용법:
    python deploy/healthcheck.py                  # 기본 (localhost:8080)
    python deploy/healthcheck.py --host 0.0.0.0   # 호스트 지정
    python deploy/healthcheck.py --port 5000      # 포트 지정
    python deploy/healthcheck.py --detailed       # 상세 JSON 출력

종료 코드:
    0: 정상 (healthy)
    1: 성능 저하 (degraded) - 일부 검사 실패이나 서비스는 작동 중
    2: 비정상 (unhealthy) - 서비스 응답 불가 또는 치명적 오류
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
MIN_FAQ_COUNT = 50
MAX_RESPONSE_TIME = 5.0  # 초
DEGRADED_RESPONSE_TIME = 3.0  # 초 (이 이상이면 degraded)
DB_PATH_DEFAULT = "logs/chat_logs.db"

# 종료 코드
EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_UNHEALTHY = 2


def check_api_health(host, port):
    """API 헬스 엔드포인트를 확인한다.

    Args:
        host: 서버 호스트
        port: 서버 포트

    Returns:
        dict: 검사 결과 {status, response_time, faq_count, message}
    """
    url = f"http://{host}:{port}/api/health"
    result = {
        "check": "api_health",
        "status": "fail",
        "response_time": None,
        "faq_count": None,
        "message": "",
    }

    start_time = time.time()

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "chatbot-healthcheck/2.0")

        with urllib.request.urlopen(req, timeout=10) as response:
            elapsed = time.time() - start_time
            body = response.read().decode("utf-8")
            data = json.loads(body)

    except urllib.error.URLError as e:
        result["message"] = f"연결 실패: {e}"
        return result
    except Exception as e:
        result["message"] = f"요청 오류: {e}"
        return result

    result["response_time"] = round(elapsed, 4)

    # 상태 확인
    status = data.get("status")
    if status != "ok":
        result["message"] = f"상태 비정상: status={status}"
        return result

    # FAQ 수 검증
    faq_count = data.get("faq_count", 0)
    result["faq_count"] = faq_count
    if faq_count < MIN_FAQ_COUNT:
        result["message"] = f"FAQ 수 부족: {faq_count}개 (최소 {MIN_FAQ_COUNT}개)"
        return result

    # 응답 시간 검증
    if elapsed > MAX_RESPONSE_TIME:
        result["message"] = f"응답 시간 초과: {elapsed:.2f}초"
        return result

    if elapsed > DEGRADED_RESPONSE_TIME:
        result["status"] = "degraded"
        result["message"] = f"응답 시간 경고: {elapsed:.2f}초 (임계치: {DEGRADED_RESPONSE_TIME}초)"
        return result

    result["status"] = "pass"
    result["message"] = f"정상 (FAQ: {faq_count}개, 응답시간: {elapsed:.3f}초)"
    return result


def check_db_connectivity(db_path):
    """SQLite DB 연결 상태를 확인한다.

    Args:
        db_path: DB 파일 경로

    Returns:
        dict: 검사 결과 {status, message, details}
    """
    result = {
        "check": "db_connectivity",
        "status": "fail",
        "message": "",
        "details": {},
    }

    if not os.path.exists(db_path):
        result["status"] = "pass"
        result["message"] = "DB 파일 없음 (신규 설치 - 정상)"
        return result

    try:
        start_time = time.time()
        conn = sqlite3.connect(db_path, timeout=5)
        cursor = conn.cursor()

        # 기본 쿼리 실행
        cursor.execute("SELECT 1")

        # 테이블 목록 조회
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        # DB 크기
        cursor.execute("PRAGMA page_count")
        page_count = cursor.fetchone()[0]
        cursor.execute("PRAGMA page_size")
        page_size = cursor.fetchone()[0]
        db_size_mb = round((page_count * page_size) / (1024 * 1024), 2)

        # 무결성 검사 (빠른 검사만)
        cursor.execute("PRAGMA quick_check")
        integrity = cursor.fetchone()[0]

        elapsed = time.time() - start_time
        conn.close()

        result["details"] = {
            "tables": tables,
            "db_size_mb": db_size_mb,
            "integrity": integrity,
            "query_time": round(elapsed, 4),
        }

        if integrity != "ok":
            result["status"] = "fail"
            result["message"] = f"DB 무결성 오류: {integrity}"
            return result

        result["status"] = "pass"
        result["message"] = f"DB 정상 (테이블: {len(tables)}개, 크기: {db_size_mb}MB)"

    except sqlite3.Error as e:
        result["message"] = f"DB 연결 오류: {e}"
    except Exception as e:
        result["message"] = f"DB 검사 오류: {e}"

    return result


def check_faq_data_loaded(host, port):
    """FAQ 데이터 적재 상태를 확인한다.

    채팅 엔드포인트에 테스트 질문을 보내 응답을 검증한다.

    Args:
        host: 서버 호스트
        port: 서버 포트

    Returns:
        dict: 검사 결과 {status, message, response_time}
    """
    url = f"http://{host}:{port}/api/chat"
    result = {
        "check": "faq_data_loaded",
        "status": "fail",
        "response_time": None,
        "message": "",
    }

    test_payload = json.dumps({"question": "보세전시장"}).encode("utf-8")
    start_time = time.time()

    try:
        req = urllib.request.Request(
            url,
            data=test_payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "chatbot-healthcheck/2.0",
            },
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            elapsed = time.time() - start_time
            body = response.read().decode("utf-8")
            data = json.loads(body)

    except urllib.error.URLError as e:
        result["message"] = f"채팅 API 연결 실패: {e}"
        return result
    except Exception as e:
        result["message"] = f"채팅 API 오류: {e}"
        return result

    result["response_time"] = round(elapsed, 4)

    # 응답에 답변이 포함되어 있는지 확인
    answer = data.get("answer", data.get("response", ""))
    if not answer or len(answer.strip()) == 0:
        result["message"] = "FAQ 응답이 비어있음 - 데이터 미적재 가능성"
        return result

    result["status"] = "pass"
    result["message"] = f"FAQ 응답 정상 (응답시간: {elapsed:.3f}초, 응답길이: {len(answer)}자)"
    return result


def check_response_time(host, port, num_requests=3):
    """응답 시간을 여러 번 측정하여 평균을 구한다.

    Args:
        host: 서버 호스트
        port: 서버 포트
        num_requests: 측정 횟수

    Returns:
        dict: 검사 결과 {status, avg_time, min_time, max_time, message}
    """
    url = f"http://{host}:{port}/api/health"
    result = {
        "check": "response_time",
        "status": "fail",
        "avg_time": None,
        "min_time": None,
        "max_time": None,
        "message": "",
    }

    times = []
    for _ in range(num_requests):
        start = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "chatbot-healthcheck/2.0")
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
                elapsed = time.time() - start
                times.append(elapsed)
        except Exception:
            times.append(MAX_RESPONSE_TIME + 1)

    if not times:
        result["message"] = "응답 시간 측정 실패"
        return result

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    result["avg_time"] = round(avg_time, 4)
    result["min_time"] = round(min_time, 4)
    result["max_time"] = round(max_time, 4)

    if avg_time > MAX_RESPONSE_TIME:
        result["message"] = f"평균 응답 시간 초과: {avg_time:.3f}초"
        return result

    if avg_time > DEGRADED_RESPONSE_TIME:
        result["status"] = "degraded"
        result["message"] = f"평균 응답 시간 경고: {avg_time:.3f}초"
        return result

    result["status"] = "pass"
    result["message"] = f"응답 시간 정상 (평균: {avg_time:.3f}초, 최소: {min_time:.3f}초, 최대: {max_time:.3f}초)"
    return result


def run_all_checks(host, port, db_path, detailed=False):
    """모든 헬스체크를 수행하고 종합 결과를 반환한다.

    Args:
        host: 서버 호스트
        port: 서버 포트
        db_path: DB 파일 경로
        detailed: 상세 결과 포함 여부

    Returns:
        (exit_code: int, result: dict)
    """
    checks = []
    overall_status = "healthy"

    # 1. API 헬스체크 (필수)
    api_result = check_api_health(host, port)
    checks.append(api_result)

    if api_result["status"] == "fail":
        overall_status = "unhealthy"
    elif api_result["status"] == "degraded" and overall_status == "healthy":
        overall_status = "degraded"

    # 2. DB 연결 검사
    db_result = check_db_connectivity(db_path)
    checks.append(db_result)

    if db_result["status"] == "fail" and "없음" not in db_result.get("message", ""):
        if overall_status == "healthy":
            overall_status = "degraded"

    # API가 응답하는 경우에만 추가 검사 수행
    if api_result["status"] != "fail":
        # 3. FAQ 데이터 적재 검사
        faq_result = check_faq_data_loaded(host, port)
        checks.append(faq_result)

        if faq_result["status"] == "fail":
            if overall_status == "healthy":
                overall_status = "degraded"

        # 4. 응답 시간 검사
        rt_result = check_response_time(host, port)
        checks.append(rt_result)

        if rt_result["status"] == "fail":
            if overall_status == "healthy":
                overall_status = "degraded"
        elif rt_result["status"] == "degraded" and overall_status == "healthy":
            overall_status = "degraded"

    # 종합 결과
    result = {
        "status": overall_status,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": host,
        "port": port,
        "checks_total": len(checks),
        "checks_passed": sum(1 for c in checks if c["status"] == "pass"),
        "checks_degraded": sum(1 for c in checks if c["status"] == "degraded"),
        "checks_failed": sum(1 for c in checks if c["status"] == "fail"),
    }

    if detailed:
        result["checks"] = checks

    # 종료 코드 결정
    if overall_status == "healthy":
        exit_code = EXIT_HEALTHY
    elif overall_status == "degraded":
        exit_code = EXIT_DEGRADED
    else:
        exit_code = EXIT_UNHEALTHY

    return exit_code, result


def check_health(host, port):
    """기본 헬스체크를 수행한다 (하위 호환성 유지).

    Args:
        host: 서버 호스트
        port: 서버 포트

    Returns:
        (success: bool, message: str)
    """
    url = f"http://{host}:{port}/api/health"

    start_time = time.time()

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "chatbot-healthcheck/1.0")

        with urllib.request.urlopen(req, timeout=10) as response:
            elapsed = time.time() - start_time
            body = response.read().decode("utf-8")
            data = json.loads(body)

    except urllib.error.URLError as e:
        return False, f"연결 실패: {e}"
    except Exception as e:
        return False, f"요청 오류: {e}"

    # 상태 확인
    status = data.get("status")
    if status != "ok":
        return False, f"상태 비정상: status={status}"

    # FAQ 수 검증
    faq_count = data.get("faq_count", 0)
    if faq_count < MIN_FAQ_COUNT:
        return False, f"FAQ 수 부족: {faq_count}개 (최소 {MIN_FAQ_COUNT}개 필요)"

    # 응답 시간 검증
    if elapsed > MAX_RESPONSE_TIME:
        return False, f"응답 시간 초과: {elapsed:.2f}초 (최대 {MAX_RESPONSE_TIME}초)"

    return True, f"정상 (FAQ: {faq_count}개, 응답시간: {elapsed:.3f}초)"


def main():
    parser = argparse.ArgumentParser(description="보세전시장 챗봇 헬스체크")
    parser.add_argument("--host", default=os.environ.get("CHATBOT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CHATBOT_PORT", "8080")))
    parser.add_argument("--db-path", default=os.environ.get("CHATBOT_DB_PATH", DB_PATH_DEFAULT))
    parser.add_argument("--detailed", action="store_true", help="상세 JSON 결과 출력")
    args = parser.parse_args()

    if args.detailed:
        # 종합 검사 모드
        exit_code, result = run_all_checks(
            args.host, args.port, args.db_path, detailed=True
        )

        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(exit_code)
    else:
        # 기본 검사 모드 (하위 호환)
        success, message = check_health(args.host, args.port)

        if success:
            print(f"[OK] {message}")
            sys.exit(EXIT_HEALTHY)
        else:
            print(f"[FAIL] {message}", file=sys.stderr)
            sys.exit(EXIT_UNHEALTHY)


if __name__ == "__main__":
    main()

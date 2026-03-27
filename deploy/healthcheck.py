#!/usr/bin/env python3
"""보세전시장 챗봇 헬스체크 스크립트.

Docker HEALTHCHECK 또는 모니터링 시스템과 연동하여 사용한다.
/api/health 엔드포인트를 호출하고, FAQ 수와 응답 시간을 검증한다.

사용법:
    python deploy/healthcheck.py                  # 기본 (localhost:8080)
    python deploy/healthcheck.py --host 0.0.0.0   # 호스트 지정
    python deploy/healthcheck.py --port 5000      # 포트 지정

종료 코드:
    0: 정상
    1: 비정상 (Docker HEALTHCHECK에서 unhealthy로 판정)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

# 설정
MIN_FAQ_COUNT = 50
MAX_RESPONSE_TIME = 5.0  # 초


def check_health(host, port):
    """헬스체크를 수행한다.

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
    args = parser.parse_args()

    success, message = check_health(args.host, args.port)

    if success:
        print(f"[OK] {message}")
        sys.exit(0)
    else:
        print(f"[FAIL] {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

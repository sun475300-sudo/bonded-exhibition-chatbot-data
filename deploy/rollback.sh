#!/bin/bash
# 보세전시장 챗봇 롤백 스크립트
#
# 현재 실행 중인 컨테이너를 중지하고 이전 버전으로 복원한다.
#
# 사용법:
#   bash deploy/rollback.sh [--previous-tag <TAG>] [--port <PORT>]
#
# 매개변수:
#   --previous-tag     복원할 이미지 태그 (기본: 직전 실행 이미지)
#   --port             서비스 포트 (기본: 8080)
#
# 예시:
#   bash deploy/rollback.sh
#   bash deploy/rollback.sh --previous-tag abc1234 --port 8080

set -euo pipefail

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
IMAGE_NAME="${IMAGE_NAME:-bonded-exhibition-chatbot}"
PREVIOUS_TAG=""
PORT="${PORT:-8080}"
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-/api/health}"
HEALTH_RETRIES=10
HEALTH_INTERVAL=3
CONTAINER_PREFIX="chatbot"
LOG_FILE="${LOG_FILE:-/var/log/chatbot-deploy.log}"

# ──────────────────────────────────────────────
# 인수 파싱
# ──────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --previous-tag)
            PREVIOUS_TAG="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        *)
            echo "알 수 없는 옵션: $1" >&2
            exit 1
            ;;
    esac
done

# ──────────────────────────────────────────────
# 유틸리티 함수
# ──────────────────────────────────────────────
log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${message}" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
health_check() {
    local port="$1"
    local attempt=0

    log_info "헬스체크 시작 (포트: ${port})"

    while [[ $attempt -lt $HEALTH_RETRIES ]]; do
        attempt=$((attempt + 1))
        log_info "헬스체크 시도 ${attempt}/${HEALTH_RETRIES}..."

        if curl -sf --max-time 5 "http://localhost:${port}${HEALTH_ENDPOINT}" > /dev/null 2>&1; then
            log_info "헬스체크 통과"
            return 0
        fi

        sleep "$HEALTH_INTERVAL"
    done

    log_error "헬스체크 실패 (${HEALTH_RETRIES}회 시도)"
    return 1
}

# ──────────────────────────────────────────────
# 현재 실행 중인 컨테이너 찾기
# ──────────────────────────────────────────────
find_current_container() {
    local container
    container=$(docker ps --format '{{.Names}}' | grep "^${CONTAINER_PREFIX}-" | head -1)
    echo "${container:-}"
}

# ──────────────────────────────────────────────
# 이전 이미지 태그 찾기
# ──────────────────────────────────────────────
find_previous_tag() {
    # 현재 컨테이너의 이미지 태그 확인
    local current_container="$1"
    local current_image
    current_image=$(docker inspect --format '{{.Config.Image}}' "${current_container}" 2>/dev/null || echo "")

    if [[ -z "$current_image" ]]; then
        log_error "현재 컨테이너의 이미지를 확인할 수 없습니다."
        return 1
    fi

    log_info "현재 이미지: ${current_image}"

    # 로컬에 있는 이전 이미지 태그 찾기 (최신 것 다음)
    local previous
    previous=$(docker images "${IMAGE_NAME}" --format '{{.Tag}}' | grep -v "latest" | head -2 | tail -1)

    if [[ -z "$previous" ]]; then
        log_error "이전 버전 이미지를 찾을 수 없습니다."
        return 1
    fi

    echo "$previous"
}

# ──────────────────────────────────────────────
# 메인 롤백 프로세스
# ──────────────────────────────────────────────
main() {
    log_info "=========================================="
    log_info "롤백 시작"
    log_info "시간: $(date '+%Y-%m-%d %H:%M:%S')"
    log_info "=========================================="

    # 1단계: 현재 컨테이너 확인
    log_info "[1/5] 현재 컨테이너 확인..."
    local current_container
    current_container=$(find_current_container)

    if [[ -z "$current_container" ]]; then
        log_error "실행 중인 챗봇 컨테이너를 찾을 수 없습니다."
        exit 1
    fi

    local current_image
    current_image=$(docker inspect --format '{{.Config.Image}}' "${current_container}" 2>/dev/null)
    log_info "현재 컨테이너: ${current_container}"
    log_info "현재 이미지: ${current_image}"

    # 2단계: 이전 버전 태그 결정
    log_info "[2/5] 이전 버전 확인..."

    if [[ -z "$PREVIOUS_TAG" ]]; then
        PREVIOUS_TAG=$(find_previous_tag "$current_container")
        if [[ -z "$PREVIOUS_TAG" ]]; then
            log_error "이전 버전을 자동으로 찾을 수 없습니다. --previous-tag 옵션을 사용하세요."
            exit 1
        fi
    fi

    log_info "롤백 대상 이미지: ${IMAGE_NAME}:${PREVIOUS_TAG}"

    # 이미지 존재 확인
    if ! docker image inspect "${IMAGE_NAME}:${PREVIOUS_TAG}" > /dev/null 2>&1; then
        log_info "로컬에 이미지가 없습니다. Pull 시도..."
        if ! docker pull "${IMAGE_NAME}:${PREVIOUS_TAG}"; then
            log_error "이미지를 가져올 수 없습니다: ${IMAGE_NAME}:${PREVIOUS_TAG}"
            exit 1
        fi
    fi

    # 3단계: 현재 컨테이너 정지
    log_info "[3/5] 현재 컨테이너 정지..."
    docker stop "${current_container}"
    log_info "컨테이너 정지됨: ${current_container}"

    # 컨테이너 이름에서 색상 추출
    local current_color="${current_container##*-}"
    local rollback_color
    if [[ "$current_color" == "blue" ]]; then
        rollback_color="green"
    else
        rollback_color="blue"
    fi
    local rollback_container="${CONTAINER_PREFIX}-${rollback_color}"

    # 기존 비활성 컨테이너 정리
    if docker ps -a --format '{{.Names}}' | grep -q "^${rollback_container}$"; then
        docker rm -f "${rollback_container}" 2>/dev/null || true
    fi

    # 4단계: 이전 버전 시작
    log_info "[4/5] 이전 버전 시작..."

    docker run -d \
        --name "${rollback_container}" \
        -p "${PORT}:8080" \
        --restart unless-stopped \
        -e CHATBOT_PORT=8080 \
        -e CHATBOT_HOST=0.0.0.0 \
        -e CHATBOT_LOG_LEVEL=INFO \
        -v chatbot-logs:/app/logs \
        -v chatbot-data:/app/data \
        "${IMAGE_NAME}:${PREVIOUS_TAG}"

    log_info "이전 버전 컨테이너 시작됨: ${rollback_container}"

    # 5단계: 헬스체크 검증
    log_info "[5/5] 롤백 후 헬스체크..."

    if health_check "$PORT"; then
        log_info "헬스체크 통과 - 롤백 성공"

        # 이전(현재) 컨테이너 제거
        docker rm "${current_container}" 2>/dev/null || true

        log_info "=========================================="
        log_info "롤백 완료"
        log_info "활성 컨테이너: ${rollback_container}"
        log_info "이미지: ${IMAGE_NAME}:${PREVIOUS_TAG}"
        log_info "=========================================="
    else
        log_error "롤백 후 헬스체크 실패"
        log_warn "이전 컨테이너 복원 시도..."

        docker stop "${rollback_container}" 2>/dev/null || true
        docker rm "${rollback_container}" 2>/dev/null || true
        docker start "${current_container}" 2>/dev/null || true

        log_error "=========================================="
        log_error "롤백 실패 - 수동 조치 필요"
        log_error "=========================================="
        exit 1
    fi
}

main "$@"

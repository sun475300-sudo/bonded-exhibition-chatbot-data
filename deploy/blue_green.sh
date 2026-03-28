#!/bin/bash
# 보세전시장 챗봇 Blue/Green 배포 스크립트
#
# 사용법:
#   bash deploy/blue_green.sh --image-tag <TAG> [--port <PORT>] [--health-endpoint <PATH>]
#
# 매개변수:
#   --image-tag        배포할 Docker 이미지 태그 (필수)
#   --port             서비스 포트 (기본: 8080)
#   --health-endpoint  헬스체크 경로 (기본: /api/health)
#
# 예시:
#   bash deploy/blue_green.sh --image-tag abc1234
#   bash deploy/blue_green.sh --image-tag abc1234 --port 9090 --health-endpoint /health

set -euo pipefail

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
IMAGE_NAME="${IMAGE_NAME:-bonded-exhibition-chatbot}"
IMAGE_TAG=""
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
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --health-endpoint)
            HEALTH_ENDPOINT="$2"
            shift 2
            ;;
        *)
            echo "알 수 없는 옵션: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$IMAGE_TAG" ]]; then
    echo "오류: --image-tag 는 필수 매개변수입니다." >&2
    echo "사용법: bash deploy/blue_green.sh --image-tag <TAG>" >&2
    exit 1
fi

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

cleanup_on_error() {
    log_error "배포 중 오류 발생 - 롤백 시작"
    rollback
    exit 1
}

trap cleanup_on_error ERR

# ──────────────────────────────────────────────
# 현재 활성 컨테이너 확인
# ──────────────────────────────────────────────
get_active_color() {
    if docker ps --format '{{.Names}}' | grep -q "${CONTAINER_PREFIX}-blue"; then
        echo "blue"
    elif docker ps --format '{{.Names}}' | grep -q "${CONTAINER_PREFIX}-green"; then
        echo "green"
    else
        echo "none"
    fi
}

get_inactive_color() {
    local active
    active=$(get_active_color)
    if [[ "$active" == "blue" ]]; then
        echo "green"
    else
        echo "blue"
    fi
}

# ──────────────────────────────────────────────
# 헬스체크
# ──────────────────────────────────────────────
health_check() {
    local port="$1"
    local attempt=0

    log_info "헬스체크 시작 (포트: ${port}, 엔드포인트: ${HEALTH_ENDPOINT})"

    while [[ $attempt -lt $HEALTH_RETRIES ]]; do
        attempt=$((attempt + 1))
        log_info "헬스체크 시도 ${attempt}/${HEALTH_RETRIES}..."

        if curl -sf --max-time 5 "http://localhost:${port}${HEALTH_ENDPOINT}" > /dev/null 2>&1; then
            log_info "헬스체크 통과"
            return 0
        fi

        log_warn "헬스체크 실패 - ${HEALTH_INTERVAL}초 후 재시도..."
        sleep "$HEALTH_INTERVAL"
    done

    log_error "헬스체크 최종 실패 (${HEALTH_RETRIES}회 시도)"
    return 1
}

# ──────────────────────────────────────────────
# 롤백
# ──────────────────────────────────────────────
rollback() {
    local new_color
    new_color=$(get_inactive_color)
    local new_container="${CONTAINER_PREFIX}-${new_color}"

    log_warn "롤백: 새 컨테이너(${new_container}) 중지 및 제거"

    if docker ps -a --format '{{.Names}}' | grep -q "${new_container}"; then
        docker stop "${new_container}" 2>/dev/null || true
        docker rm "${new_container}" 2>/dev/null || true
    fi

    local active_color
    active_color=$(get_active_color)
    if [[ "$active_color" != "none" ]]; then
        local active_container="${CONTAINER_PREFIX}-${active_color}"
        log_info "롤백: 이전 컨테이너(${active_container}) 유지"

        if health_check "$PORT"; then
            log_info "롤백 완료 - 이전 버전 정상 작동 중"
        else
            log_error "롤백 후 이전 버전도 비정상 - 수동 조치 필요"
        fi
    else
        log_error "롤백: 활성 컨테이너 없음 - 수동 조치 필요"
    fi
}

# ──────────────────────────────────────────────
# 메인 배포 프로세스
# ──────────────────────────────────────────────
main() {
    log_info "=========================================="
    log_info "Blue/Green 배포 시작"
    log_info "이미지: ${IMAGE_NAME}:${IMAGE_TAG}"
    log_info "포트: ${PORT}"
    log_info "헬스 엔드포인트: ${HEALTH_ENDPOINT}"
    log_info "=========================================="

    # 현재 상태 확인
    local active_color
    active_color=$(get_active_color)
    local new_color
    new_color=$(get_inactive_color)
    local new_container="${CONTAINER_PREFIX}-${new_color}"
    local green_port

    log_info "현재 활성 컨테이너: ${active_color}"
    log_info "새 컨테이너 색상: ${new_color}"

    # Green 포트 (임시 - 헬스체크용)
    if [[ "$active_color" == "none" ]]; then
        green_port="$PORT"
    else
        green_port=$((PORT + 1))
    fi

    # 1단계: 새 컨테이너 시작
    log_info "[1/4] 새 컨테이너(${new_container}) 시작..."

    # 기존 비활성 컨테이너 정리
    if docker ps -a --format '{{.Names}}' | grep -q "${new_container}"; then
        log_info "기존 비활성 컨테이너 제거: ${new_container}"
        docker stop "${new_container}" 2>/dev/null || true
        docker rm "${new_container}" 2>/dev/null || true
    fi

    docker run -d \
        --name "${new_container}" \
        -p "${green_port}:8080" \
        --restart unless-stopped \
        -e CHATBOT_PORT=8080 \
        -e CHATBOT_HOST=0.0.0.0 \
        -e CHATBOT_LOG_LEVEL=INFO \
        -v chatbot-logs:/app/logs \
        -v chatbot-data:/app/data \
        "${IMAGE_NAME}:${IMAGE_TAG}"

    log_info "컨테이너 시작됨: ${new_container} (포트: ${green_port})"

    # 2단계: 헬스체크
    log_info "[2/4] 헬스체크 수행..."

    if ! health_check "$green_port"; then
        log_error "새 컨테이너 헬스체크 실패 - 롤백 실행"
        rollback
        exit 1
    fi

    # 3단계: 트래픽 전환
    log_info "[3/4] 트래픽 전환..."

    if [[ "$active_color" != "none" ]]; then
        local old_container="${CONTAINER_PREFIX}-${active_color}"

        # 포트 재매핑: 새 컨테이너를 메인 포트로 전환
        docker stop "${new_container}"
        docker rm "${new_container}"

        docker run -d \
            --name "${new_container}" \
            -p "${PORT}:8080" \
            --restart unless-stopped \
            -e CHATBOT_PORT=8080 \
            -e CHATBOT_HOST=0.0.0.0 \
            -e CHATBOT_LOG_LEVEL=INFO \
            -v chatbot-logs:/app/logs \
            -v chatbot-data:/app/data \
            "${IMAGE_NAME}:${IMAGE_TAG}"

        # 전환 후 헬스체크
        sleep 2
        if ! health_check "$PORT"; then
            log_error "트래픽 전환 후 헬스체크 실패 - 롤백"
            docker stop "${new_container}" 2>/dev/null || true
            docker rm "${new_container}" 2>/dev/null || true
            docker start "${old_container}" 2>/dev/null || true
            exit 1
        fi

        # 이전 컨테이너 정지
        log_info "이전 컨테이너(${old_container}) 정지"
        docker stop "${old_container}"
        docker rm "${old_container}"
    fi

    # 4단계: 완료
    log_info "[4/4] 배포 완료"
    log_info "=========================================="
    log_info "Blue/Green 배포 성공"
    log_info "활성 컨테이너: ${new_container}"
    log_info "이미지: ${IMAGE_NAME}:${IMAGE_TAG}"
    log_info "포트: ${PORT}"
    log_info "=========================================="
}

main "$@"

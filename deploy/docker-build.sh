#!/bin/bash
# 보세전시장 챗봇 Docker 빌드 스크립트
#
# 사용법:
#   bash deploy/docker-build.sh [--push] [--registry <REGISTRY>]
#
# 매개변수:
#   --push         빌드 후 레지스트리에 푸시
#   --registry     Docker 레지스트리 URL (기본: ghcr.io)
#   --image-name   이미지 이름 (기본: bonded-exhibition-chatbot)
#
# 예시:
#   bash deploy/docker-build.sh
#   bash deploy/docker-build.sh --push --registry ghcr.io/myorg

set -euo pipefail

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
REGISTRY="${REGISTRY:-ghcr.io}"
IMAGE_NAME="${IMAGE_NAME:-bonded-exhibition-chatbot}"
PUSH=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ──────────────────────────────────────────────
# 인수 파싱
# ──────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)
            PUSH=true
            shift
            ;;
        --registry)
            REGISTRY="$2"
            shift 2
            ;;
        --image-name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        *)
            echo "알 수 없는 옵션: $1" >&2
            exit 1
            ;;
    esac
done

# ──────────────────────────────────────────────
# 태그 생성
# ──────────────────────────────────────────────
COMMIT_SHA=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
COMMIT_SHA_FULL=$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
BRANCH_NAME=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# 브랜치 이름을 Docker 태그에 사용할 수 있도록 정리
BRANCH_TAG=$(echo "$BRANCH_NAME" | sed 's/[^a-zA-Z0-9._-]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')

FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}"

echo "=========================================="
echo "Docker 이미지 빌드"
echo "=========================================="
echo "프로젝트 디렉토리: ${PROJECT_DIR}"
echo "레지스트리:        ${REGISTRY}"
echo "이미지 이름:       ${IMAGE_NAME}"
echo "커밋 SHA:          ${COMMIT_SHA}"
echo "브랜치:            ${BRANCH_NAME}"
echo "빌드 시간:         ${BUILD_DATE}"
echo "=========================================="
echo ""

# ──────────────────────────────────────────────
# 빌드
# ──────────────────────────────────────────────
echo "[1/3] Docker 이미지 빌드 중..."

TAGS=(
    "-t" "${FULL_IMAGE}:${COMMIT_SHA}"
    "-t" "${FULL_IMAGE}:${BRANCH_TAG}"
    "-t" "${FULL_IMAGE}:latest"
)

docker build \
    "${TAGS[@]}" \
    --label "org.opencontainers.image.created=${BUILD_DATE}" \
    --label "org.opencontainers.image.revision=${COMMIT_SHA_FULL}" \
    --label "org.opencontainers.image.source=https://github.com/${IMAGE_NAME}" \
    --label "org.opencontainers.image.ref.name=${BRANCH_NAME}" \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${COMMIT_SHA_FULL}" \
    "$PROJECT_DIR"

echo ""
echo "[2/3] 빌드 완료 - 이미지 크기 보고"
echo "------------------------------------------"
echo "태그별 이미지 크기:"

for tag in "${COMMIT_SHA}" "${BRANCH_TAG}" "latest"; do
    SIZE=$(docker image inspect "${FULL_IMAGE}:${tag}" --format '{{.Size}}' 2>/dev/null || echo "0")
    SIZE_MB=$(echo "scale=2; ${SIZE} / 1048576" | bc 2>/dev/null || echo "N/A")
    echo "  ${FULL_IMAGE}:${tag} -> ${SIZE_MB} MB"
done

echo "------------------------------------------"

# 전체 이미지 레이어 정보
echo ""
echo "레이어 정보:"
docker history "${FULL_IMAGE}:${COMMIT_SHA}" --format "  {{.Size}}\t{{.CreatedBy}}" | head -10
echo ""

# ──────────────────────────────────────────────
# 푸시 (옵션)
# ──────────────────────────────────────────────
if [[ "$PUSH" == "true" ]]; then
    echo "[3/3] 레지스트리에 푸시 중..."

    for tag in "${COMMIT_SHA}" "${BRANCH_TAG}" "latest"; do
        echo "  푸시: ${FULL_IMAGE}:${tag}"
        docker push "${FULL_IMAGE}:${tag}"
    done

    echo ""
    echo "푸시 완료"
else
    echo "[3/3] 푸시 건너뜀 (--push 옵션으로 활성화)"
fi

# ──────────────────────────────────────────────
# 요약
# ──────────────────────────────────────────────
echo ""
echo "=========================================="
echo "빌드 완료"
echo "=========================================="
echo "이미지 태그:"
echo "  - ${FULL_IMAGE}:${COMMIT_SHA}"
echo "  - ${FULL_IMAGE}:${BRANCH_TAG}"
echo "  - ${FULL_IMAGE}:latest"
echo ""
echo "로컬 실행:"
echo "  docker run -p 8080:8080 ${FULL_IMAGE}:${COMMIT_SHA}"
echo "=========================================="

#!/bin/bash
# 보세전시장 챗봇 복원 스크립트
# 사용법: bash deploy/restore.sh <백업파일.tar.gz>

set -euo pipefail

# 설정
APP_DIR="${APP_DIR:-/app}"

if [ $# -eq 0 ]; then
    echo "사용법: bash deploy/restore.sh <백업파일.tar.gz>"
    echo ""
    echo "사용 가능한 백업 목록:"
    ls -lh "${APP_DIR}/backups"/chatbot_backup_*.tar.gz 2>/dev/null || echo "  (백업 파일 없음)"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "오류: 백업 파일을 찾을 수 없습니다: ${BACKUP_FILE}"
    exit 1
fi

echo "============================================"
echo "보세전시장 챗봇 복원 시작"
echo "시간: $(date '+%Y-%m-%d %H:%M:%S')"
echo "백업 파일: ${BACKUP_FILE}"
echo "============================================"

# 1. 복원 전 현재 상태 자동 백업
echo "[1/3] 복원 전 현재 상태 백업 중..."
PRE_RESTORE_DIR="${APP_DIR}/backups"
mkdir -p "${PRE_RESTORE_DIR}"
PRE_RESTORE_NAME="pre_restore_$(date +%Y%m%d_%H%M%S)"
PRE_RESTORE_PATH="${PRE_RESTORE_DIR}/${PRE_RESTORE_NAME}"
mkdir -p "${PRE_RESTORE_PATH}"

if [ -f "${APP_DIR}/logs/chat_logs.db" ]; then
    cp "${APP_DIR}/logs/chat_logs.db" "${PRE_RESTORE_PATH}/"
fi
if [ -f "${APP_DIR}/logs/feedback.db" ]; then
    cp "${APP_DIR}/logs/feedback.db" "${PRE_RESTORE_PATH}/"
fi
if [ -d "${APP_DIR}/data" ]; then
    cp -r "${APP_DIR}/data" "${PRE_RESTORE_PATH}/data"
fi
if [ -d "${APP_DIR}/config" ]; then
    cp -r "${APP_DIR}/config" "${PRE_RESTORE_PATH}/config"
fi

cd "${PRE_RESTORE_DIR}"
tar -czf "${PRE_RESTORE_NAME}.tar.gz" "${PRE_RESTORE_NAME}"
rm -rf "${PRE_RESTORE_PATH}"
echo "  - 현재 상태 백업 완료: ${PRE_RESTORE_DIR}/${PRE_RESTORE_NAME}.tar.gz"

# 2. 백업 파일 압축 해제
echo "[2/3] 백업 파일 압축 해제 중..."
TEMP_DIR=$(mktemp -d)
tar -xzf "${BACKUP_FILE}" -C "${TEMP_DIR}"

# 백업 디렉토리 찾기 (tar 안의 최상위 디렉토리)
EXTRACTED_DIR=$(ls "${TEMP_DIR}" | head -1)
RESTORE_SOURCE="${TEMP_DIR}/${EXTRACTED_DIR}"

echo "  - 압축 해제 완료: ${RESTORE_SOURCE}"

# 3. 복원 수행
echo "[3/3] 복원 수행 중..."

# DB 복원
if [ -f "${RESTORE_SOURCE}/chat_logs.db" ]; then
    mkdir -p "${APP_DIR}/logs"
    cp "${RESTORE_SOURCE}/chat_logs.db" "${APP_DIR}/logs/chat_logs.db"
    echo "  - chat_logs.db 복원 완료"
fi

if [ -f "${RESTORE_SOURCE}/feedback.db" ]; then
    mkdir -p "${APP_DIR}/logs"
    cp "${RESTORE_SOURCE}/feedback.db" "${APP_DIR}/logs/feedback.db"
    echo "  - feedback.db 복원 완료"
fi

# data 디렉토리 복원
if [ -d "${RESTORE_SOURCE}/data" ]; then
    rm -rf "${APP_DIR}/data"
    cp -r "${RESTORE_SOURCE}/data" "${APP_DIR}/data"
    echo "  - data/ 디렉토리 복원 완료"
fi

# config 디렉토리 복원
if [ -d "${RESTORE_SOURCE}/config" ]; then
    rm -rf "${APP_DIR}/config"
    cp -r "${RESTORE_SOURCE}/config" "${APP_DIR}/config"
    echo "  - config/ 디렉토리 복원 완료"
fi

# 임시 디렉토리 정리
rm -rf "${TEMP_DIR}"

echo ""
echo "============================================"
echo "복원 완료!"
echo "============================================"
echo ""
echo "[참고]"
echo "  - 복원 전 상태: ${PRE_RESTORE_DIR}/${PRE_RESTORE_NAME}.tar.gz"
echo "  - 서비스 재시작이 필요할 수 있습니다:"
echo "    docker-compose restart"
echo ""

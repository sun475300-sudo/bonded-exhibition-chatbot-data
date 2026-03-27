#!/bin/bash
# 보세전시장 챗봇 백업 스크립트
# 사용법: bash deploy/backup.sh [백업_디렉토리]
# 크론탭: 0 2 * * * /app/deploy/backup.sh /app/backups >> /app/logs/backup.log 2>&1

set -euo pipefail

# 설정
APP_DIR="${APP_DIR:-/app}"
BACKUP_DIR="${1:-${APP_DIR}/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_NAME="chatbot_backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"
RETENTION_DAYS=30

echo "============================================"
echo "보세전시장 챗봇 백업 시작"
echo "시간: $(date '+%Y-%m-%d %H:%M:%S')"
echo "백업 위치: ${BACKUP_PATH}"
echo "============================================"

# 백업 디렉토리 생성
mkdir -p "${BACKUP_PATH}"

# 1. SQLite DB 백업 (온라인 백업 - .backup 명령 사용)
echo "[1/3] 데이터베이스 백업 중..."

if [ -f "${APP_DIR}/logs/chat_logs.db" ]; then
    sqlite3 "${APP_DIR}/logs/chat_logs.db" ".backup '${BACKUP_PATH}/chat_logs.db'"
    echo "  - chat_logs.db 백업 완료 ($(du -h "${BACKUP_PATH}/chat_logs.db" | cut -f1))"
else
    echo "  - chat_logs.db 없음 (건너뜀)"
fi

if [ -f "${APP_DIR}/logs/feedback.db" ]; then
    sqlite3 "${APP_DIR}/logs/feedback.db" ".backup '${BACKUP_PATH}/feedback.db'"
    echo "  - feedback.db 백업 완료 ($(du -h "${BACKUP_PATH}/feedback.db" | cut -f1))"
else
    echo "  - feedback.db 없음 (건너뜀)"
fi

# 2. data 디렉토리 백업
echo "[2/3] 데이터 파일 백업 중..."

if [ -d "${APP_DIR}/data" ]; then
    cp -r "${APP_DIR}/data" "${BACKUP_PATH}/data"
    echo "  - data/ 디렉토리 백업 완료"
else
    echo "  - data/ 디렉토리 없음 (건너뜀)"
fi

# 3. config 디렉토리 백업
echo "  - config/ 디렉토리 백업 중..."

if [ -d "${APP_DIR}/config" ]; then
    cp -r "${APP_DIR}/config" "${BACKUP_PATH}/config"
    echo "  - config/ 디렉토리 백업 완료"
fi

# 압축
echo "[3/3] 압축 중..."
cd "${BACKUP_DIR}"
tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}"
rm -rf "${BACKUP_PATH}"
echo "  - ${BACKUP_NAME}.tar.gz 생성 완료 ($(du -h "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1))"

# 오래된 백업 삭제 (30일 이상)
echo ""
echo "오래된 백업 정리 (${RETENTION_DAYS}일 이상)..."
DELETED=$(find "${BACKUP_DIR}" -name "chatbot_backup_*.tar.gz" -type f -mtime +${RETENTION_DAYS} -print -delete | wc -l)
echo "  - ${DELETED}개 파일 삭제됨"

echo ""
echo "============================================"
echo "백업 완료!"
echo "파일: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo "============================================"
echo ""
echo "[복원 안내]"
echo "  bash deploy/restore.sh ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo ""

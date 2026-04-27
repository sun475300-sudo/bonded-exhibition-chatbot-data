# RUNBOOK — bonded-exhibition-chatbot-data

> 운영자/온콜용 시작·정지·롤백·장애 시나리오. 코드 변경 없이 운영 가이드.

## 1. 시작 (Start)

### 1.1 로컬 개발

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # 필요한 키 채우기 — 아래 표 참조
python web_server.py --host 127.0.0.1 --port 5000
```

### 1.2 프로덕션 (gunicorn)

**E3 권장 옵션** — sentence-transformers 모델 메모리 절약:

```bash
gunicorn web_server:app \
  --bind 0.0.0.0:8080 \
  --workers 4 \
  --threads 2 \
  --timeout 120 \
  --preload
```

`--preload` 효과:
- 마스터 프로세스에서 한 번만 모델 로드 → fork된 워커가 메모리 공유 (Linux copy-on-write)
- 4 worker × 모델 1GB → 4GB 대신 ~1.5GB
- 단점: graceful reload 안 됨 (worker 재시작 시 마스터 재시작 필요)

**E1 lazy load 효과**: import 시점 8s → 첫 query 시점 8s. `--preload` 없이도 import 즉시 가능.

### 1.3 Docker

```bash
docker compose up -d  # docker-compose.yml + .env 사용
docker compose logs -f chatbot  # 로그 추적
```

`docker-compose.yml`은 `chatbot` + `redis` 두 서비스. redis는 캐시/세션 — 필수는 아니지만 운영 권장.

## 2. 정지 (Stop)

```bash
# 로컬
Ctrl+C  # SIGINT — graceful

# gunicorn (PID 파일 사용 시)
kill -TERM $(cat /var/run/chatbot.pid)  # graceful
kill -KILL $(cat /var/run/chatbot.pid)  # 강제 (마지막 수단)

# Docker
docker compose down  # 컨테이너 정지 + 네트워크 제거 (볼륨 보존)
docker compose down -v  # 볼륨까지 제거 (DESTRUCTIVE)
```

## 3. 헬스체크

```bash
curl http://localhost:8080/api/health
```

**F2 응답 스키마**:
```json
{
  "status": "ok",
  "version": "1.0.0",
  "faq_count": 50,
  "db_ok": true,
  "vector_index_loaded": false,
  "llm_provider_ok": true,
  "timestamp": "2026-04-26T10:00:00Z"
}
```

| 필드 | 의미 | 정상값 |
|---|---|---|
| `db_ok` | SQLite ping (`SELECT 1`) | `true` |
| `vector_index_loaded` | sentence-transformers lazy load 여부 | 첫 검색 후 `true`, 그 전 `false` (정상) |
| `llm_provider_ok` | `CHATBOT_LLM_API_KEY` 또는 `ANTHROPIC_API_KEY` 환경변수 | `true` (LLM fallback 활성화 시) |

## 4. 롤백

### 4.1 코드 롤백

```bash
git log --oneline -10
git revert <SHA>  # 새 commit으로 되돌림 (안전)
git push origin main
```

**force push 금지** — `git revert` 사용. 머지 커밋은 `git revert -m 1 <merge-sha>`.

### 4.2 DB 롤백

`backups/` 디렉토리에 일일 백업 (`backup_manager.py`가 매일 02:00 KST 실행).

```bash
ls -lt backups/  # 최근 백업 확인
cp backups/chat_logs.db.2026-04-25.bak logs/chat_logs.db  # 복구
sudo systemctl restart chatbot  # 또는 docker compose restart
```

### 4.3 배포 롤백 (Docker)

```bash
docker compose down
docker tag bonded-chatbot:current bonded-chatbot:rollback-target
docker compose up -d --build  # 이전 이미지 사용 — docker-compose.yml의 image: 태그 변경 필요
```

## 5. 장애 시나리오

### 5.1 챗봇 응답이 느림 (>3s)

1. `/api/health` → `vector_index_loaded: false`면 첫 query라 정상 (모델 로드 ~3-4s)
2. `vector_index_loaded: true`인데도 느림 → CPU/메모리 확인:
   ```bash
   docker stats bonded-chatbot
   top -p $(pgrep -f gunicorn)
   ```
3. 임베딩 캐시 hit률 낮음 — `/api/admin/health/detailed` 확인

### 5.2 LLM fallback이 안 됨

1. `/api/health` → `llm_provider_ok: false`면 환경변수 미설정
2. `.env`에 `ANTHROPIC_API_KEY=sk-ant-...` 추가 + 재시작
3. rate limit (분당 10건) 초과면 60초 대기 — `provider.get_stats()["rate_limiter"]`

### 5.3 DB 잠김 (database is locked)

WAL 모드 적용 후 거의 발생 안 하지만:
1. 모든 쓰기 작업 일시 중단
2. `.db-wal`, `.db-shm` 파일 존재 확인 (정상이면 있음)
3. 필요 시 manual checkpoint:
   ```bash
   sqlite3 logs/chat_logs.db "PRAGMA wal_checkpoint(TRUNCATE);"
   ```

### 5.4 메모리 부족 (OOM kill)

1. gunicorn workers 줄이기 (`--workers 2`)
2. `--preload` 사용 (모델 메모리 공유)
3. 임베딩 캐시 크기 줄이기 (`vector_search.py`의 1000 → 256)

## 6. 환경변수 (.env)

| 키 | 필수 | 기본 | 설명 |
|---|---|---|---|
| `CHATBOT_HOST` | — | `0.0.0.0` | 서버 바인드 호스트 |
| `CHATBOT_PORT` | — | `8080` | 서버 포트 |
| `CHATBOT_DEBUG` | — | `false` | 디버그 모드 (운영에선 false) |
| `CHATBOT_LOG_LEVEL` | — | `INFO` | 로그 레벨 |
| `CHATBOT_DB_PATH` | — | `logs/chat_logs.db` | SQLite 경로 |
| `CHATBOT_API_KEYS` | — | (none) | API 인증 (콤마 구분, 비우면 인증 비활성) |
| `CHATBOT_CORS_ORIGINS` | — | `*` | CORS 허용 (운영에선 도메인 명시) |
| `JWT_SECRET` | **운영** | (none) | 관리자 인증 — `secrets.token_urlsafe(64)` 권장 |
| `ADMIN_USERS` | **운영** | (none) | 관리자 계정 JSON `[{"username":"…","password":"…"}]` |
| `SLACK_WEBHOOK_URL` | — | (none) | 알림 대상 |
| `ANTHROPIC_API_KEY` | LLM | (none) | LLM fallback 활성화 |
| `CHATBOT_LLM_MODEL` | — | `claude-sonnet-4-20250514` | LLM 모델 |
| `DATABASE_URL` | prod | (none) | PostgreSQL (선택) |
| `GUNICORN_WORKERS` | — | `4` | gunicorn worker 수 |
| `GUNICORN_THREADS` | — | `2` | gunicorn thread 수 |
| `GUNICORN_TIMEOUT` | — | `120` | request timeout (s) |

## 7. 로그 위치

| 종류 | 경로 | rotation |
|---|---|---|
| 채팅 로그 | `logs/chat_logs.db` (SQLite, WAL) | 미설정 (수동 vacuum 권장) |
| FAQ 변경 이력 | `logs/faq_history.db` | 미설정 |
| 정책 위반 | `logs/policy/policy_YYYY-MM-DD.jsonl` | 일별 분리됨 |
| 애플리케이션 로그 | stdout (Docker는 `docker compose logs`) | Docker가 관리 |

## 8. 정기 작업

| 작업 | 주기 | 트리거 |
|---|---|---|
| FAQ 백업 | 매일 02:00 KST | `task_scheduler.py` cron `0 2 * * *` |
| 주간 리포트 | 일요일 08:00 | `task_scheduler.py` |
| 법령정보센터 동기화 | 매일 06:00 | `task_scheduler.py` |
| 로그 정리 | 매월 1일 03:00 | `task_scheduler.py` |
| FAQ 품질 검사 | 매일 09:00 | `task_scheduler.py` |

## 9. 트러블슈팅 부록

### 9.1 "Module not found" import 에러
- `pip install -r requirements.txt` 재실행
- `.venv` 활성화 확인
- `PYTHONPATH` 또는 `sys.path` 설정 확인

### 9.2 sentence-transformers 모델 다운로드 실패
- `HF_TOKEN` 환경변수 설정 (huggingface.co rate limit 회피)
- 또는 모델 사전 다운로드:
  ```bash
  python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
  ```

### 9.3 첫 요청 응답 느림 (8s+)
- 정상 — sentence-transformers 모델 lazy load
- `--preload` 사용 시 시작 시간으로 이동 (첫 요청은 빠름)
- 또는 startup hook에서 `chatbot.vector_search.find_best_match("warmup")` 호출

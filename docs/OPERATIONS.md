# 운영 매뉴얼

## 1. 설치

### 로컬 실행
```bash
git clone https://github.com/sun475300-sudo/bonded-exhibition-chatbot-data.git
cd bonded-exhibition-chatbot-data
pip install -r requirements.txt
python web_server.py --port 8080
```

### Docker 배포
```bash
docker-compose up -d
# http://서버주소:8080 접속
```

### 프로덕션 배포 (nginx + gunicorn)
```bash
pip install gunicorn
gunicorn -c deploy/gunicorn_config.py web_server:app
# nginx 설정: deploy/nginx.conf 참고
```

## 2. 일상 운영

### FAQ 추가
1. `data/faq.json`에 항목 추가
2. `src/classifier.py`의 `CATEGORY_KEYWORDS`에 키워드 추가 (필요 시)
3. 검증: `python -m pytest tests/ -v`
4. 시뮬레이터 테스트: `python simulator.py -q "새 질문"`

### 백업/복원
```bash
bash deploy/backup.sh          # 백업
bash deploy/restore.sh backup.tar.gz  # 복원
```

### 로그 확인
- 관리자 대시보드: `http://서버주소:8080/admin`
- API: `GET /api/admin/logs?limit=100`
- DB 직접: `sqlite3 logs/chat_logs.db "SELECT * FROM queries ORDER BY timestamp DESC LIMIT 10;"`

## 3. 장애 대응

### 헬스체크
```bash
curl http://127.0.0.1:8080/api/health
# {"status": "ok", "faq_count": 50}
python deploy/healthcheck.py
```

### 서버 재시작
```bash
# Docker
docker-compose restart

# 프로세스
kill $(lsof -ti:8080) && python web_server.py --port 8080 &
```

## 4. 보안 설정

### API Key 활성화
```bash
export CHATBOT_API_KEYS="key1,key2,key3"
```

### Rate Limit 조정
```bash
export CHATBOT_RATE_LIMIT=120  # 분당 120회
```

### HTTPS (nginx)
`deploy/nginx.conf`의 SSL 섹션 주석 해제 후 인증서 경로 설정.

## 5. 법령 업데이트

1. 관세청 고시 개정 확인
2. `data/legal_references.json` 업데이트
3. `data/faq.json`의 관련 답변/근거 수정
4. `python -m pytest tests/test_data_validator.py -v` 로 정합성 검증

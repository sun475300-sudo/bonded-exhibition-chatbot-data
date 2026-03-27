# 개발자 가이드

## 아키텍처 개요

```
web_server.py (Flask)
  ├── src/chatbot.py        ← 메인 진입점
  │     ├── classifier.py   ← 키워드 분류 (10 카테고리)
  │     ├── similarity.py   ← TF-IDF 폴백 매칭
  │     ├── escalation.py   ← 에스컬레이션 5규칙
  │     ├── session.py      ← 멀티턴 세션
  │     ├── validator.py    ← 확인 질문 관리
  │     └── response_builder.py ← 답변 조립
  ├── src/logger_db.py      ← SQLite 로그
  ├── src/feedback.py       ← 피드백 관리
  ├── src/security.py       ← API Key, Rate Limit
  ├── src/translator.py     ← 다국어 (KO/EN/CN/JP)
  ├── src/analytics.py      ← 트렌드 분석
  └── src/auto_faq_pipeline.py ← FAQ 자동 추천
```

## 로컬 개발

```bash
git clone 후
pip install -r requirements.txt
python -m pytest tests/ -v     # 테스트
python simulator.py --test     # 시뮬레이터
python web_server.py --port 8080 --debug  # 디버그 서버
```

## 질문 처리 파이프라인

1. `web_server.py` → 입력 살균 + Rate Limit
2. `classifier.py` → 10개 카테고리 분류 (도메인 우선순위)
3. `chatbot.py` → 에스컬레이션 체크 → FAQ 매칭 (키워드 → TF-IDF)
4. `response_builder.py` → 결론/설명/근거/면책 조립
5. `logger_db.py` → 로그 저장
6. `translator.py` → 다국어 변환 (요청 시)

## FAQ 추가 체크리스트

- [ ] `data/faq.json`에 항목 추가 (id, category, question, answer, legal_basis, keywords)
- [ ] keywords가 최소 3개 이상
- [ ] legal_basis가 `data/legal_references.json`에 존재
- [ ] `src/classifier.py`의 CATEGORY_KEYWORDS에 새 키워드 추가 (필요 시)
- [ ] `python -m pytest tests/ -v` 전체 통과
- [ ] `python -c "from src.data_validator import run_all_validations; print(run_all_validations())"` 정합성 통과

## 테스트 구조

| 파일 | 대상 | 테스트 수 |
|------|------|----------|
| test_chatbot.py | 메인 챗봇 로직 | ~19 |
| test_classifier.py | 분류기 | ~14 |
| test_similarity.py | TF-IDF | ~30 |
| test_session.py | 멀티턴 세션 | ~37 |
| test_escalation.py | 에스컬레이션 | ~10 |
| test_e2e.py | E2E + 회귀 + 부하 | ~23 |
| test_web_api.py | Flask API | ~14 |
| ... | ... | ... |

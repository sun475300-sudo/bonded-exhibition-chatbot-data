"""국가법령정보센터 API 동기화 테스트."""
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.law_api_sync import LawSyncManager, LawAPIClient, MONITORED_LAWS, FAQAutoUpdater


@pytest.fixture
def sync_manager(tmp_path):
    client = LawAPIClient(oc="")
    return LawSyncManager(api_client=client, db_path=str(tmp_path / "sync.db"))


class TestLawSyncManager:
    def test_init_creates_tables(self, sync_manager):
        history = sync_manager.get_sync_history()
        assert isinstance(history, list)

    def test_record_check_new(self, sync_manager):
        changed = sync_manager._record_check("관세법", "제190조", "테스트 내용")
        assert changed is False  # first time = no previous to compare

    def test_record_check_unchanged(self, sync_manager):
        sync_manager._record_check("관세법", "제190조", "테스트 내용")
        changed = sync_manager._record_check("관세법", "제190조", "테스트 내용")
        assert changed is False

    def test_record_check_changed(self, sync_manager):
        sync_manager._record_check("관세법", "제190조", "원래 내용")
        changed = sync_manager._record_check("관세법", "제190조", "변경된 내용")
        assert changed is True

    def test_get_cached_content(self, sync_manager):
        sync_manager._record_check("관세법", "제190조", "캐시 테스트")
        cached = sync_manager.get_cached_content("관세법", "제190조")
        assert cached is not None
        assert cached["content"] == "캐시 테스트"

    def test_get_cached_content_not_found(self, sync_manager):
        cached = sync_manager.get_cached_content("없는법", "제1조")
        assert cached is None

    def test_sync_history(self, sync_manager):
        sync_manager._record_check("관세법", "제190조", "내용1")
        sync_manager._record_check("관세법", "제190조", "내용2")
        history = sync_manager.get_sync_history(limit=10)
        assert len(history) == 2

    def test_monitored_laws(self, sync_manager):
        laws = sync_manager.get_monitored_laws()
        assert len(laws) >= 2
        assert laws[0]["law_name"] == "관세법"


class TestLawAPIClient:
    def test_init_default(self):
        client = LawAPIClient()
        assert client.oc == ""

    def test_init_with_oc(self):
        client = LawAPIClient(oc="test_key")
        assert client.oc == "test_key"

    def test_get_article_text_none_xml(self):
        client = LawAPIClient()
        result = client.get_article_text(None, "제190조")
        assert result is None

    def test_get_article_text_invalid_xml(self):
        client = LawAPIClient()
        result = client.get_article_text("not xml", "제190조")
        assert result is None


class TestMonitoredLaws:
    def test_has_customs_act(self):
        names = [law["law_name"] for law in MONITORED_LAWS]
        assert "관세법" in names

    def test_has_customs_decree(self):
        names = [law["law_name"] for law in MONITORED_LAWS]
        assert "관세법 시행령" in names

    def test_articles_190(self):
        for law in MONITORED_LAWS:
            if law["law_name"] == "관세법":
                assert "제190조" in law["articles"]

    def test_articles_208(self):
        for law in MONITORED_LAWS:
            if law["law_name"] == "관세법 시행령":
                assert "제208조" in law["articles"]


class TestUpdateLegalReferences:
    def test_update_with_no_cache(self, sync_manager, tmp_path):
        ref_path = tmp_path / "legal_ref.json"
        ref_path.write_text(json.dumps({
            "references": [{"law_name": "관세법", "article": "제190조", "summary": "원래"}]
        }, ensure_ascii=False), encoding="utf-8")
        import src.law_api_sync as mod
        original_path = mod.LEGAL_REF_PATH
        mod.LEGAL_REF_PATH = str(ref_path)
        try:
            result = sync_manager.update_legal_references()
            assert result["updated"] == 0
        finally:
            mod.LEGAL_REF_PATH = original_path

    def test_update_with_cached_change(self, sync_manager, tmp_path):
        sync_manager._record_check("관세법", "제190조", "새로운 법령 내용입니다. 보세전시장은 박람회 등의 운영을 위해...")
        ref_path = tmp_path / "legal_ref.json"
        ref_path.write_text(json.dumps({
            "references": [{"law_name": "관세법", "article": "제190조", "summary": "원래 내용"}]
        }, ensure_ascii=False), encoding="utf-8")
        import src.law_api_sync as mod
        original_path = mod.LEGAL_REF_PATH
        mod.LEGAL_REF_PATH = str(ref_path)
        try:
            result = sync_manager.update_legal_references()
            assert result["updated"] == 1
        finally:
            mod.LEGAL_REF_PATH = original_path


class TestFAQAutoUpdater:
    @pytest.fixture
    def faq_file(self, tmp_path):
        faq_data = {
            "faq_version": "1.0.0",
            "last_updated": "2026-01-01",
            "items": [
                {
                    "id": "A",
                    "category": "GENERAL",
                    "question": "보세전시장?",
                    "answer": "설명 A",
                    "legal_basis": ["관세법 제190조"],
                    "keywords": ["정의"],
                },
                {
                    "id": "B",
                    "category": "SALES",
                    "question": "판매?",
                    "answer": "설명 B",
                    "legal_basis": [
                        "관세법 시행령 제101조(판매용품의 면허전 사용금지)",
                        "관세법 시행령 제102조(직매된 전시용품의 통관전 반출금지)",
                    ],
                    "keywords": ["판매"],
                },
                {
                    "id": "C",
                    "category": "GENERAL",
                    "question": "무관?",
                    "answer": "관련없음",
                    "legal_basis": ["관세법 제999조"],
                    "keywords": ["무관"],
                },
            ],
        }
        path = tmp_path / "faq.json"
        path.write_text(json.dumps(faq_data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_find_affected_faq_ids_single(self, sync_manager, faq_file):
        updater = FAQAutoUpdater(faq_path=faq_file, sync_manager=sync_manager)
        affected = updater.find_affected_faq_ids(
            [{"law_name": "관세법", "article": "제190조"}]
        )
        ids = [a["faq_id"] for a in affected]
        assert "A" in ids
        assert "B" not in ids

    def test_find_affected_faq_ids_with_paren(self, sync_manager, faq_file):
        updater = FAQAutoUpdater(faq_path=faq_file, sync_manager=sync_manager)
        affected = updater.find_affected_faq_ids(
            [{"law_name": "관세법 시행령", "article": "제101조"}]
        )
        assert len(affected) == 1
        assert affected[0]["faq_id"] == "B"

    def test_find_affected_none(self, sync_manager, faq_file):
        updater = FAQAutoUpdater(faq_path=faq_file, sync_manager=sync_manager)
        affected = updater.find_affected_faq_ids(
            [{"law_name": "없는법", "article": "제1조"}]
        )
        assert affected == []

    def test_propagate_writes_snapshot(self, sync_manager, faq_file):
        sync_manager._record_check("관세법", "제190조", "새로운 조문 내용입니다.")
        updater = FAQAutoUpdater(faq_path=faq_file, sync_manager=sync_manager)
        result = updater.propagate([{"law_name": "관세법", "article": "제190조"}])
        assert result["updated_items"] == 1
        with open(faq_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        item_a = next(i for i in data["items"] if i["id"] == "A")
        assert "law_snapshot" in item_a
        assert item_a["law_snapshot"][0]["law_name"] == "관세법"
        assert item_a["law_snapshot"][0]["summary"].startswith("새로운 조문")
        assert "last_synced" in item_a
        assert data["last_updated"]

    def test_propagate_no_matches_no_write(self, sync_manager, faq_file):
        updater = FAQAutoUpdater(faq_path=faq_file, sync_manager=sync_manager)
        before_mtime = os.path.getmtime(faq_file)
        result = updater.propagate([{"law_name": "없는법", "article": "제9조"}])
        assert result["updated_items"] == 0
        assert os.path.getmtime(faq_file) == before_mtime


class TestSyncAndPropagate:
    def test_sync_and_propagate_no_changes(self, sync_manager, tmp_path, monkeypatch):
        """API 오류 시에도 예외 없이 요약 결과를 반환한다."""
        monkeypatch.setattr(
            sync_manager.client, "get_law_text", lambda **kw: None
        )
        faq_file = tmp_path / "faq.json"
        faq_file.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
        ref_file = tmp_path / "legal_ref.json"
        ref_file.write_text(json.dumps({"references": []}, ensure_ascii=False), encoding="utf-8")
        import src.law_api_sync as mod
        original_legal = mod.LEGAL_REF_PATH
        mod.LEGAL_REF_PATH = str(ref_file)
        try:
            result = sync_manager.sync_and_propagate(
                faq_path=str(faq_file), legal_ref_path=str(ref_file)
            )
            assert "check" in result
            assert "legal_refs" in result
            assert "faq" in result
            assert result["faq"]["updated_items"] == 0
            assert result["notifications"] == 0
        finally:
            mod.LEGAL_REF_PATH = original_legal

    def test_sync_and_propagate_with_notifier(self, sync_manager, tmp_path, monkeypatch):
        """변경 감지 시 FAQUpdateNotifier 알림이 생성된다."""
        faq_file = tmp_path / "faq.json"
        faq_file.write_text(json.dumps({
            "items": [{
                "id": "TEST1",
                "category": "GENERAL",
                "question": "q",
                "answer": "a",
                "legal_basis": ["관세법 제190조(보세전시장)"],
                "keywords": [],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        ref_file = tmp_path / "legal_ref.json"
        ref_file.write_text(json.dumps({
            "references": [{
                "law_name": "관세법",
                "article": "제190조",
                "summary": "원래 요약",
            }],
        }, ensure_ascii=False), encoding="utf-8")

        # check_all은 네트워크 실패를 시뮬레이션, 대신 캐시에 변경을 주입
        monkeypatch.setattr(sync_manager.client, "get_law_text", lambda **kw: None)
        sync_manager._record_check("관세법", "제190조", "새 본문입니다. 길게 나열된 실제 조문 내용의 요약.")

        from src.law_updater import FAQUpdateNotifier
        notifier = FAQUpdateNotifier(
            faq_path=str(faq_file),
            db_path=str(tmp_path / "notif.db"),
        )

        import src.law_api_sync as mod
        original_legal = mod.LEGAL_REF_PATH
        mod.LEGAL_REF_PATH = str(ref_file)
        try:
            result = sync_manager.sync_and_propagate(
                faq_path=str(faq_file),
                legal_ref_path=str(ref_file),
                notifier=notifier,
            )
            assert result["legal_refs"]["updated"] == 1
            assert result["faq"]["updated_items"] == 1
            assert result["notifications"] == 1
            pending = notifier.get_pending_notifications()
            assert len(pending) == 1
            assert pending[0]["faq_id"] == "TEST1"
        finally:
            mod.LEGAL_REF_PATH = original_legal


class TestLawSyncAPI:
    @pytest.fixture
    def client(self):
        os.environ["ADMIN_AUTH_DISABLED"] = "true"
        os.environ["TESTING"] = "true"
        from web_server import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c
        os.environ.pop("ADMIN_AUTH_DISABLED", None)
        os.environ.pop("TESTING", None)

    def test_history_endpoint(self, client):
        res = client.get("/api/admin/law-sync/history")
        assert res.status_code == 200
        assert "history" in res.get_json()

    def test_monitored_endpoint(self, client):
        res = client.get("/api/admin/law-sync/monitored")
        assert res.status_code == 200
        data = res.get_json()
        assert "laws" in data
        assert len(data["laws"]) >= 2

    def test_propagate_endpoint(self, client):
        res = client.post("/api/admin/law-sync/propagate")
        # 네트워크 실패로 check 단계 오류가 발생해도 파이프라인은 구조화된 응답을 돌려준다
        assert res.status_code in (200, 500)
        if res.status_code == 200:
            data = res.get_json()
            assert "check" in data
            assert "legal_refs" in data
            assert "faq" in data


class TestFAQAccuracyRegression:
    """보세봇 FAQ 정확도 회귀 테스트."""

    @pytest.fixture
    def faq_data(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "data", "faq.json"), "r", encoding="utf-8") as f:
            return json.load(f)

    def test_appeal_uses_chapter_not_part(self, faq_data):
        """관세 불복 FAQ(AQ)는 '제5장' 표기를 쓰고 잘못된 '제7편' 표기를 쓰지 않는다."""
        aq = next((i for i in faq_data["items"] if i["id"] == "AQ"), None)
        assert aq is not None, "FAQ AQ가 존재해야 한다"
        assert "제7편" not in aq["answer"], "관세법은 '편'이 아닌 '장' 체계이다"
        assert "제5장" in aq["answer"] or "제119조" in aq["answer"]
        assert any("제5장" in b or "제119조" in b for b in aq["legal_basis"])

    def test_faq_last_updated_is_iso_date(self, faq_data):
        """faq.json의 last_updated는 YYYY-MM-DD 형식을 유지한다."""
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", faq_data["last_updated"])

    def test_legal_references_have_urls(self):
        """자주 참조되는 관세법 조문은 국가법령정보센터 URL을 가진다."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "data", "legal_references.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        required_ids = {"customs_act_190", "customs_act_161", "customs_act_226",
                        "customs_act_269", "customs_act_183"}
        found = {r["id"] for r in data["references"] if r.get("url")}
        missing = required_ids - found
        assert not missing, f"URL이 비어있는 조문: {missing}"

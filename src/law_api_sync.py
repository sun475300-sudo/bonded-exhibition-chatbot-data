"""국가법령정보센터 Open API 연동 모듈.

법령 원문을 자동으로 가져와 변경 여부를 감지하고,
변경 시 FAQ 및 legal_references.json을 자동 업데이트한다.

국가법령정보센터 Open API:
- 법령 검색: https://www.law.go.kr/DRF/lawSearch.do
- 법령 본문: https://www.law.go.kr/DRF/lawService.do
- XML 형식 응답

사용법:
    python -m src.law_api_sync          # 전체 동기화
    python -m src.law_api_sync --check  # 변경 확인만
"""

import json
import os
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "law_sync.db")
LEGAL_REF_PATH = os.path.join(BASE_DIR, "data", "legal_references.json")
FAQ_PATH = os.path.join(BASE_DIR, "data", "faq.json")

LAW_API_BASE = "https://www.law.go.kr/DRF/lawService.do"
LAW_SEARCH_BASE = "https://www.law.go.kr/DRF/lawSearch.do"

MONITORED_LAWS = [
    {"law_name": "관세법", "articles": ["제190조", "제161조", "제269조", "제183조", "제174조", "제226조"]},
    {"law_name": "관세법 시행령", "articles": ["제101조", "제102조", "제208조"]},
]


class LawAPIClient:
    """국가법령정보센터 Open API 클라이언트."""

    def __init__(self, oc=None):
        self.oc = oc or os.environ.get("LAW_API_OC", "")

    def search_law(self, law_name):
        """법령명으로 검색하여 법령 ID를 반환한다."""
        params = f"OC={self.oc}&target=law&type=XML&query={quote(law_name)}"
        url = f"{LAW_SEARCH_BASE}?{params}"
        try:
            req = Request(url, headers={"User-Agent": "BondedExhibitionChatbot/1.0"})
            with urlopen(req, timeout=15) as resp:
                xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            results = []
            for item in root.findall(".//law") or root.findall(".//LawSearch"):
                law_id = self._get_text(item, "법령일련번호") or self._get_text(item, "lawId") or ""
                name = self._get_text(item, "법령명한글") or self._get_text(item, "lawNameKorean") or ""
                if name:
                    results.append({"law_id": law_id, "law_name": name})
            return results
        except (URLError, ET.ParseError) as e:
            return [{"error": str(e)}]

    def get_law_text(self, law_id=None, law_name=None):
        """법령 본문을 가져온다."""
        if law_id:
            params = f"OC={self.oc}&target=law&type=XML&ID={law_id}"
        elif law_name:
            params = f"OC={self.oc}&target=law&type=XML&query={quote(law_name)}"
        else:
            return None
        url = f"{LAW_API_BASE}?{params}"
        try:
            req = Request(url, headers={"User-Agent": "BondedExhibitionChatbot/1.0"})
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except URLError:
            return None

    def get_article_text(self, xml_data, article_no):
        """XML에서 특정 조문 본문을 추출한다."""
        if not xml_data:
            return None
        try:
            root = ET.fromstring(xml_data)
            for article in root.iter():
                if article.tag in ("조문", "Article", "조문내용"):
                    no = self._get_text(article, "조문번호") or self._get_text(article, "조문제목") or ""
                    if article_no.replace("제", "").replace("조", "") in no.replace("제", "").replace("조", ""):
                        content = self._get_text(article, "조문내용") or ""
                        if not content:
                            content = "".join(article.itertext()).strip()
                        return content
            for elem in root.iter():
                text = elem.text or ""
                if article_no in text and len(text) > len(article_no) + 10:
                    return text.strip()
        except ET.ParseError:
            pass
        return None

    def _get_text(self, elem, tag):
        child = elem.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return None


class LawSyncManager:
    """법령 변경 감지 및 자동 동기화 매니저."""

    def __init__(self, api_client=None, db_path=None):
        self.client = api_client or LawAPIClient()
        self.db_path = db_path or DB_PATH
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content_preview TEXT,
                    previous_hash TEXT,
                    changed INTEGER DEFAULT 0,
                    checked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS law_content_cache (
                    law_name TEXT NOT NULL,
                    article TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (law_name, article)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def check_all(self):
        """모니터링 대상 모든 법령 조문의 변경을 확인한다."""
        results = {
            "checked_at": datetime.now().isoformat(),
            "total_checked": 0,
            "changes_detected": 0,
            "errors": 0,
            "details": [],
        }

        for law in MONITORED_LAWS:
            law_name = law["law_name"]
            xml_data = self.client.get_law_text(law_name=law_name)

            for article in law["articles"]:
                results["total_checked"] += 1
                detail = {"law_name": law_name, "article": article}

                if xml_data:
                    content = self.client.get_article_text(xml_data, article)
                    if content:
                        changed = self._record_check(law_name, article, content)
                        detail["status"] = "changed" if changed else "unchanged"
                        detail["content_preview"] = content[:100]
                        if changed:
                            results["changes_detected"] += 1
                    else:
                        detail["status"] = "article_not_found"
                        results["errors"] += 1
                else:
                    detail["status"] = "api_error"
                    results["errors"] += 1

                results["details"].append(detail)

        return results

    def check_single(self, law_name, article):
        """단일 조문의 변경을 확인한다."""
        xml_data = self.client.get_law_text(law_name=law_name)
        if not xml_data:
            return {"status": "api_error", "law_name": law_name, "article": article}

        content = self.client.get_article_text(xml_data, article)
        if not content:
            return {"status": "article_not_found", "law_name": law_name, "article": article}

        changed = self._record_check(law_name, article, content)
        return {
            "status": "changed" if changed else "unchanged",
            "law_name": law_name,
            "article": article,
            "content_preview": content[:200],
        }

    def _record_check(self, law_name, article, content):
        """조문 내용을 기록하고 변경 여부를 반환한다."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT content_hash FROM law_content_cache WHERE law_name = ? AND article = ?",
                (law_name, article),
            )
            row = cursor.fetchone()
            previous_hash = row[0] if row else None
            changed = previous_hash is not None and previous_hash != content_hash

            now = datetime.now().isoformat()
            conn.execute(
                """INSERT OR REPLACE INTO law_content_cache
                   (law_name, article, content, content_hash, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (law_name, article, content, content_hash, now),
            )
            conn.execute(
                """INSERT INTO law_sync_log
                   (law_name, article, content_hash, content_preview, previous_hash, changed, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (law_name, article, content_hash, content[:100], previous_hash, int(changed), now),
            )
            conn.commit()
            return changed
        finally:
            conn.close()

    def get_sync_history(self, limit=50):
        """동기화 이력을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT law_name, article, content_hash, content_preview,
                          previous_hash, changed, checked_at
                   FROM law_sync_log ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
            return [
                {
                    "law_name": r[0], "article": r[1], "content_hash": r[2],
                    "content_preview": r[3], "previous_hash": r[4],
                    "changed": bool(r[5]), "checked_at": r[6],
                }
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()

    def get_cached_content(self, law_name, article):
        """캐시된 조문 내용을 반환한다."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT content, content_hash, fetched_at FROM law_content_cache WHERE law_name = ? AND article = ?",
                (law_name, article),
            )
            row = cursor.fetchone()
            if row:
                return {"content": row[0], "content_hash": row[1], "fetched_at": row[2]}
            return None
        finally:
            conn.close()

    def update_legal_references(self):
        """캐시된 최신 조문으로 legal_references.json을 업데이트한다."""
        if not os.path.exists(LEGAL_REF_PATH):
            return {"updated": 0}

        with open(LEGAL_REF_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        updated = 0
        for ref in data.get("references", []):
            law_name = ref.get("law_name", "")
            article = ref.get("article", "")
            cached = self.get_cached_content(law_name, article)
            if cached and cached["content"]:
                new_summary = cached["content"][:200].strip()
                if new_summary and new_summary != ref.get("summary", ""):
                    ref["summary"] = new_summary
                    ref["last_synced"] = cached["fetched_at"]
                    updated += 1

        if updated > 0:
            with open(LEGAL_REF_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        return {"updated": updated, "total": len(data.get("references", []))}

    def apply_to_faq(self, faq_path=None):
        """캐시된 최신 조문을 FAQ 항목의 law_snippets 메타필드에 반영한다.

        ``legal_basis``에 명시된 법령/조항을 매칭하여, 최신 조문 텍스트와
        동기화 시각을 ``law_snippets`` 딕셔너리에 기록한다. 챗봇은 답변 생성
        시 이 필드를 참조하여 최신 조문 요약을 함께 안내할 수 있다.

        Args:
            faq_path: FAQ 파일 경로. 지정하지 않으면 기본 경로 사용.

        Returns:
            {"updated_faqs": int, "total_faqs": int, "updated_articles": int}
        """
        path = faq_path or FAQ_PATH
        if not os.path.exists(path):
            return {"updated_faqs": 0, "total_faqs": 0, "updated_articles": 0}

        with open(path, "r", encoding="utf-8") as f:
            faq_data = json.load(f)

        items = faq_data.get("items", [])
        updated_faqs = 0
        updated_articles = 0

        for item in items:
            legal_basis_list = item.get("legal_basis", []) or []
            snippets = item.get("law_snippets", {}) or {}
            item_changed = False

            for basis in legal_basis_list:
                law_name, article = self._parse_legal_basis(basis)
                if not law_name or not article:
                    continue
                cached = self.get_cached_content(law_name, article)
                if not cached or not cached.get("content"):
                    continue

                snippet_text = cached["content"][:300].strip()
                key = f"{law_name} {article}"
                existing = snippets.get(key, {})
                if existing.get("content_hash") == cached.get("content_hash"):
                    continue

                snippets[key] = {
                    "content": snippet_text,
                    "content_hash": cached.get("content_hash"),
                    "fetched_at": cached.get("fetched_at"),
                    "law_name": law_name,
                    "article": article,
                }
                updated_articles += 1
                item_changed = True

            if item_changed:
                item["law_snippets"] = snippets
                item["last_law_synced"] = datetime.now().isoformat()
                updated_faqs += 1

        if updated_faqs > 0:
            faq_data["last_updated"] = datetime.now().date().isoformat()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(faq_data, f, ensure_ascii=False, indent=2)

        return {
            "updated_faqs": updated_faqs,
            "total_faqs": len(items),
            "updated_articles": updated_articles,
        }

    @staticmethod
    def _parse_legal_basis(basis_text):
        """``"관세법 제190조"`` 형태에서 (법령명, 조항)을 추출한다.

        괄호로 둘러싼 조항 제목은 무시한다. 예: "제190조(보세전시장)" → 제190조.
        """
        if not basis_text:
            return None, None
        text = basis_text.strip()
        if "(" in text:
            text = text.split("(")[0].strip()
        # 마지막 토큰이 "제N조" 패턴인지 확인
        tokens = text.split()
        for idx in range(len(tokens) - 1, -1, -1):
            tok = tokens[idx]
            if tok.startswith("제") and "조" in tok:
                law_name = " ".join(tokens[:idx]).strip()
                article = tok
                if law_name and article:
                    return law_name, article
        return None, None

    def sync_all(self, apply_to_faq=True):
        """전체 동기화를 한 번에 수행한다.

        ``check_all`` → ``update_legal_references`` → ``apply_to_faq`` 순으로
        실행하여 ``data/faq.json``과 ``data/legal_references.json`` 모두
        최신 법령 본문 요약으로 갱신한다.
        """
        check_result = self.check_all()
        legal_result = self.update_legal_references()
        faq_result = {"updated_faqs": 0, "total_faqs": 0, "updated_articles": 0}
        if apply_to_faq:
            faq_result = self.apply_to_faq()
        return {
            "check": check_result,
            "legal_references": legal_result,
            "faq": faq_result,
        }

    def get_monitored_laws(self):
        """모니터링 대상 법령 목록을 반환한다."""
        return MONITORED_LAWS


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="국가법령정보센터 동기화")
    parser.add_argument("--check", action="store_true", help="변경 확인만")
    parser.add_argument("--sync", action="store_true", help="변경 확인 + legal_references 업데이트")
    parser.add_argument("--history", action="store_true", help="동기화 이력 조회")
    args = parser.parse_args()

    manager = LawSyncManager()

    if args.history:
        history = manager.get_sync_history(limit=20)
        for h in history:
            mark = "변경" if h["changed"] else "동일"
            print(f"[{mark}] {h['law_name']} {h['article']} - {h['checked_at']}")
    elif args.sync:
        print("법령 변경 확인 중...")
        result = manager.sync_all(apply_to_faq=True)
        check = result["check"]
        legal = result["legal_references"]
        faq = result["faq"]
        print(f"확인: {check['total_checked']}개, 변경: {check['changes_detected']}개, 오류: {check['errors']}개")
        print(f"legal_references 업데이트: {legal.get('updated', 0)}/{legal.get('total', 0)}개")
        print(f"FAQ 업데이트: {faq.get('updated_faqs', 0)}/{faq.get('total_faqs', 0)}개"
              f" (조항 {faq.get('updated_articles', 0)}건)")
    else:
        print("법령 변경 확인 중...")
        result = manager.check_all()
        print(f"확인: {result['total_checked']}개, 변경: {result['changes_detected']}개")
        for d in result["details"]:
            print(f"  {d['law_name']} {d['article']}: {d['status']}")

"""FAQ quality checker for operations automation."""


class FAQQualityChecker:
    """Validates FAQ data quality and flags potential issues."""

    def __init__(self, faq_items: list[dict], legal_refs: list | dict):
        """Initialize with FAQ data and legal references.

        Args:
            faq_items: List of FAQ dicts. Each should have at minimum:
                id, question, answer, keywords, category, and optionally legal_basis.
            legal_refs: Legal reference data -- either a list of dicts with "id" keys,
                or a dict keyed by reference id.
        """
        self.faq_items = faq_items

        # Normalize legal_refs to a set of valid ids
        if isinstance(legal_refs, dict):
            self._legal_ref_ids: set[str] = set(legal_refs.keys())
        elif isinstance(legal_refs, list):
            self._legal_ref_ids = {
                str(ref.get("id", ref)) if isinstance(ref, dict) else str(ref)
                for ref in legal_refs
            }
        else:
            self._legal_ref_ids = set()

    def check_all(self) -> dict:
        """Run all quality checks and return an aggregate report.

        Returns:
            {
                "passed": bool,
                "issues": list[dict],
                "score": float  # 0.0 to 1.0
            }
        """
        all_issues: list[dict] = []

        duplicates = self.check_duplicates()
        if duplicates:
            all_issues.append({
                "check": "duplicates",
                "count": len(duplicates),
                "details": duplicates,
            })

        keyword_issues = self.check_keyword_coverage()
        if keyword_issues:
            all_issues.append({
                "check": "keyword_coverage",
                "count": len(keyword_issues),
                "details": keyword_issues,
            })

        legal_issues = self.check_legal_refs()
        if legal_issues:
            all_issues.append({
                "check": "legal_refs",
                "count": len(legal_issues),
                "details": legal_issues,
            })

        answer_issues = self.check_answer_consistency()
        if answer_issues:
            all_issues.append({
                "check": "answer_consistency",
                "count": len(answer_issues),
                "details": answer_issues,
            })

        balance_result = self.check_category_balance()
        underrepresented = balance_result.get("underrepresented", [])
        if underrepresented:
            all_issues.append({
                "check": "category_balance",
                "count": len(underrepresented),
                "details": underrepresented,
            })

        # Score: fraction of checks that passed (5 checks total)
        checks_failed = len(all_issues)
        total_checks = 5
        score = round((total_checks - checks_failed) / total_checks, 2)

        return {
            "passed": checks_failed == 0,
            "issues": all_issues,
            "score": score,
        }

    def check_duplicates(self) -> list[dict]:
        """Find FAQ pairs with high keyword overlap (>60% Jaccard similarity).

        Returns:
            List of {"faq1": id, "faq2": id, "overlap": float}.
        """
        duplicates: list[dict] = []
        n = len(self.faq_items)

        for i in range(n):
            kw_i = self._get_keyword_set(self.faq_items[i])
            if not kw_i:
                continue
            faq_id_i = self.faq_items[i].get("id", i)

            for j in range(i + 1, n):
                kw_j = self._get_keyword_set(self.faq_items[j])
                if not kw_j:
                    continue

                intersection = len(kw_i & kw_j)
                union = len(kw_i | kw_j)
                if union == 0:
                    continue

                jaccard = intersection / union
                if jaccard > 0.6:
                    faq_id_j = self.faq_items[j].get("id", j)
                    duplicates.append({
                        "faq1": faq_id_i,
                        "faq2": faq_id_j,
                        "overlap": round(jaccard, 4),
                    })

        return duplicates

    def check_keyword_coverage(self) -> list[dict]:
        """Find FAQs with fewer than 3 keywords.

        Returns:
            List of {"faq_id": id, "keyword_count": int, "keywords": list}.
        """
        issues: list[dict] = []

        for faq in self.faq_items:
            keywords = faq.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]

            if len(keywords) < 3:
                issues.append({
                    "faq_id": faq.get("id"),
                    "keyword_count": len(keywords),
                    "keywords": keywords,
                })

        return issues

    def check_legal_refs(self) -> list[dict]:
        """Find FAQs referencing legal_basis entries not in the legal references.

        Returns:
            List of {"faq_id": id, "invalid_refs": list[str]}.
        """
        issues: list[dict] = []

        for faq in self.faq_items:
            legal_basis = faq.get("legal_basis")
            if not legal_basis:
                continue

            # Normalize to list
            if isinstance(legal_basis, str):
                refs = [legal_basis]
            elif isinstance(legal_basis, list):
                refs = legal_basis
            else:
                continue

            invalid = [
                str(ref) for ref in refs if str(ref) not in self._legal_ref_ids
            ]
            if invalid:
                issues.append({
                    "faq_id": faq.get("id"),
                    "invalid_refs": invalid,
                })

        return issues

    def check_answer_consistency(self) -> list[dict]:
        """Find FAQs with answers shorter than 20 chars or missing key phrases.

        Returns:
            List of {"faq_id": id, "issue": str, "answer_length": int}.
        """
        issues: list[dict] = []

        for faq in self.faq_items:
            answer = faq.get("answer", "")
            faq_id = faq.get("id")
            answer_len = len(answer)

            if answer_len < 20:
                issues.append({
                    "faq_id": faq_id,
                    "issue": "answer_too_short",
                    "answer_length": answer_len,
                })
                continue

            # Check for missing key structural phrases
            missing_phrases: list[str] = []
            # Answers should contain some form of definitive statement
            has_any_key_phrase = False
            key_indicators = [".", "다.", "니다.", "습니다.", "입니다."]
            for indicator in key_indicators:
                if indicator in answer:
                    has_any_key_phrase = True
                    break

            if not has_any_key_phrase:
                missing_phrases.append("ending_punctuation")

            if missing_phrases:
                issues.append({
                    "faq_id": faq_id,
                    "issue": "missing_key_phrases",
                    "missing": missing_phrases,
                    "answer_length": answer_len,
                })

        return issues

    def check_category_balance(self) -> dict:
        """Check category distribution and flag underrepresented categories.

        A category is underrepresented if it has fewer than 3 FAQs.

        Returns:
            {
                "distribution": {category: count, ...},
                "total_categories": int,
                "underrepresented": [{"category": str, "count": int}, ...]
            }
        """
        distribution: dict[str, int] = {}
        for faq in self.faq_items:
            cat = faq.get("category", "uncategorized")
            distribution[cat] = distribution.get(cat, 0) + 1

        underrepresented = [
            {"category": cat, "count": count}
            for cat, count in sorted(distribution.items())
            if count < 3
        ]

        return {
            "distribution": distribution,
            "total_categories": len(distribution),
            "underrepresented": underrepresented,
        }

    @staticmethod
    def _get_keyword_set(faq: dict) -> set[str]:
        """Extract a normalized set of keywords from a FAQ item."""
        keywords = faq.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        return {kw.strip().lower() for kw in keywords if isinstance(kw, str)}

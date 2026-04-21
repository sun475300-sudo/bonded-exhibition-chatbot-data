"""Knowledge graph for FAQ relationships.

Builds a graph of FAQ items, concepts, laws, and categories,
with edges representing semantic relationships between them.
"""

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple


class KnowledgeGraph:
    """Graph of FAQ items, concepts, laws, and categories."""

    VALID_NODE_TYPES = {"faq", "concept", "law", "category"}
    VALID_RELATIONS = {"related_to", "requires", "part_of", "cites"}

    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        # adjacency: node_id -> list of (target, relation, weight)
        self._adj: Dict[str, List[Tuple[str, str, float]]] = {}

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, node_type: str, data: Optional[Dict] = None) -> None:
        """Add a node to the graph.

        Args:
            node_id: Unique identifier.
            node_type: One of ``faq``, ``concept``, ``law``, ``category``.
            data: Arbitrary payload stored on the node.
        """
        if node_type not in self.VALID_NODE_TYPES:
            raise ValueError(f"Invalid node_type '{node_type}'. Must be one of {self.VALID_NODE_TYPES}")
        self.nodes[node_id] = {"id": node_id, "type": node_type, "data": data or {}}
        if node_id not in self._adj:
            self._adj[node_id] = []

    def add_edge(self, source: str, target: str, relation: str, weight: float = 1.0) -> None:
        """Add a directed edge between two nodes.

        Args:
            source: Source node id (must exist).
            target: Target node id (must exist).
            relation: One of ``related_to``, ``requires``, ``part_of``, ``cites``.
            weight: Edge weight (default 1.0).
        """
        if relation not in self.VALID_RELATIONS:
            raise ValueError(f"Invalid relation '{relation}'. Must be one of {self.VALID_RELATIONS}")
        if source not in self.nodes:
            raise KeyError(f"Source node '{source}' not found")
        if target not in self.nodes:
            raise KeyError(f"Target node '{target}' not found")

        # Avoid duplicate edges
        for existing in self.edges:
            if existing["source"] == source and existing["target"] == target and existing["relation"] == relation:
                existing["weight"] = weight
                return

        edge = {"source": source, "target": target, "relation": relation, "weight": weight}
        self.edges.append(edge)
        self._adj[source].append((target, relation, weight))
        self._adj[target].append((source, relation, weight))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_neighbors(self, node_id: str, relation: Optional[str] = None, depth: int = 1) -> List[Dict]:
        """Return connected nodes up to *depth* hops away.

        Args:
            node_id: Starting node.
            relation: If given, only follow edges with this relation.
            depth: How many hops to traverse (default 1).

        Returns:
            List of node dicts.
        """
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")

        visited: Set[str] = {node_id}
        queue: deque = deque()
        queue.append((node_id, 0))
        results: List[Dict] = []

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue
            for target, rel, _w in self._adj.get(current, []):
                if target in visited:
                    continue
                if relation and rel != relation:
                    continue
                visited.add(target)
                results.append(self.nodes[target])
                queue.append((target, d + 1))

        return results

    def find_path(self, source: str, target: str) -> List[str]:
        """BFS shortest path between two nodes.

        Returns:
            List of node ids forming the path, or empty list if no path.
        """
        if source not in self.nodes:
            raise KeyError(f"Node '{source}' not found")
        if target not in self.nodes:
            raise KeyError(f"Node '{target}' not found")
        if source == target:
            return [source]

        visited: Set[str] = {source}
        queue: deque = deque()
        queue.append((source, [source]))

        while queue:
            current, path = queue.popleft()
            for neighbor, _rel, _w in self._adj.get(current, []):
                if neighbor in visited:
                    continue
                new_path = path + [neighbor]
                if neighbor == target:
                    return new_path
                visited.add(neighbor)
                queue.append((neighbor, new_path))

        return []

    def get_subgraph(self, node_id: str, depth: int = 2) -> Dict:
        """Return a local subgraph around *node_id*.

        Returns:
            ``{"nodes": [...], "edges": [...]}``
        """
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")

        visited: Set[str] = {node_id}
        queue: deque = deque()
        queue.append((node_id, 0))

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue
            for neighbor, _rel, _w in self._adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, d + 1))

        sub_nodes = [self.nodes[nid] for nid in visited]
        sub_edges = [
            e for e in self.edges if e["source"] in visited and e["target"] in visited
        ]
        return {"nodes": sub_nodes, "edges": sub_edges}

    def search_nodes(self, query: str) -> List[Dict]:
        """Find nodes whose id or data contains *query* (case-insensitive)."""
        query_lower = query.lower()
        results: List[Dict] = []
        for node in self.nodes.values():
            if query_lower in node["id"].lower():
                results.append(node)
                continue
            data = node.get("data", {})
            for value in data.values():
                if isinstance(value, str) and query_lower in value.lower():
                    results.append(node)
                    break
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and query_lower in item.lower():
                            results.append(node)
                            break
        return results

    def get_related_concepts(self, faq_id: str) -> List[Dict]:
        """Return concept nodes related to the given FAQ node."""
        if faq_id not in self.nodes:
            raise KeyError(f"Node '{faq_id}' not found")
        neighbors = self.get_neighbors(faq_id, depth=2)
        return [n for n in neighbors if n["type"] == "concept"]

    # ------------------------------------------------------------------
    # Statistics / export
    # ------------------------------------------------------------------

    def get_graph_stats(self) -> Dict:
        """Return node/edge counts and density."""
        n = len(self.nodes)
        e = len(self.edges)
        max_edges = n * (n - 1) if n > 1 else 1
        density = e / max_edges if max_edges else 0.0
        type_counts: Dict[str, int] = {}
        for node in self.nodes.values():
            t = node["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        relation_counts: Dict[str, int] = {}
        for edge in self.edges:
            r = edge["relation"]
            relation_counts[r] = relation_counts.get(r, 0) + 1
        return {
            "node_count": n,
            "edge_count": e,
            "density": round(density, 6),
            "node_types": type_counts,
            "relation_types": relation_counts,
        }

    def export_graph(self) -> Dict:
        """Export full graph as JSON-serialisable dict."""
        return {
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges),
        }

    # ------------------------------------------------------------------
    # Auto-build from FAQ data
    # ------------------------------------------------------------------

    @classmethod
    def build_from_faq(cls, faq_items: List[Dict], law_references: Optional[List[Dict]] = None) -> "KnowledgeGraph":
        """Construct a knowledge graph from FAQ items.

        Auto-detects relationships based on:
        - Shared keywords  -> ``related_to``
        - Same legal_basis -> ``cites``
        - Same category    -> ``part_of``
        """
        graph = cls()

        # 법령 참조 데이터를 맵으로 변환 (빠른 조회를 위해)
        # FAQ 의 legal_basis 문자열은 "관세법 제190조" 처럼 법령명과 조문이
        # 결합된 형태이므로, 두 필드를 결합한 키와 조문 단독 키 모두를
        # 등록하여 부분 매칭이 가능하도록 한다.
        law_ref_map: Dict[str, Dict] = {}
        law_refs_list: List[Dict] = []
        if law_references:
            for ref in law_references:
                article = ref.get("article", "")
                law_name = ref.get("law_name", "")
                if article:
                    # 조문 단독 (뒤늦게 등록되면 덮어쓰지 않음)
                    law_ref_map.setdefault(article, ref)
                if law_name and article:
                    law_ref_map[f"{law_name} {article}"] = ref
                if law_name:
                    law_ref_map.setdefault(law_name, ref)
                law_refs_list.append(ref)

        def _lookup_ref(basis: str) -> Dict:
            """legal_basis 문자열에 가장 잘 맞는 법령 참조를 찾는다."""
            if not basis:
                return {}
            # 1) 정확/완전 일치
            if basis in law_ref_map:
                return law_ref_map[basis]
            # 2) "법령명 조문" 조합이 basis 에 포함되는 ref 선택
            best = {}
            best_len = 0
            for ref in law_refs_list:
                law_name = ref.get("law_name", "")
                article = ref.get("article", "")
                if law_name and article and law_name in basis and article in basis:
                    # 더 구체적으로 일치하는 ref(law_name + article 길이 합) 선호
                    score = len(law_name) + len(article)
                    if score > best_len:
                        best = ref
                        best_len = score
            if best:
                return best
            # 3) 조문만이라도 일치하는 ref
            for ref in law_refs_list:
                article = ref.get("article", "")
                if article and article in basis:
                    return ref
            # 4) 법령명만 일치
            for ref in law_refs_list:
                law_name = ref.get("law_name", "")
                if law_name and law_name in basis:
                    return ref
            return {}

        # Collect unique categories and legal bases
        categories: Dict[str, List[str]] = {}
        laws: Dict[str, List[str]] = {}
        keyword_map: Dict[str, List[str]] = {}  # keyword -> [faq_id, ...]

        for item in faq_items:
            faq_id = f"faq_{item['id']}"
            graph.add_node(faq_id, "faq", {
                "question": item.get("question", ""),
                "category": item.get("category", ""),
                "keywords": item.get("keywords", []),
            })

            # Category tracking
            cat = item.get("category", "")
            if cat:
                cat_id = f"cat_{cat}"
                if cat_id not in graph.nodes:
                    graph.add_node(cat_id, "category", {"name": cat})
                categories.setdefault(cat, []).append(faq_id)

            # Legal basis tracking
            for basis in item.get("legal_basis", []):
                law_id = f"law_{basis}"
                if law_id not in graph.nodes:
                    # 법령 참조 데이터에서 요약 정보 가져오기 (부분 매칭 지원)
                    ref_data = _lookup_ref(basis)
                    node_data = {
                        "name": basis,
                        "title": ref_data.get("title", ""),
                        "summary": ref_data.get("summary", ""),
                        "url": ref_data.get("url", ""),
                        "last_synced": ref_data.get("last_synced", ""),
                    }
                    graph.add_node(law_id, "law", node_data)
                laws.setdefault(basis, []).append(faq_id)

            # Keyword tracking -> concept nodes
            for kw in item.get("keywords", []):
                kw_lower = kw.lower()
                concept_id = f"concept_{kw_lower}"
                if concept_id not in graph.nodes:
                    graph.add_node(concept_id, "concept", {"name": kw})
                keyword_map.setdefault(kw_lower, []).append(faq_id)

        # --- Build edges ---

        # 1. FAQ -> category (part_of)
        for cat, faq_ids in categories.items():
            cat_id = f"cat_{cat}"
            for faq_id in faq_ids:
                graph.add_edge(faq_id, cat_id, "part_of")

        # 2. FAQ -> law (cites)
        for basis, faq_ids in laws.items():
            law_id = f"law_{basis}"
            for faq_id in faq_ids:
                graph.add_edge(faq_id, law_id, "cites")

        # 3. FAQ <-> FAQ via shared keywords (related_to)
        # Also FAQ -> concept edges
        for kw_lower, faq_ids in keyword_map.items():
            concept_id = f"concept_{kw_lower}"
            for faq_id in faq_ids:
                graph.add_edge(faq_id, concept_id, "related_to")

            # Connect FAQ pairs that share a keyword
            for i in range(len(faq_ids)):
                for j in range(i + 1, len(faq_ids)):
                    graph.add_edge(faq_ids[i], faq_ids[j], "related_to")

        # 4. FAQ <-> FAQ via same legal basis (cites between FAQs)
        for basis, faq_ids in laws.items():
            for i in range(len(faq_ids)):
                for j in range(i + 1, len(faq_ids)):
                    # Already may have related_to; add cites as well
                    graph.add_edge(faq_ids[i], faq_ids[j], "cites")

        return graph

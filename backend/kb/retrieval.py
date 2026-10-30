"""
混合检索 — 向量相似度 (pgvector) + 关键词匹配，RRF 融合排序

关键词侧不依赖中文分词器：查询拆成英文词 + 中文 2 字滑窗，
按命中词数（标题命中加权）打分，ILIKE 由 pg_trgm GIN 索引加速。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import text

from backend.config import RAG_MIN_SIMILARITY, RAG_RELATIVE_SIMILARITY
from backend.database import get_connection
from backend.kb import embeddings

logger = logging.getLogger("nl2sql.kb.retrieval")

_RRF_K = 60
_CANDIDATES = 40
_STOP_BIGRAMS = {"什么", "怎么", "哪些", "如何", "为什", "么是", "是什", "一下", "多少", "我们", "可以", "这个", "那个"}


@dataclass
class Hit:
    chunk_id: int
    page_id: int
    slug: str
    title: str
    heading_path: str
    content: str
    score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "page_id": self.page_id,
            "slug": self.slug,
            "title": self.title,
            "heading_path": self.heading_path,
            "content": self.content,
            "snippet": make_snippet(self.content, self.matched_terms),
            "score": round(self.score, 4),
            "vector_score": None if self.vector_score is None else round(self.vector_score, 4),
            "keyword_score": self.keyword_score,
        }


def query_terms(query: str, limit: int = 12) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_.%]+|[一-鿿]+", query):
        if re.match(r"[一-鿿]", token):
            if len(token) <= 2:
                terms.append(token)
            else:
                terms.extend(token[i:i + 2] for i in range(len(token) - 1))
        elif len(token) >= 2:
            terms.append(token)
    seen, out = set(), []
    for term in terms:
        key = term.lower()
        if key not in seen and term not in _STOP_BIGRAMS:
            seen.add(key)
            out.append(term)
    return out[:limit]


def _plain(content: str) -> str:
    """片段展示用的纯文本：表格行转成「单元格 / 单元格」，去掉分隔线、标题井号、列表符号和强调符号。"""
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\|?[\s:|-]+\|?", line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            line = " / ".join(cells)
        line = re.sub(r"^(#+|[-+*]|\d+\.)\s+", "", line)
        lines.append(re.sub(r"[*_`]{1,3}", "", line))
    joined = "".join(l if re.search(r"[。！？；.!?;：:]$", l) else f"{l}；" for l in lines).rstrip("；")
    return re.sub(r"\s+", " ", joined).strip()


def make_snippet(content: str, terms: list[str], width: int = 160) -> str:
    flat = _plain(content)
    pos = -1
    for term in terms:
        pos = flat.lower().find(term.lower())
        if pos >= 0:
            break
    start = max(0, pos - width // 3) if pos >= 0 else 0
    snippet = flat[start:start + width]
    return ("…" if start > 0 else "") + snippet + ("…" if start + width < len(flat) else "")


def _space_filter(spaces: list[int] | None) -> str:
    """检索范围：spaces 为 None 时不限制（系统内部），否则只检索这些知识空间。"""
    return "" if spaces is None else " AND p.space_id = ANY(:spaces)"


def _keyword_search(terms: list[str], limit: int, spaces: list[int] | None = None) -> list[Hit]:
    if not terms:
        return []
    params: dict = {"limit": limit, "spaces": spaces}
    content_hits, title_hits = [], []
    for i, term in enumerate(terms):
        params[f"t{i}"] = f"%{term}%"
        content_hits.append(f"(c.content ILIKE :t{i} OR c.heading_path ILIKE :t{i})::int")
        title_hits.append(f"(p.title ILIKE :t{i})::int")
    score = f"({' + '.join(content_hits)}) + 0.5 * ({' + '.join(title_hits)})"
    any_hit = " OR ".join(f"c.content ILIKE :t{i} OR c.heading_path ILIKE :t{i} OR p.title ILIKE :t{i}" for i in range(len(terms)))
    sql = f"""
        SELECT c.id, c.page_id, p.slug, p.title, c.heading_path, c.content, {score} AS score
        FROM kb_chunks c JOIN wiki_pages p ON p.id = c.page_id AND NOT p.is_deleted{_space_filter(spaces)}
        WHERE {any_hit}
        ORDER BY score DESC, c.char_count ASC
        LIMIT :limit
    """
    with get_connection() as conn:
        rows = conn.execute(text(sql), params).all()
    min_score = 1 if len(terms) <= 2 else 2  # 多词查询至少命中 2 个词，过滤掉只撞上一个常见字的噪音
    return [
        Hit(r[0], r[1], r[2], r[3], r[4], r[5], keyword_score=float(r[6]),
            matched_terms=[t for t in terms if t.lower() in (r[5] + r[4]).lower()])
        for r in rows if float(r[6]) >= min_score
    ]


def _vector_search(query: str, limit: int, spaces: list[int] | None = None) -> list[Hit]:
    vec = embeddings.embed([query])[0]
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT c.id, c.page_id, p.slug, p.title, c.heading_path, c.content,
                   1 - (c.embedding <=> CAST(:v AS vector)) AS similarity
            FROM kb_chunks c JOIN wiki_pages p ON p.id = c.page_id AND NOT p.is_deleted{_space_filter(spaces)}
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:v AS vector)
            LIMIT :limit
        """), {"v": embeddings.to_pgvector(vec), "limit": limit, "spaces": spaces}).all()
    if not rows:
        return []
    floor = max(RAG_MIN_SIMILARITY, float(rows[0][6]) - RAG_RELATIVE_SIMILARITY)
    return [Hit(r[0], r[1], r[2], r[3], r[4], r[5], vector_score=float(r[6])) for r in rows if float(r[6]) >= floor]


def search(query: str, top_k: int = 6, spaces: list[int] | None = None) -> dict:
    """返回 {mode, hits}，mode 为 hybrid / keyword，便于前端提示检索方式。"""
    query = query.strip()
    if not query:
        return {"mode": "keyword", "hits": []}
    terms = query_terms(query)
    keyword_hits = _keyword_search(terms, _CANDIDATES, spaces)
    vector_hits: list[Hit] = []
    mode = "keyword"
    if embeddings.available():
        try:
            vector_hits = _vector_search(query, _CANDIDATES, spaces)
            mode = "hybrid"
        except embeddings.EmbeddingError as exc:
            logger.warning("向量检索不可用，降级为关键词检索: %s", exc)

    fused: dict[int, Hit] = {}
    for ranked in (vector_hits, keyword_hits):
        for rank, hit in enumerate(ranked):
            existing = fused.get(hit.chunk_id)
            if existing is None:
                existing = fused[hit.chunk_id] = hit
            else:
                existing.vector_score = existing.vector_score if existing.vector_score is not None else hit.vector_score
                existing.keyword_score = existing.keyword_score if existing.keyword_score is not None else hit.keyword_score
            existing.score += 1.0 / (_RRF_K + rank + 1)
    for hit in fused.values():
        if not hit.matched_terms:
            hit.matched_terms = [t for t in terms if t.lower() in hit.content.lower()]

    ranked = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    # 同一页面最多取 2 个块，保证答案来源多样
    per_page: dict[int, int] = {}
    results = []
    for hit in ranked:
        if per_page.get(hit.page_id, 0) >= 2:
            continue
        per_page[hit.page_id] = per_page.get(hit.page_id, 0) + 1
        results.append(hit)
        if len(results) >= top_k:
            break
    return {"mode": mode, "hits": results}


def search_pages(query: str, limit: int = 20, spaces: list[int] | None = None) -> dict:
    """Wiki 搜索：按页面聚合，并补充标题直接匹配的页面。"""
    result = search(query, top_k=limit * 2, spaces=spaces)
    pages: dict[int, dict] = {}
    for hit in result["hits"]:
        page = pages.setdefault(hit.page_id, {"slug": hit.slug, "title": hit.title, "score": 0.0, "sections": []})
        page["score"] += hit.score
        if len(page["sections"]) < 2:
            page["sections"].append({"heading_path": hit.heading_path, "snippet": make_snippet(hit.content, hit.matched_terms)})
    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT id, slug, title, summary FROM wiki_pages p
            WHERE NOT is_deleted AND (title ILIKE :q OR slug ILIKE :q OR :raw = ANY(tags)){_space_filter(spaces)}
            LIMIT 10
        """), {"q": f"%{query.strip()}%", "raw": query.strip(), "spaces": spaces}).all()
    for r in rows:
        page = pages.setdefault(r[0], {"slug": r[1], "title": r[2], "score": 0.0, "sections": []})
        page["score"] += 1.0  # 标题精确命中排最前
        if not page["sections"]:
            page["sections"].append({"heading_path": "", "snippet": r[3]})
    ordered = sorted(pages.values(), key=lambda p: p["score"], reverse=True)[:limit]
    return {"mode": result["mode"], "pages": ordered}

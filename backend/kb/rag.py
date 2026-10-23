"""
RAG 问答 — 混合检索 Top-K 片段 → LLM 生成带 [n] 引用的回答

- 只允许依据检索到的资料回答；资料不足时明确说"知识库中没有找到"，不编造
- 未配置 LLM 或调用失败时，返回抽取式答案（最相关片段），问答不中断
"""

from __future__ import annotations

import logging
import re

from backend.config import RAG_TOP_K
from backend.kb.retrieval import search
from backend.llm_gateway import get_provider
from backend.llm_service import chat

logger = logging.getLogger("nl2sql.kb.rag")

_SYSTEM = """你是企业知识库助手。只能依据【参考资料】回答用户问题，遵守：
1. 每个关键结论后用 [编号] 标注来源，例如「GMV 按有效订单金额汇总 [1]」；可以同时引用多个 [1][3]。
2. 资料里没有的信息不要编造；如果资料不足以回答，直接说明“知识库中没有找到相关内容”，并建议补充哪类文档。
3. 用简洁的中文回答，先给结论，再分点说明；涉及数字、口径、流程时保持与资料原文一致。
"""


def ask(question: str, top_k: int = RAG_TOP_K, provider=None) -> dict:
    question = question.strip()
    result = search(question, top_k=top_k)
    hits = result["hits"]
    citations = [{"n": i + 1, **hit.to_dict()} for i, hit in enumerate(hits)]

    if not hits:
        return {
            "question": question,
            "answer": "知识库中没有找到与这个问题相关的内容。可以换个说法，或把相关制度/手册上传到知识库后再问。",
            "citations": [],
            "retrieval": result["mode"],
            "generation": "none",
        }

    context = "\n\n".join(
        f"[{c['n']}] 《{c['title']}》{(' / ' + c['heading_path']) if c['heading_path'] else ''}\n{c['content']}"
        for c in citations
    )
    provider = provider or get_provider()
    if provider.configured():
        try:
            answer = chat(_SYSTEM, f"【参考资料】\n{context}\n\n【问题】\n{question}", provider=provider)
            used = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
            for c in citations:
                c["cited"] = c["n"] in used
            return {"question": question, "answer": answer, "citations": citations,
                    "retrieval": result["mode"], "generation": "llm"}
        except RuntimeError as exc:
            logger.warning("RAG 生成失败，降级为抽取式答案: %s", exc)

    top = citations[: min(3, len(citations))]
    lines = ["以下是知识库中与问题最相关的内容（未启用大模型，按相关度直接摘录）：", ""]
    for c in top:
        section = c["heading_path"].split(" > ")[-1] if c["heading_path"] else ""
        where = f"《{c['title']}》" + (f" · {section}" if section and section != c["title"] else "")
        lines.append(f"- {where}：{c['snippet']} [{c['n']}]")
        c["cited"] = True
    return {"question": question, "answer": "\n".join(lines), "citations": citations,
            "retrieval": result["mode"], "generation": "extractive"}


def knowledge_for_agent(question: str, limit: int = 6) -> list[dict]:
    """供经营分析 Agent 的「检索业务知识」节点使用。"""
    hits = search(question, top_k=limit)["hits"]
    return [
        {"source": f"{h.title}" + (f" / {h.heading_path}" if h.heading_path else ""), "content": h.content[:400], "slug": h.slug}
        for h in hits
    ]

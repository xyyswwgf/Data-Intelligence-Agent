import { useMemo } from "react";
import Icon from "./Icon";
import { renderMarkdown } from "../utils/markdown";

/** 知识问答回答：正文中的 [n] 变成可点击的引用标记，下方列出来源卡片 */
export default function KbAnswer({ result, onOpenPage }) {
  const html = useMemo(() => {
    const { html: rendered } = renderMarkdown(result.answer);
    return rendered.replace(/\[(\d+)\]/g, (m, n) => `<sup class="cite" data-cite="${n}">${n}</sup>`);
  }, [result.answer]);

  const onClick = (e) => {
    const cite = e.target.closest("[data-cite]");
    if (cite) {
      const target = result.citations.find((c) => String(c.n) === cite.dataset.cite);
      if (target) onOpenPage(target.slug);
    }
    const link = e.target.closest("a[data-wiki]");
    if (link) {
      e.preventDefault();
      onOpenPage(link.dataset.wiki);
    }
  };

  const cited = result.citations.filter((c) => c.cited !== false);
  return (
    <div className="answer">
      <div className="summary kb-answer">
        <div className="markdown-body" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
        <div className="summary-meta">
          <span className="tag">{result.retrieval === "hybrid" ? "向量 + 关键词混合检索" : "关键词检索"}</span>
          <span className={`tag ${result.generation === "extractive" ? "warn" : ""}`}>
            {result.generation === "llm" ? "大模型生成" : result.generation === "extractive" ? "抽取式摘录（未启用大模型）" : "无匹配"}
          </span>
        </div>
      </div>
      {cited.length > 0 && (
        <div className="citation-list">
          {cited.map((c) => (
            <button type="button" className="citation" key={c.chunk_id} onClick={() => onOpenPage(c.slug)}>
              <span className="citation-n">{c.n}</span>
              <span className="citation-body">
                <strong>
                  <Icon name="file" size={13} /> {c.title}
                  {c.heading_path && <small> · {c.heading_path.split(" > ").slice(-1)[0]}</small>}
                </strong>
                <span>{c.snippet}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

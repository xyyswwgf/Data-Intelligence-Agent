import { useMemo } from "react";
import Icon from "../Icon";
import { downloadKnowledgeDocument } from "../../api";
import { renderMarkdown } from "../../utils/markdown";

const INDEX_LABEL = {
  pending: ["索引排队中", "warn"],
  indexing: ["索引中", "warn"],
  ready: ["已索引", "ok"],
  partial: ["仅关键词索引", "warn"],
  failed: ["索引失败", "bad"],
};

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN") : "";
}

export default function WikiPage({ page, existingSlugs, canEdit, canDelete, onOpen, onEdit, onHistory, onDelete, onCreateChild, onCreateMissing }) {
  // 正文第一行若是与页面标题相同的一级标题，就不再重复显示
  const body = useMemo(() => {
    const match = page.content_md.match(/^\s*#\s+(.+)\n?/);
    return match && match[1].trim() === page.title.trim() ? page.content_md.slice(match[0].length) : page.content_md;
  }, [page.content_md, page.title]);
  const { html, toc } = useMemo(() => renderMarkdown(body, { existingSlugs }), [body, existingSlugs]);
  const [indexLabel, indexTone] = INDEX_LABEL[page.index_status] || ["", ""];

  const handleClick = (e) => {
    const link = e.target.closest("a[data-wiki]");
    if (link) {
      e.preventDefault();
      onOpen(link.dataset.wiki);
    }
  };

  return (
    <div className="wiki-page-layout">
      <article className="wiki-article">
        {page.breadcrumbs.length > 0 && (
          <nav className="breadcrumbs">
            {page.breadcrumbs.map((b) => (
              <span key={b.slug}>
                <button type="button" onClick={() => onOpen(b.slug)}>{b.title}</button>
                <Icon name="chevron" size={12} />
              </span>
            ))}
          </nav>
        )}
        <header className="wiki-article-head">
          <div>
            <h1>{page.title}</h1>
            <p className="wiki-meta">
              {page.space_name && (
                <span className={`space-pill ${page.space_kind}`}>
                  <Icon name={page.space_kind === "personal" ? "user" : "users"} size={11} /> {page.space_name}
                </span>
              )}
              v{page.version} · {page.updated_by_name || "系统"} 更新于 {formatTime(page.updated_at)}
              <span className={`index-pill ${indexTone}`} title={page.index_error || ""}>{indexLabel}</span>
            </p>
            {page.tags.length > 0 && (
              <div className="tag-cloud">
                {page.tags.map((t) => <span className="tag-chip static" key={t}>#{t}</span>)}
              </div>
            )}
          </div>
          <div className="wiki-actions">
            {page.document_id && (
              <button type="button" className="ghost-btn" onClick={() => downloadKnowledgeDocument(page.document_id, page.document_filename)} title="下载原始文件">
                <Icon name="download" size={15} /> 原文
              </button>
            )}
            <button type="button" className="ghost-btn" onClick={onHistory}><Icon name="history" size={15} /> 历史</button>
            {canEdit && <button type="button" className="ghost-btn" onClick={onCreateChild}><Icon name="plus" size={15} /> 子页面</button>}
            {canEdit && <button type="button" className="btn-primary" onClick={onEdit}><Icon name="edit" size={15} /> 编辑</button>}
            {canDelete && <button type="button" className="icon-btn danger" onClick={onDelete} title="删除页面" aria-label="删除页面"><Icon name="trash" size={16} /></button>}
          </div>
        </header>

        {page.document_filename && (
          <div className="callout info">
            <Icon name="file" size={16} />
            <span>本页由文档《{page.document_filename}》自动转换生成，可直接编辑修正转换结果。</span>
          </div>
        )}

        <div className="markdown-body" onClick={handleClick} dangerouslySetInnerHTML={{ __html: html }} />

        {page.missing_links.length > 0 && canEdit && (
          <div className="callout info">
            <Icon name="link" size={16} />
            <span>
              本页引用了尚未创建的页面：
              {page.missing_links.map((slug) => (
                <button type="button" key={slug} className="text-link" onClick={() => onCreateMissing(slug)}>[[{slug}]]</button>
              ))}
            </span>
          </div>
        )}

        {(page.children.length > 0 || page.backlinks.length > 0) && (
          <div className="panel-grid wiki-relations">
            {page.children.length > 0 && (
              <div className="panel">
                <h4 className="panel-title"><Icon name="folder" size={15} /> 子页面</h4>
                <ul className="link-list">
                  {page.children.map((c) => (
                    <li key={c.slug}><button type="button" onClick={() => onOpen(c.slug)}>{c.title}</button><small>{c.summary}</small></li>
                  ))}
                </ul>
              </div>
            )}
            {page.backlinks.length > 0 && (
              <div className="panel">
                <h4 className="panel-title"><Icon name="link" size={15} /> 反向链接</h4>
                <ul className="link-list">
                  {page.backlinks.map((b) => <li key={b.slug}><button type="button" onClick={() => onOpen(b.slug)}>{b.title}</button></li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </article>

      {toc.length > 1 && (
        <aside className="wiki-toc">
          <div className="side-label">本页目录</div>
          {toc.map((item) => (
            <a key={item.id} href={`#${item.id}`} className={`toc-${item.depth}`} onClick={(e) => {
              e.preventDefault();
              document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}>
              {item.text}
            </a>
          ))}
          <div className="toc-meta">{page.chunk_count} 个检索切块</div>
        </aside>
      )}
    </div>
  );
}

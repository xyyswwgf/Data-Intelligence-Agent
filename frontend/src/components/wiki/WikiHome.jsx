import { useEffect, useState } from "react";
import Icon from "../Icon";
import { getWikiIndex, getWikiTag } from "../../api";
import { extractErrorMessage } from "../../utils/format";

const STATUS_LABEL = { processing: "处理中", ready: "已入库", failed: "失败" };

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
}

function formatSize(bytes) {
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** 知识库索引首页：统计、标签索引、最近更新、待完善事项、文档入库记录 */
export default function WikiHome({ onOpen, refreshKey }) {
  const [index, setIndex] = useState(null);
  const [error, setError] = useState(null);
  const [tag, setTag] = useState(null);
  const [tagPages, setTagPages] = useState([]);

  useEffect(() => {
    getWikiIndex()
      .then((data) => {
        setIndex(data);
        setError(null);
      })
      .catch((err) => setError(extractErrorMessage(err)));
  }, [refreshKey]);

  useEffect(() => {
    if (!tag) return;
    getWikiTag(tag).then((data) => setTagPages(data.items)).catch(() => setTagPages([]));
  }, [tag]);

  if (error) return <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>;
  if (!index) return <div className="wiki-loading"><span className="spinner" /></div>;
  const { stats } = index;
  const coverage = stats.chunks ? Math.round((stats.embedded_chunks / stats.chunks) * 100) : 0;

  return (
    <div className="wiki-home">
      <header className="wiki-home-head">
        <h1>知识库索引</h1>
        <p>所有上传的文档都会转换为 Markdown 页面，自动切块索引，供搜索和知识问答引用。</p>
      </header>

      <div className="metric-grid">
        <div className="metric-card"><span className="metric-name">页面</span><strong>{stats.pages}</strong></div>
        <div className="metric-card"><span className="metric-name">原始文档</span><strong>{stats.documents}</strong></div>
        <div className="metric-card"><span className="metric-name">检索切块</span><strong>{stats.chunks}</strong></div>
        <div className="metric-card">
          <span className="metric-name">向量覆盖率</span>
          <strong>{coverage}%</strong>
          <span className={`delta ${coverage === 100 ? "up" : "down"}`}>{stats.embedded_chunks}/{stats.chunks}</span>
        </div>
      </div>
      {(stats.indexing > 0 || stats.index_issues > 0) && (
        <div className="callout info">
          <Icon name="refresh" size={16} />
          <span>
            {stats.indexing > 0 && `${stats.indexing} 个页面正在索引。`}
            {stats.index_issues > 0 && `${stats.index_issues} 个页面向量化未完成（已降级为关键词检索），管理员可在「系统管理」中重建索引。`}
          </span>
        </div>
      )}

      <div className="panel-grid">
        <div className="panel">
          <h4 className="panel-title"><Icon name="history" size={15} /> 最近更新</h4>
          <ul className="link-list">
            {index.recent.map((p) => (
              <li key={p.slug}>
                <button type="button" onClick={() => onOpen(p.slug)}>{p.title}</button>
                <small>{p.updated_by_name || "系统"} · {formatTime(p.updated_at)} · v{p.version}</small>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <h4 className="panel-title"><Icon name="tag" size={15} /> 标签索引</h4>
          <div className="tag-cloud">
            {index.tags.length === 0 && <span className="side-empty">暂无标签</span>}
            {index.tags.map((t) => (
              <button type="button" key={t.tag} className={`tag-chip ${tag === t.tag ? "active" : ""}`} onClick={() => setTag(tag === t.tag ? null : t.tag)}>
                #{t.tag} <small>{t.count}</small>
              </button>
            ))}
          </div>
          {tag && (
            <ul className="link-list compact">
              {tagPages.map((p) => (
                <li key={p.slug}><button type="button" onClick={() => onOpen(p.slug)}>{p.title}</button></li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {(index.orphans.length > 0 || index.dangling_links.length > 0) && (
        <div className="panel">
          <h4 className="panel-title"><Icon name="link" size={15} /> 待整理</h4>
          <div className="todo-grid">
            {index.orphans.length > 0 && (
              <div>
                <p className="todo-title">未归类页面（无父页面、无入链）</p>
                <div className="tag-cloud">
                  {index.orphans.map((p) => <button type="button" key={p.slug} className="tag-chip" onClick={() => onOpen(p.slug)}>{p.title}</button>)}
                </div>
              </div>
            )}
            {index.dangling_links.length > 0 && (
              <div>
                <p className="todo-title">缺失页面（被引用但尚未创建）</p>
                <div className="tag-cloud">
                  {index.dangling_links.map((l) => <span key={l.slug} className="tag-chip missing">[[{l.slug}]] <small>×{l.referenced_by}</small></span>)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="panel">
        <h4 className="panel-title"><Icon name="upload" size={15} /> 文档入库记录</h4>
        {index.documents.length === 0 ? (
          <p className="side-empty">还没有上传文档。支持 Markdown、TXT、PDF、Word(.docx)。</p>
        ) : (
          <table className="mini-table">
            <thead><tr><th>文件</th><th>类型</th><th>大小</th><th>状态</th><th>上传</th></tr></thead>
            <tbody>
              {index.documents.map((d) => (
                <tr key={d.id}>
                  <td>{d.page_slug ? <button type="button" className="text-link" onClick={() => onOpen(d.page_slug)}>{d.filename}</button> : d.filename}</td>
                  <td>{d.file_type.toUpperCase()}</td>
                  <td>{formatSize(d.size_bytes)}</td>
                  <td><span className={`status-pill ${d.status}`} title={d.error || ""}>{STATUS_LABEL[d.status]}</span></td>
                  <td>{d.uploaded_by_name || "—"} · {formatTime(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

import { useMemo, useState } from "react";
import Icon from "../Icon";
import { renderMarkdown } from "../../utils/markdown";

/**
 * Wiki 编辑器：左侧 Markdown、右侧实时预览
 * 新建与编辑共用；保存冲突（409）由父组件提示后保留当前草稿
 */
export default function WikiEditor({ initial, pages, existingSlugs, saving, error, onSave, onCancel }) {
  const [title, setTitle] = useState(initial.title || "");
  const [content, setContent] = useState(initial.content_md || "");
  const [tags, setTags] = useState((initial.tags || []).join(", "));
  const [parentId, setParentId] = useState(initial.parent_id ?? "");
  const [note, setNote] = useState("");
  const [tab, setTab] = useState("split");
  const preview = useMemo(() => renderMarkdown(content, { existingSlugs }).html, [content, existingSlugs]);

  // 不能把页面挂到自己下面（深层环路由后端校验）
  const parentOptions = pages.filter((p) => p.id !== initial.id);

  const submit = (e) => {
    e.preventDefault();
    onSave({
      title: title.trim(),
      content_md: content,
      tags: tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
      parent_id: parentId === "" ? null : Number(parentId),
      change_note: note.trim() || null,
    });
  };

  const dirty = title !== (initial.title || "") || content !== (initial.content_md || "");

  return (
    <form className="wiki-editor" onSubmit={submit}>
      <div className="editor-head">
        <input className="editor-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="页面标题" maxLength={300} autoFocus={!initial.id} />
        <div className="segmented">
          {[["edit", "编辑"], ["split", "分栏"], ["preview", "预览"]].map(([key, label]) => (
            <button type="button" key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="editor-meta">
        <label>
          <Icon name="folder" size={14} />
          <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">（根目录）</option>
            {parentOptions.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
        </label>
        <label className="grow">
          <Icon name="tag" size={14} />
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="标签，用逗号分隔" />
        </label>
      </div>
      <div className={`editor-body ${tab}`}>
        {tab !== "preview" && (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={"# 标题\n\n使用 Markdown 编写。用 [[页面标题]] 链接到其他页面。"}
            spellCheck={false}
          />
        )}
        {tab !== "edit" && <div className="markdown-body editor-preview" dangerouslySetInnerHTML={{ __html: preview }} />}
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="editor-foot">
        {initial.id && <input className="editor-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="修改说明（可选），例如：更新 2027 年差旅标准" maxLength={300} />}
        <span className="editor-hint">支持 Markdown、表格、代码块；[[页面]] 自动生成双向链接</span>
        <button type="button" className="btn-secondary" onClick={() => (!dirty || window.confirm("放弃未保存的修改？")) && onCancel()} disabled={saving}>取消</button>
        <button type="submit" className="btn-primary" disabled={saving || !title.trim()}>{saving ? "保存中…" : initial.id ? "保存新版本" : "创建页面"}</button>
      </div>
    </form>
  );
}

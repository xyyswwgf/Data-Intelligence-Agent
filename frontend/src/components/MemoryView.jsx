import { useEffect, useState } from "react";
import Icon from "./Icon";
import { addMemory, clearMemories, deleteMemory, listMemories, setMemoryEnabled, updateMemory } from "../api";
import { extractErrorMessage } from "../utils/format";

const CATEGORY = {
  preference: { label: "偏好", hint: "表达方式、报告格式、关注的口径" },
  fact: { label: "事实", hint: "部门、负责的区域或业务线" },
  goal: { label: "目标", hint: "持续推进的目标或项目" },
  instruction: { label: "指令", hint: "要求助手以后一直遵守的做法" },
};
const SOURCE = { auto: "对话中自动提炼", explicit: "你让助手记住", manual: "手动添加" };

/** 「我的记忆」：用户对助手记住的内容完全可见、可控 */
export default function MemoryView() {
  const [data, setData] = useState({ enabled: true, items: [] });
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState({ content: "", category: "preference", pinned: false });
  const [editing, setEditing] = useState(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    listMemories().then(setData).catch((err) => setError(extractErrorMessage(err)));
  }, [version]);

  const reload = () => setVersion((v) => v + 1);
  const run = async (fn) => {
    setError(null);
    try {
      await fn();
      reload();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  const pinned = data.items.filter((m) => m.pinned);
  const others = data.items.filter((m) => !m.pinned);

  const renderItem = (m) => (
    <li key={m.id} className={`memory-item ${m.pinned ? "pinned" : ""}`}>
      <span className={`memory-cat cat-${m.category}`}>{CATEGORY[m.category]?.label || m.category}</span>
      {editing?.id === m.id ? (
        <form className="memory-edit" onSubmit={(e) => { e.preventDefault(); run(() => updateMemory(m.id, { content: editing.content })).then(() => setEditing(null)); }}>
          <input value={editing.content} onChange={(e) => setEditing({ ...editing, content: e.target.value })} autoFocus maxLength={500} />
          <button type="submit" className="btn-primary btn-sm">保存</button>
          <button type="button" className="btn-secondary btn-sm" onClick={() => setEditing(null)}>取消</button>
        </form>
      ) : (
        <div className="memory-body">
          <p>{m.content}</p>
          <small>
            {SOURCE[m.source]} · 使用 {m.use_count} 次 · 更新于 {new Date(m.updated_at).toLocaleString("zh-CN")}
          </small>
        </div>
      )}
      <div className="memory-actions">
        <button type="button" className={`icon-btn ${m.pinned ? "active" : ""}`} title={m.pinned ? "取消置顶" : "置顶为核心记忆（每次对话都会带上）"}
          onClick={() => run(() => updateMemory(m.id, { pinned: !m.pinned }))}>
          <Icon name="target" size={15} />
        </button>
        <button type="button" className="icon-btn" title="编辑" onClick={() => setEditing({ id: m.id, content: m.content })}>
          <Icon name="edit" size={15} />
        </button>
        <button type="button" className="icon-btn danger" title="删除" onClick={() => run(() => deleteMemory(m.id))}>
          <Icon name="trash" size={15} />
        </button>
      </div>
    </li>
  );

  return (
    <div className="admin-view memory-view">
      <header className="admin-head">
        <div>
          <h1><Icon name="bulb" size={22} /> 我的记忆</h1>
          <p className="muted">助手会记住你的偏好和背景，在之后的对话里自动参考。这些内容只有你自己可见。</p>
        </div>
        <label className="switch">
          <input type="checkbox" checked={data.enabled} onChange={(e) => run(() => setMemoryEnabled(e.target.checked))} />
          <span className="switch-track" />
          <span>{data.enabled ? "记忆已开启" : "记忆已关闭"}</span>
        </label>
      </header>

      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}

      <div className="panel-grid memory-layers">
        <div className="panel">
          <h4 className="panel-title"><Icon name="activity" size={15} /> 记忆是怎么工作的</h4>
          <ol className="layer-list">
            <li><strong>工作记忆</strong>：当前对话最近几轮的原文</li>
            <li><strong>会话摘要</strong>：对话变长后，较早的内容自动压缩成要点</li>
            <li><strong>核心记忆</strong>：置顶的条目，每次对话都会带上</li>
            <li><strong>相关记忆</strong>：按问题语义召回的其他条目</li>
            <li><strong>企业知识库</strong>：所有成员共享的制度与口径</li>
          </ol>
        </div>
        <form className="panel" onSubmit={(e) => { e.preventDefault(); run(() => addMemory(draft)).then(() => setDraft({ ...draft, content: "" })); }}>
          <h4 className="panel-title"><Icon name="plus" size={15} /> 手动添加</h4>
          <textarea className="memory-input" rows={3} value={draft.content} maxLength={500}
            onChange={(e) => setDraft({ ...draft, content: e.target.value })}
            placeholder="例如：我负责华北区域的销售分析；汇报时金额以万元为单位" />
          <div className="btn-row">
            <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} title={CATEGORY[draft.category].hint}>
              {Object.entries(CATEGORY).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
            <label className="check"><input type="checkbox" checked={draft.pinned} onChange={(e) => setDraft({ ...draft, pinned: e.target.checked })} /> 置顶</label>
            <button type="submit" className="btn-primary btn-sm" disabled={draft.content.trim().length < 2}>添加</button>
          </div>
        </form>
      </div>

      <div className="panel">
        <h4 className="panel-title"><Icon name="target" size={15} /> 核心记忆 <span className="count">{pinned.length}</span></h4>
        {pinned.length ? <ul className="memory-list">{pinned.map(renderItem)}</ul> : <p className="side-empty">把最重要的记忆置顶，每次对话都会参考。</p>}
      </div>
      <div className="panel">
        <div className="panel-title-row">
          <h4 className="panel-title"><Icon name="bulb" size={15} /> 其他记忆 <span className="count">{others.length}</span></h4>
          {data.items.length > 0 && (
            <button type="button" className="text-link danger-link" onClick={() => window.confirm("清空全部记忆？此操作不可撤销。") && run(clearMemories)}>
              清空全部
            </button>
          )}
        </div>
        {others.length ? <ul className="memory-list">{others.map(renderItem)}</ul> : <p className="side-empty">还没有记忆。和助手对话时说「记住：…」，或开启记忆后让它自动提炼。</p>}
      </div>
    </div>
  );
}

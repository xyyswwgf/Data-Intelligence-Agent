import { useEffect, useMemo, useState } from "react";
import Icon from "../Icon";
import { adminApi } from "../../api";
import { extractErrorMessage } from "../../utils/format";
import { renderMarkdown } from "../../utils/markdown";

function SkillForm({ initial, tools, onSaved, onCancel }) {
  const editing = Boolean(initial?.id);
  const [form, setForm] = useState({
    name: initial?.name || "", title: initial?.title || "", description: initial?.description || "",
    instructions: initial?.instructions || "# 技能名称\n\n1. 第一步……\n2. 第二步……\n\n输出格式：……",
    tools: initial?.tools || [], enabled: initial?.enabled ?? true,
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const preview = useMemo(() => renderMarkdown(form.instructions).html, [form.instructions]);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  const toggleTool = (name) =>
    setForm({ ...form, tools: form.tools.includes(name) ? form.tools.filter((t) => t !== name) : [...form.tools, name] });

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { title: form.title, description: form.description, instructions: form.instructions, tools: form.tools, enabled: form.enabled };
      const saved = editing ? await adminApi.updateSkill(initial.id, body) : await adminApi.createSkill({ ...body, name: form.name });
      onSaved(saved);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel skill-form" onSubmit={submit}>
      <h4 className="panel-title"><Icon name={editing ? "edit" : "plus"} size={15} /> {editing ? `编辑技能：${initial.title}` : "新建技能"}</h4>
      <div className="form-grid">
        <label>技能标识
          <input value={form.name} onChange={set("name")} disabled={editing} required pattern="[a-z][a-z0-9\-]{2,40}" placeholder="weekly-report" title="小写字母开头，可含数字和连字符" />
        </label>
        <label className="span-2">名称<input value={form.title} onChange={set("title")} required maxLength={100} placeholder="经营周报" /></label>
        <label className="span-3">适用场景（模型据此判断何时加载，写清「用户说什么 / 要做什么时使用」）
          <input value={form.description} onChange={set("description")} required minLength={5} maxLength={500} placeholder="用户要写周报、月报或经营汇报材料时使用。" />
        </label>
      </div>
      <div className="form-block">
        <span className="form-label">推荐使用的工具</span>
        <div className="tag-cloud">
          {tools.map((t) => (
            <button type="button" key={t.name} className={`tag-chip ${form.tools.includes(t.name) ? "active" : ""}`} onClick={() => toggleTool(t.name)}>
              {t.display_name}
            </button>
          ))}
        </div>
      </div>
      <div className="editor-body split">
        <textarea value={form.instructions} onChange={set("instructions")} spellCheck={false} required minLength={10} />
        <div className="markdown-body editor-preview" dangerouslySetInnerHTML={{ __html: preview }} />
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm" disabled={busy}>{busy ? "保存中…" : "保存"}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>取消</button>
      </div>
    </form>
  );
}

export default function SkillsPanel() {
  const [items, setItems] = useState([]);
  const [tools, setTools] = useState([]);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    adminApi.listSkills().then((d) => setItems(d.items)).catch((err) => setError(extractErrorMessage(err)));
    adminApi.listTools().then((d) => setTools(d.items.filter((t) => t.enabled && t.name !== "use_skill"))).catch(() => {});
  }, [version]);
  const reload = () => setVersion((v) => v + 1);
  const act = async (fn) => {
    setError(null);
    try {
      await fn();
      reload();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };
  const toolName = (name) => tools.find((t) => t.name === name)?.display_name || name;

  return (
    <div className="admin-section">
      <div className="callout info">
        <Icon name="bulb" size={16} />
        <span>
          技能是可复用的「做事方法」。对话时助手只看到每个技能的名称和适用场景，判断需要时才加载完整指引（不占用多余上下文）；
          用户也可以在输入框的「技能」菜单中手动指定。
        </span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {editing ? (
        <SkillForm initial={editing === "new" ? null : editing} tools={tools} onSaved={() => { setEditing(null); reload(); }} onCancel={() => setEditing(null)} />
      ) : (
        <button type="button" className="btn-primary btn-sm self-start" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> 新建技能</button>
      )}
      <div className="skill-cards">
        {items.map((s) => (
          <div key={s.id} className={`panel skill-card ${s.enabled ? "" : "disabled"}`}>
            <div className="model-card-head">
              <Icon name="bulb" size={16} />
              <strong>{s.title}</strong>
              <code>{s.name}</code>
              {!s.enabled && <span className="tag warn">已停用</span>}
            </div>
            <p className="skill-desc">{s.description}</p>
            {s.tools.length > 0 && (
              <div className="tag-cloud">{s.tools.map((t) => <span key={t} className="tag-chip static">{toolName(t)}</span>)}</div>
            )}
            <div className="btn-row">
              <button type="button" className="btn-secondary btn-sm" onClick={() => setEditing(s)}>编辑</button>
              <button type="button" className="btn-secondary btn-sm" onClick={() => act(() => adminApi.updateSkill(s.id, { enabled: !s.enabled }))}>
                {s.enabled ? "停用" : "启用"}
              </button>
              <button type="button" className="text-link danger-link" onClick={() => window.confirm(`删除技能「${s.title}」？`) && act(() => adminApi.deleteSkill(s.id))}>删除</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

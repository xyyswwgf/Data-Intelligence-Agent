import { useRef, useState } from "react";
import Icon from "./Icon";

/**
 * 输入框（Composer）
 *
 * - Enter 发送，Shift+Enter 换行，输入框随内容自动增高
 * - 底部工具栏：模式切换（智能助手 / 经营分析 / 知识问答 / SQL 查询）、当前分析的表、打开数据面板
 */
function Picker({ icon, label, title, open, onToggle, children }) {
  return (
    <div className="picker">
      <button type="button" className={`picker-btn ${open ? "open" : ""}`} onClick={onToggle} title={title}>
        <Icon name={icon} size={14} />
        <span className="picker-label">{label}</span>
        <Icon name="chevron" size={12} className="picker-chevron" />
      </button>
      {open && <div className="picker-menu">{children}</div>}
    </div>
  );
}

export default function Composer({
  onSubmit, loading, mode, onModeChange, targetTable, onClearTable, onOpenData, autoFocus,
  models = [], modelId, onModelChange, skills = [], skill, onSkillChange,
}) {
  const [value, setValue] = useState("");
  const [menu, setMenu] = useState(null);
  const currentModel = models.find((m) => m.id === modelId) || models.find((m) => m.is_default) || models[0];
  const currentSkill = skills.find((s) => s.name === skill);
  const toggleMenu = (name) => setMenu((m) => (m === name ? null : name));
  const textareaRef = useRef(null);

  const resize = (el) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  const submit = () => {
    const question = value.trim();
    if (!question || loading) return;
    onSubmit(question);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const placeholder = mode === "assistant"
    ? targetTable ? `问问关于 ${targetTable} 的任何问题` : "问任何业务问题，我会自己查资料、查数据、推理后回答"
    : mode === "kb"
    ? "向企业知识库提问，例如：GMV 的统计口径是什么？"
    : targetTable && mode === "agent"
    ? `针对 ${targetTable} 提问，例如：这个表按月份有什么变化？`
    : mode === "agent"
      ? "描述你的业务问题，例如：本月销售额为什么下降？"
      : "用自然语言查数据，例如：每个品类的销售额是多少？";

  return (
    <div className={`composer ${loading ? "is-busy" : ""}`}>
      <textarea
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          setValue(e.target.value);
          resize(e.target);
        }}
        onKeyDown={handleKeyDown}
        disabled={loading}
        autoFocus={autoFocus}
        aria-label="输入问题"
      />
      <div className="composer-bar">
        <div className="composer-tools">
          <button type="button" className="tool-btn" onClick={onOpenData} title="导入 CSV / 查看数据源">
            <Icon name="plus" size={16} />
          </button>
          <div className="segmented" role="tablist" aria-label="模式">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "assistant"}
              className={mode === "assistant" ? "active" : ""}
              onClick={() => onModeChange("assistant")}
              disabled={loading}
            >
              <Icon name="sparkles" size={14} /> 智能助手
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "agent"}
              className={mode === "agent" ? "active" : ""}
              onClick={() => onModeChange("agent")}
              disabled={loading}
            >
              <Icon name="trend" size={14} /> 经营分析
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "kb"}
              className={mode === "kb" ? "active" : ""}
              onClick={() => onModeChange("kb")}
              disabled={loading}
            >
              <Icon name="book" size={14} /> 知识问答
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "query"}
              className={mode === "query" ? "active" : ""}
              onClick={() => onModeChange("query")}
              disabled={loading}
            >
              <Icon name="code" size={14} /> SQL 查询
            </button>
          </div>
          {targetTable && (mode === "agent" || mode === "assistant") && (
            <span className="table-chip" title={targetTable}>
              <Icon name="table" size={13} />
              <span className="table-chip-name">{targetTable}</span>
              <button type="button" onClick={onClearTable} aria-label="取消选择表">
                <Icon name="x" size={12} />
              </button>
            </span>
          )}
        </div>
        <div className="composer-right">
          {models.length > 0 && (
            <Picker
              icon={currentModel?.kind === "local" ? "database" : "sparkles"}
              label={currentModel ? currentModel.name : "选择模型"}
              title="切换本轮使用的模型"
              open={menu === "model"}
              onToggle={() => toggleMenu("model")}
            >
              <div className="picker-title">选择模型</div>
              {models.map((m) => (
                <button type="button" key={m.id ?? "env"} className={`picker-item ${m === currentModel ? "active" : ""}`}
                  onClick={() => { onModelChange(m.id); setMenu(null); }}>
                  <span className={`kind-badge ${m.kind}`}>{m.kind === "local" ? "本地" : "云端"}</span>
                  <span className="picker-item-main">
                    <strong>{m.name}{m.is_default && <em>默认</em>}</strong>
                    <small>{m.model}{m.supports_tools === false && " · 不支持工具调用"}</small>
                  </span>
                  {m === currentModel && <Icon name="check" size={14} />}
                </button>
              ))}
            </Picker>
          )}
          {mode === "assistant" && skills.length > 0 && (
            <Picker
              icon="bulb"
              label={currentSkill ? currentSkill.title : "技能"}
              title="为这次提问指定技能（不选则由助手自动判断）"
              open={menu === "skill"}
              onToggle={() => toggleMenu("skill")}
            >
              <div className="picker-title">技能</div>
              <button type="button" className={`picker-item ${!skill ? "active" : ""}`} onClick={() => { onSkillChange(null); setMenu(null); }}>
                <span className="picker-item-main"><strong>自动</strong><small>由助手根据问题判断是否使用技能</small></span>
                {!skill && <Icon name="check" size={14} />}
              </button>
              {skills.map((s) => (
                <button type="button" key={s.name} className={`picker-item ${s.name === skill ? "active" : ""}`}
                  onClick={() => { onSkillChange(s.name); setMenu(null); }}>
                  <span className="picker-item-main"><strong>{s.title}</strong><small>{s.description}</small></span>
                  {s.name === skill && <Icon name="check" size={14} />}
                </button>
              ))}
            </Picker>
          )}
        <button type="button" className="send-btn" onClick={submit} disabled={loading || !value.trim()} aria-label="发送">
          {loading ? <span className="send-spinner" /> : <Icon name="send" size={18} strokeWidth={2.2} />}
        </button>
        </div>
      </div>
    </div>
  );
}

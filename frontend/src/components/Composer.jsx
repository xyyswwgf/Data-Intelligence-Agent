import { useRef, useState } from "react";
import Icon from "./Icon";

/**
 * 输入框（Composer）
 *
 * - Enter 发送，Shift+Enter 换行，输入框随内容自动增高
 * - 底部工具栏：模式切换（Agent 分析 / SQL 查询）、当前分析的表、打开数据面板
 */
export default function Composer({ onSubmit, loading, mode, onModeChange, targetTable, onClearTable, onOpenData, autoFocus }) {
  const [value, setValue] = useState("");
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

  const placeholder = targetTable
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
              aria-selected={mode === "agent"}
              className={mode === "agent" ? "active" : ""}
              onClick={() => onModeChange("agent")}
              disabled={loading}
            >
              <Icon name="sparkles" size={14} /> Agent 分析
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
          {targetTable && mode === "agent" && (
            <span className="table-chip" title={targetTable}>
              <Icon name="table" size={13} />
              <span className="table-chip-name">{targetTable}</span>
              <button type="button" onClick={onClearTable} aria-label="取消选择表">
                <Icon name="x" size={12} />
              </button>
            </span>
          )}
        </div>
        <button type="button" className="send-btn" onClick={submit} disabled={loading || !value.trim()} aria-label="发送">
          {loading ? <span className="send-spinner" /> : <Icon name="send" size={18} strokeWidth={2.2} />}
        </button>
      </div>
    </div>
  );
}

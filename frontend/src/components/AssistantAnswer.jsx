import { useMemo, useState } from "react";
import Icon from "./Icon";
import ResultChart from "./ResultChart";
import ResultTable from "./ResultTable";
import { AgentAnswer } from "./AgentAnswer";
import { detectChartConfig } from "../utils/chartDetector";
import { renderMarkdown } from "../utils/markdown";

const TOOL_META = {
  search_knowledge: { label: "检索知识库", icon: "book" },
  list_tables: { label: "查看数据表", icon: "database" },
  describe_tables: { label: "读取表结构", icon: "table" },
  run_sql: { label: "执行 SQL", icon: "code" },
  business_analysis: { label: "经营归因分析", icon: "trend" },
  analyze_imported_table: { label: "分析导入表", icon: "chart" },
  remember: { label: "写入记忆", icon: "bulb" },
  use_skill: { label: "加载技能", icon: "bulb" },
  python_analysis: { label: "Python 分析", icon: "code" },
};

const PART_LABEL = {
  system: "系统指令",
  tools: "工具定义",
  core_memory: "核心记忆",
  recalled_memory: "相关记忆",
  summary: "会话摘要",
  history: "对话历史",
  question: "当前问题",
};

function formatK(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** 上下文占用条：各部分占模型上下文窗口的比例 */
function ContextBar({ ctx }) {
  const parts = Object.entries(ctx.parts || {}).filter(([, v]) => v > 0);
  const pct = Math.min(100, (ctx.used / ctx.window) * 100);
  return (
    <div className="ctx-bar">
      <div className="ctx-head">
        <span><Icon name="activity" size={13} /> 上下文 {formatK(ctx.used)} / {formatK(ctx.window)} tokens</span>
        {ctx.dropped_messages > 0 && <span className="muted">较早的 {ctx.dropped_messages} 条消息未放入（已在摘要中）</span>}
      </div>
      <div className="ctx-track" title={parts.map(([k, v]) => `${PART_LABEL[k] || k}: ${v}`).join("\n")}>
        {parts.map(([key, value]) => (
          <span key={key} className={`ctx-seg seg-${key}`} style={{ width: `${(value / ctx.window) * 100}%` }} />
        ))}
        <span className="ctx-free" style={{ width: `${100 - pct}%` }} />
      </div>
      <div className="ctx-legend">
        {parts.map(([key, value]) => (
          <span key={key}><i className={`seg-${key}`} /> {PART_LABEL[key] || key} {formatK(value)}</span>
        ))}
      </div>
      {ctx.memories?.length > 0 && (
        <div className="ctx-memories">
          <Icon name="bulb" size={13} /> 已调用记忆：
          {ctx.memories.map((m) => <span key={m.id} className={`memory-chip ${m.pinned ? "pinned" : ""}`}>{m.content}</span>)}
        </div>
      )}
    </div>
  );
}

function chartConfigFrom(display) {
  const spec = display.chart;
  if (spec && spec.x && display.columns?.includes(spec.x)) {
    const y = (Array.isArray(spec.y) ? spec.y : [spec.y]).filter((c) => display.columns.includes(c));
    if (y.length) return { chartType: spec.type === "line" ? "line" : "bar", xAxis: spec.x, yAxis: y };
  }
  return detectChartConfig(display.columns, display.data);
}

function PythonDisplay({ display }) {
  return (
    <div className="tool-display">
      <details className="code-details">
        <summary><Icon name="code" size={13} /> 代码与取数 SQL（输入 {display.input_rows ?? "—"} 行{display.input_truncated ? "，已截断" : ""}）</summary>
        <pre className="sql-code">{display.sql}</pre>
        <pre className="sql-code python-code">{display.code}</pre>
      </details>
      {display.problems && <div className="callout danger"><Icon name="alert" size={14} /><span>{display.problems.join("；")}</span></div>}
      {display.error && <div className="callout danger"><Icon name="alert" size={14} /><span>{display.error}</span></div>}
      {display.stdout && <pre className="stdout-block">{display.stdout}</pre>}
      {display.columns && (
        <>
          <ResultChart columns={display.columns} data={display.data} chartConfig={chartConfigFrom(display)} />
          <ResultTable columns={display.columns} data={display.data} truncated={display.truncated} />
        </>
      )}
      {display.value !== undefined && <pre className="stdout-block">{JSON.stringify(display.value, null, 2)}</pre>}
    </div>
  );
}

function ToolDisplay({ display }) {
  if (!display) return null;
  if (display.python) return <PythonDisplay display={display} />;
  if (display.columns) {
    const chartConfig = detectChartConfig(display.columns, display.data);
    return (
      <div className="tool-display">
        <ResultChart columns={display.columns} data={display.data} chartConfig={chartConfig} />
        <ResultTable columns={display.columns} data={display.data} sql={display.sql} truncated={display.truncated} />
      </div>
    );
  }
  if (display.analysis) {
    return <div className="tool-display"><AgentAnswer result={display.analysis} /></div>;
  }
  if (display.citations?.length) {
    return (
      <ul className="tool-citations">
        {display.citations.map((c) => (
          <li key={c.chunk_id}><strong>{c.title}</strong> {c.snippet}</li>
        ))}
      </ul>
    );
  }
  if (display.http) {
    return (
      <div className="tool-display">
        <p className="muted">{display.http.url} · HTTP {display.http.status}</p>
        <pre className="sql-code">{JSON.stringify(display.http.json ?? display.http.text, null, 2)}</pre>
      </div>
    );
  }
  if (display.sql) return <pre className="sql-code">{display.sql}</pre>;
  if (display.memory) return <p className="muted">{display.memory.content}</p>;
  if (display.tables) {
    return <p className="muted">{Object.entries(display.tables).map(([t, n]) => `${t}(${n})`).join("、")}</p>;
  }
  return null;
}

function ToolStep({ call, result }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL_META[call.name] || { label: call.name, icon: "code" };
  const status = !result ? "running" : result.ok ? "ok" : "bad";
  const argText = ["run_sql", "python_analysis"].includes(call.name)
    ? call.args.purpose || call.args.sql
    : Object.values(call.args || {}).join(" · ");
  const expandable = result && (result.display?.columns || result.display?.analysis || result.display?.citations?.length
    || result.display?.sql || result.display?.http || result.display?.python);
  return (
    <li className={`trace-tool ${status}`}>
      <button type="button" className="trace-tool-head" onClick={() => expandable && setOpen(!open)} disabled={!expandable}>
        <span className="trace-icon"><Icon name={meta.icon} size={13} /></span>
        <strong>{meta.label}</strong>
        <span className="trace-args">{argText}</span>
        <span className="trace-status">
          {status === "running" ? <span className="send-spinner dark" /> : result.summary}
          {result?.elapsed_ms != null && <small> · {result.elapsed_ms}ms</small>}
        </span>
        {expandable && <Icon name="chevron" size={13} className={`trace-chevron ${open ? "open" : ""}`} />}
      </button>
      {call.name === "run_sql" && call.args.sql && open && <pre className="sql-code trace-sql">{call.args.sql}</pre>}
      {call.name === "python_analysis" && !result && <pre className="sql-code trace-sql python-code">{call.args.code}</pre>}
      {open && <ToolDisplay display={result?.display} />}
    </li>
  );
}

/** 推理轨迹：思考 → 工具调用 → 观察，按事件顺序展示 */
export function ReasoningTrace({ events, live, draft }) {
  const [collapsed, setCollapsed] = useState(false);
  const items = useMemo(() => {
    const results = new Map(events.filter((e) => e.type === "tool_result").map((e) => [e.id, e]));
    return events
      .filter((e) => ["thinking", "tool_call", "memory", "error", "skill"].includes(e.type))
      .map((e) => (e.type === "tool_call" ? { ...e, result: results.get(e.id) } : e));
  }, [events]);
  const ctx = events.find((e) => e.type === "context");
  const steps = items.filter((e) => e.type === "tool_call").length;
  if (!items.length && !ctx && !live) return null;

  return (
    <div className={`thinking reasoning ${live ? "is-live" : ""}`}>
      <button type="button" className="thinking-head" onClick={() => setCollapsed(!collapsed)}>
        {live ? <span className="pulse-dot" /> : <Icon name="check" size={14} strokeWidth={2.4} />}
        <span>{live ? "正在推理" : `推理完成 · ${steps} 次工具调用`}</span>
        <Icon name="chevron" size={14} className={`thinking-chevron ${collapsed ? "" : "open"}`} />
      </button>
      {!collapsed && (
        <div className="reasoning-body">
          {ctx && <ContextBar ctx={ctx} />}
          <ol className="trace-list">
            {items.map((item, i) => {
              if (item.type === "thinking") {
                return (
                  <li key={i} className={`trace-thought ${item.kind === "plan" ? "plan" : ""}`}>
                    <span className="trace-icon"><Icon name={item.kind === "plan" ? "target" : "sparkles"} size={13} /></span>
                    <p>{item.text}</p>
                  </li>
                );
              }
              if (item.type === "memory") {
                return (
                  <li key={i} className="trace-thought memory">
                    <span className="trace-icon"><Icon name="bulb" size={13} /></span>
                    <p>{item.action === "updated" ? "更新记忆" : "记住了"}：{item.memory.content}</p>
                  </li>
                );
              }
              if (item.type === "skill") {
                return (
                  <li key={i} className="trace-thought memory">
                    <span className="trace-icon"><Icon name="bulb" size={13} /></span>
                    <p>按你指定的技能「{item.title}」执行</p>
                  </li>
                );
              }
              if (item.type === "error") {
                return <li key={i} className="trace-thought error"><span className="trace-icon"><Icon name="alert" size={13} /></span><p>{item.message}</p></li>;
              }
              return <ToolStep key={item.id || i} call={item} result={item.result} />;
            })}
            {live && draft?.reasoning && (
              <li className="trace-thought streaming">
                <span className="trace-icon"><Icon name="sparkles" size={13} /></span>
                <p>{draft.reasoning.slice(-1200)}</p>
              </li>
            )}
            {live && !draft?.reasoning && (
              <li className="trace-thought waiting">
                <span className="trace-icon"><span className="send-spinner dark" /></span>
                <p>{draft?.answer ? "正在撰写回答…" : items.length ? "继续推理中…" : "正在组装上下文、思考需要哪些信息…"}</p>
              </li>
            )}
          </ol>
        </div>
      )}
    </div>
  );
}

/** 最终回答：Markdown + [[页面]] 引用 + 来源卡片 + 用量 */
export function AssistantFinal({ content, citations, usage, onOpenPage, streaming = false }) {
  const html = useMemo(() => renderMarkdown(content).html, [content]);
  const onClick = (e) => {
    const link = e.target.closest("a[data-wiki]");
    if (link) {
      e.preventDefault();
      onOpenPage(link.dataset.wiki);
    }
  };
  const cited = (citations || []).filter((c) => c.cited !== false);
  return (
    <div className="answer">
      <div className={`summary kb-answer ${streaming ? "is-streaming" : ""}`}>
        <div className="markdown-body" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
        {usage && (usage.steps != null || usage.input_tokens) && (
          <div className="summary-meta">
            {usage.mode === "rule" ? (
              <span className="tag warn">未配置大模型 · 规则路由</span>
            ) : (
              <>
                <span className="tag">{usage.steps} 步推理</span>
                {usage.input_tokens > 0 && <span className="tag">{formatK(usage.input_tokens)} 输入 / {formatK(usage.output_tokens)} 输出 tokens</span>}
              </>
            )}
          </div>
        )}
      </div>
      {cited.length > 0 && (
        <div className="citation-list">
          {cited.map((c, i) => (
            <button type="button" className="citation" key={`${c.chunk_id}-${i}`} onClick={() => onOpenPage(c.slug)}>
              <span className="citation-n">{i + 1}</span>
              <span className="citation-body">
                <strong><Icon name="file" size={13} /> {c.title}</strong>
                <span>{c.snippet}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

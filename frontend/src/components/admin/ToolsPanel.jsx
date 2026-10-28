import { useEffect, useState } from "react";
import Icon from "../Icon";
import { adminApi } from "../../api";
import { extractErrorMessage } from "../../utils/format";

const ROLE_LABEL = { viewer: "所有成员", editor: "编辑及以上", admin: "仅管理员" };
const KIND_LABEL = { builtin: "内置", http: "HTTP 接口", sql: "SQL 模板" };

function schemaToRows(schema) {
  const required = new Set(schema?.required || []);
  return Object.entries(schema?.properties || {}).map(([name, p]) => ({
    name, type: p.type, description: p.description || "", required: required.has(name), enum: (p.enum || []).join(","),
  }));
}

function rowsToSchema(rows) {
  const properties = {};
  rows.filter((r) => r.name.trim()).forEach((r) => {
    properties[r.name.trim()] = {
      type: r.type, description: r.description,
      ...(r.enum.trim() ? { enum: r.enum.split(/[,，]/).map((v) => v.trim()).filter(Boolean) } : {}),
    };
  });
  return { type: "object", properties, required: rows.filter((r) => r.required && r.name.trim()).map((r) => r.name.trim()) };
}

function ParamsEditor({ rows, onChange }) {
  const update = (i, key, value) => onChange(rows.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  return (
    <div className="params-editor">
      <div className="params-head"><span>参数名</span><span>类型</span><span>说明（给模型看）</span><span>可选值</span><span>必填</span><span /></div>
      {rows.map((r, i) => (
        <div className="params-row" key={i}>
          <input value={r.name} onChange={(e) => update(i, "name", e.target.value)} placeholder="month" />
          <select value={r.type} onChange={(e) => update(i, "type", e.target.value)}>
            <option value="string">文本</option><option value="integer">整数</option><option value="number">数字</option><option value="boolean">是/否</option>
          </select>
          <input value={r.description} onChange={(e) => update(i, "description", e.target.value)} placeholder="月份，格式 YYYY-MM" />
          <input value={r.enum} onChange={(e) => update(i, "enum", e.target.value)} placeholder="逗号分隔，可留空" />
          <input type="checkbox" checked={r.required} onChange={(e) => update(i, "required", e.target.checked)} />
          <button type="button" className="icon-btn" onClick={() => onChange(rows.filter((_, j) => j !== i))} aria-label="删除参数"><Icon name="x" size={14} /></button>
        </div>
      ))}
      <button type="button" className="text-link" onClick={() => onChange([...rows, { name: "", type: "string", description: "", required: false, enum: "" }])}>
        + 添加参数
      </button>
    </div>
  );
}

function ToolForm({ initial, onSaved, onCancel }) {
  const editing = Boolean(initial?.id);
  const [kind, setKind] = useState(initial?.kind || "sql");
  const [form, setForm] = useState({
    name: initial?.name || "", display_name: initial?.display_name || "", description: initial?.description || "",
    min_role: initial?.min_role || "viewer", sql: initial?.config?.sql || "", max_rows: initial?.config?.max_rows || 200,
    method: initial?.config?.method || "GET", url: initial?.config?.url || "", response_path: initial?.config?.response_path || "",
    headers: JSON.stringify(initial?.config?.headers || {}, null, 0), secret: "",
  });
  const [rows, setRows] = useState(() => schemaToRows(initial?.parameters));
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    let headers;
    try {
      headers = form.headers.trim() ? JSON.parse(form.headers) : {};
    } catch {
      setError("请求头必须是 JSON 对象，例如 {\"X-Tenant\": \"demo\"}");
      return;
    }
    const config = kind === "sql"
      ? { sql: form.sql, max_rows: Number(form.max_rows) }
      : { method: form.method, url: form.url, headers, response_path: form.response_path };
    const body = {
      display_name: form.display_name, description: form.description, min_role: form.min_role,
      parameters: rowsToSchema(rows), config,
    };
    if (form.secret.trim()) {
      const [key, ...rest] = form.secret.split(":");
      body.secret_headers = { [key.trim()]: rest.join(":").trim() };
    }
    setBusy(true);
    try {
      const saved = editing ? await adminApi.updateTool(initial.id, body) : await adminApi.createTool({ ...body, name: form.name, kind });
      onSaved(saved);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel tool-form" onSubmit={submit}>
      <div className="panel-title-row">
        <h4 className="panel-title"><Icon name={editing ? "edit" : "plus"} size={15} /> {editing ? `编辑工具：${initial.display_name}` : "添加工具"}</h4>
        {!editing && (
          <div className="segmented">
            <button type="button" className={kind === "sql" ? "active" : ""} onClick={() => setKind("sql")}><Icon name="database" size={14} /> SQL 模板</button>
            <button type="button" className={kind === "http" ? "active" : ""} onClick={() => setKind("http")}><Icon name="link" size={14} /> HTTP 接口</button>
          </div>
        )}
      </div>
      <div className="form-grid">
        <label>工具标识
          <input value={form.name} onChange={set("name")} disabled={editing} required pattern="[a-z][a-z0-9_]{2,40}" placeholder="gmv_by_province" title="小写字母开头，可含数字和下划线" />
        </label>
        <label>显示名称<input value={form.display_name} onChange={set("display_name")} required maxLength={100} placeholder="门店月度毛利" /></label>
        <label>可使用的角色
          <select value={form.min_role} onChange={set("min_role")}>{Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </label>
        <label className="span-3">用途说明（模型据此判断何时调用，写清输入输出）
          <textarea rows={2} value={form.description} onChange={set("description")} required minLength={5} placeholder="查询指定月份各门店的销售额和毛利，返回门店、销售额、毛利（元）。" />
        </label>
      </div>
      <div className="form-block">
        <span className="form-label">参数</span>
        <ParamsEditor rows={rows} onChange={setRows} />
      </div>
      {kind === "sql" ? (
        <div className="form-grid">
          <label className="span-3">SQL 模板（用 :参数名 引用参数；只允许 SELECT，执行时走只读账号）
            <textarea className="code-input" rows={6} value={form.sql} onChange={set("sql")} required
              placeholder={"SELECT d.short_name AS 门店, SUM(s.sale_price) AS 销售额, SUM(s.gross_profit) AS 毛利\nFROM fact_store_sales s JOIN dim_store d ON d.store_id = s.store_id\nWHERE s.month_label = :month GROUP BY 1 ORDER BY 3 DESC"} />
          </label>
          <label>最多返回行数<input type="number" min={1} max={2000} value={form.max_rows} onChange={set("max_rows")} /></label>
        </div>
      ) : (
        <div className="form-grid">
          <label>方法<select value={form.method} onChange={set("method")}><option>GET</option><option>POST</option></select></label>
          <label className="span-2">URL（路径参数写成 {"{参数名}"}，其余参数 GET 放查询串、POST 放 JSON 体）
            <input value={form.url} onChange={set("url")} required placeholder="https://crm.example.com/api/customers/{customer_id}" />
          </label>
          <label>只取响应中的字段<input value={form.response_path} onChange={set("response_path")} placeholder="data.items，可留空" /></label>
          <label>普通请求头（JSON）<input value={form.headers} onChange={set("headers")} placeholder='{"X-Tenant": "demo"}' /></label>
          <label>加密保存的凭证头
            <input type="password" value={form.secret} onChange={set("secret")} autoComplete="new-password"
              placeholder={Object.keys(initial?.secret_headers || {}).length ? `已保存 ${Object.entries(initial.secret_headers).map(([k, v]) => `${k}: ${v}`).join("，")}` : "Authorization: Bearer xxx"} />
          </label>
        </div>
      )}
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm" disabled={busy}>{busy ? "保存中…" : "保存"}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>取消</button>
      </div>
    </form>
  );
}

function ToolTester({ tool, onClose }) {
  const props = tool.parameters?.properties || {};
  const [args, setArgs] = useState({});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      setResult(await adminApi.testTool(tool.id, args));
    } catch (err) {
      setResult({ ok: false, summary: extractErrorMessage(err), for_model: "" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="tool-tester" onSubmit={run}>
      <div className="form-grid">
        {Object.entries(props).map(([name, p]) => (
          <label key={name}>{name}{tool.parameters.required?.includes(name) ? " *" : ""}
            {p.enum ? (
              <select value={args[name] ?? ""} onChange={(e) => setArgs({ ...args, [name]: e.target.value })}>
                <option value="">（不填）</option>{p.enum.map((v) => <option key={v}>{v}</option>)}
              </select>
            ) : (
              <input value={args[name] ?? ""} onChange={(e) => setArgs({ ...args, [name]: e.target.value })} placeholder={p.description} />
            )}
          </label>
        ))}
        {Object.keys(props).length === 0 && <p className="muted">这个工具没有参数。</p>}
      </div>
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm" disabled={busy}>{busy ? "执行中…" : "执行测试"}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onClose}>收起</button>
      </div>
      {result && (
        <div className={`callout ${result.ok ? "info" : "danger"}`}>
          <Icon name={result.ok ? "check" : "alert"} size={16} />
          <div className="test-output">
            <strong>{result.summary}</strong>
            {result.for_model && <pre className="sql-code">{result.for_model}</pre>}
          </div>
        </div>
      )}
    </form>
  );
}

export default function ToolsPanel() {
  const [items, setItems] = useState([]);
  const [editing, setEditing] = useState(null);
  const [testing, setTesting] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    adminApi.listTools().then((d) => setItems(d.items)).catch((err) => setError(extractErrorMessage(err)));
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

  const builtin = items.filter((t) => t.kind === "builtin");
  const custom = items.filter((t) => t.kind !== "builtin");

  const row = (t) => (
    <li key={t.id} className={`tool-row ${t.enabled ? "" : "disabled"}`}>
      <div className="tool-main">
        <div className="tool-title">
          <span className={`kind-badge ${t.kind}`}>{KIND_LABEL[t.kind]}</span>
          <strong>{t.display_name}</strong>
          <code>{t.name}</code>
        </div>
        <p>{t.description}</p>
        {t.kind !== "builtin" && (
          <small className="muted">
            参数：{Object.keys(t.parameters?.properties || {}).join("、") || "无"}
            {t.kind === "http" && ` · ${t.config.method} ${t.config.url}`}
          </small>
        )}
        {testing === t.id && <ToolTester tool={t} onClose={() => setTesting(null)} />}
      </div>
      <div className="tool-side">
        <select value={t.min_role} onChange={(e) => act(() => adminApi.updateTool(t.id, { min_role: e.target.value }))} title="谁可以让助手使用这个工具">
          {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label className="switch small">
          <input type="checkbox" checked={t.enabled} onChange={(e) => act(() => adminApi.updateTool(t.id, { enabled: e.target.checked }))} />
          <span className="switch-track" />
        </label>
        <div className="btn-row tight">
          <button type="button" className="text-link" onClick={() => setTesting(testing === t.id ? null : t.id)}>测试</button>
          {t.kind !== "builtin" && <button type="button" className="text-link" onClick={() => setEditing(t)}>编辑</button>}
          {t.kind !== "builtin" && (
            <button type="button" className="text-link danger-link" onClick={() => window.confirm(`删除工具「${t.display_name}」？`) && act(() => adminApi.deleteTool(t.id))}>删除</button>
          )}
        </div>
      </div>
    </li>
  );

  return (
    <div className="admin-section">
      <div className="callout info">
        <Icon name="code" size={16} />
        <span>
          助手在对话中会根据「用途说明」自行决定调用哪个工具。可以把常用口径固化成 SQL 模板工具，或把 CRM、工单等内部系统接成 HTTP 工具。
          HTTP 工具默认不能访问内网地址，需要时由运维在 TOOL_HTTP_ALLOWED_HOSTS 中放行。
        </span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {editing ? (
        <ToolForm initial={editing === "new" ? null : editing} onSaved={() => { setEditing(null); reload(); }} onCancel={() => setEditing(null)} />
      ) : (
        <button type="button" className="btn-primary btn-sm self-start" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> 添加工具</button>
      )}
      <div className="panel">
        <h4 className="panel-title"><Icon name="code" size={15} /> 自定义工具 <span className="count">{custom.length}</span></h4>
        {custom.length ? <ul className="tool-list">{custom.map(row)}</ul> : <p className="side-empty">还没有自定义工具。</p>}
      </div>
      <div className="panel">
        <h4 className="panel-title"><Icon name="sparkles" size={15} /> 内置工具 <span className="count">{builtin.length}</span></h4>
        <ul className="tool-list">{builtin.map(row)}</ul>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import Icon from "../Icon";
import { adminApi } from "../../api";
import { extractErrorMessage } from "../../utils/format";

const PRESETS = {
  ollama: { name: "本地 Ollama", kind: "local", provider: "openai_compatible", base_url: "http://localhost:11434/v1", model: "", no_reasoning_effort: "none", context_window: 32000, timeout_seconds: 180 },
  vllm: { name: "本地 vLLM", kind: "local", provider: "openai_compatible", base_url: "http://localhost:8001/v1", model: "", context_window: 32000 },
  deepseek: { name: "DeepSeek", kind: "cloud", provider: "openai_compatible", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", context_window: 64000 },
  qwen: { name: "通义千问", kind: "cloud", provider: "openai_compatible", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-max", context_window: 32000 },
  anthropic: { name: "DeepSeek（Anthropic 协议）", kind: "cloud", provider: "anthropic", base_url: "https://api.deepseek.com/anthropic", model: "deepseek-chat", context_window: 64000 },
};

const EMPTY = {
  name: "", kind: "cloud", provider: "openai_compatible", base_url: "", api_key: "", model: "",
  context_window: 64000, max_output_tokens: 2048, thinking_budget: 0, reasoning_effort: "", no_reasoning_effort: "",
  timeout_seconds: 60, enabled: true, description: "", price_input: "", price_output: "",
};
// 单价留空 = 使用系统默认价（BILLING_DEFAULT_PRICE_INPUT / OUTPUT）
const priceValue = (v) => (v === "" || v == null ? null : Number(v));

function ModelForm({ initial, onSaved, onCancel }) {
  const editing = Boolean(initial?.id);
  const [form, setForm] = useState(() => (editing ? { ...EMPTY, ...initial, api_key: "" } : { ...EMPTY }));
  const [models, setModels] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.type === "number" ? Number(e.target.value) : e.target.value });

  const discover = async () => {
    setError(null);
    try {
      const res = await adminApi.discoverModels({ provider: form.provider, base_url: form.base_url, api_key: form.api_key || null, profile_id: initial?.id });
      setModels(res.models);
      if (!res.models.length) setError("服务没有返回可用模型");
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { ...form, price_input: priceValue(form.price_input), price_output: priceValue(form.price_output) };
      if (editing && !body.api_key) delete body.api_key; // 留空表示不修改
      delete body.id;
      ["is_default", "last_test", "created_at", "updated_at", "api_key_masked"].forEach((k) => delete body[k]);
      const saved = editing ? await adminApi.updateModel(initial.id, body) : await adminApi.createModel(body);
      onSaved(saved);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel model-form" onSubmit={submit}>
      <div className="panel-title-row">
        <h4 className="panel-title"><Icon name={editing ? "edit" : "plus"} size={15} /> {editing ? `编辑：${initial.name}` : "添加模型"}</h4>
        {!editing && (
          <div className="preset-row">
            <span className="muted">快速填写：</span>
            {Object.entries(PRESETS).map(([key, p]) => (
              <button type="button" key={key} className="tag-chip" onClick={() => setForm({ ...EMPTY, ...p })}>{p.name}</button>
            ))}
          </div>
        )}
      </div>
      <div className="form-grid">
        <label>名称<input value={form.name} onChange={set("name")} required maxLength={100} /></label>
        <label>部署位置
          <select value={form.kind} onChange={set("kind")}>
            <option value="local">本地 / 私有部署</option>
            <option value="cloud">云端 API</option>
          </select>
        </label>
        <label>接口协议
          <select value={form.provider} onChange={set("provider")}>
            <option value="openai_compatible">OpenAI 兼容</option>
            <option value="anthropic">Anthropic 兼容</option>
          </select>
        </label>
        <label className="span-2">接口地址<input value={form.base_url} onChange={set("base_url")} required placeholder="http://localhost:11434/v1" /></label>
        <label>API Key
          <input type="password" value={form.api_key} onChange={set("api_key")} autoComplete="new-password"
            placeholder={editing ? (initial.api_key_masked ? `已保存 ${initial.api_key_masked}，留空不修改` : "未设置") : "本地模型可不填"} />
        </label>
        <label className="span-2">模型名
          <div className="input-with-btn">
            <input value={form.model} onChange={set("model")} required list="discovered-models" placeholder="qwen3.5:4b-mlx / deepseek-chat" />
            <button type="button" className="btn-secondary btn-sm" onClick={discover} disabled={!form.base_url}>获取模型列表</button>
          </div>
          <datalist id="discovered-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
        </label>
        <label>上下文窗口（tokens）<input type="number" min={2000} value={form.context_window} onChange={set("context_window")} /></label>
        <label>最大输出 tokens<input type="number" min={256} value={form.max_output_tokens} onChange={set("max_output_tokens")} /></label>
        <label>超时（秒）<input type="number" min={5} value={form.timeout_seconds} onChange={set("timeout_seconds")} /></label>
        <label title="每 1K 输入 token 扣多少点，留空使用系统默认价">输入单价（点 / 1K token）
          <input type="number" min={0} step="0.01" placeholder="默认" value={form.price_input ?? ""} onChange={(e) => setForm({ ...form, price_input: e.target.value })} />
        </label>
        <label title="每 1K 输出 token 扣多少点，留空使用系统默认价">输出单价（点 / 1K token）
          <input type="number" min={0} step="0.01" placeholder="默认" value={form.price_output ?? ""} onChange={(e) => setForm({ ...form, price_output: e.target.value })} />
        </label>
        <label className="span-3">在「发现」中显示的介绍
          <input value={form.description || ""} maxLength={500} onChange={set("description")} placeholder="例如：门店本地部署，数据不出内网，适合日常问答" />
        </label>
        {form.provider === "anthropic" ? (
          <label>Thinking 预算（0 关闭）<input type="number" min={0} value={form.thinking_budget} onChange={set("thinking_budget")} /></label>
        ) : (
          <>
            <label>推理强度
              <select value={form.reasoning_effort} onChange={set("reasoning_effort")}>
                <option value="">不指定</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option>
              </select>
            </label>
            <label title="记忆提炼、会话摘要等后台任务使用。Ollama / Qwen 等推理模型建议选 none">后台任务推理
              <select value={form.no_reasoning_effort} onChange={set("no_reasoning_effort")}>
                <option value="">不指定</option><option value="none">none（关闭）</option><option value="low">low</option>
              </select>
            </label>
          </>
        )}
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm" disabled={busy}>{busy ? "保存中…" : "保存"}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>取消</button>
      </div>
    </form>
  );
}

function TestBadge({ test }) {
  if (!test) return <span className="status-pill processing">未测试</span>;
  if (!test.ok) return <span className="status-pill failed" title={test.error}>连接失败</span>;
  return (
    <>
      <span className="status-pill ready">{test.latency_ms} ms</span>
      <span className={`status-pill ${test.tools ? "ready" : "failed"}`}>{test.tools ? "支持工具调用" : "不支持工具调用"}</span>
    </>
  );
}

export default function ModelsPanel() {
  const [items, setItems] = useState([]);
  const [editing, setEditing] = useState(null);
  const [testing, setTesting] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    adminApi.listModels().then((d) => setItems(d.items)).catch((err) => setError(extractErrorMessage(err)));
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

  const test = async (id) => {
    setTesting(id);
    await act(() => adminApi.testModel(id));
    setTesting(null);
  };

  return (
    <div className="admin-section">
      <div className="callout info">
        <Icon name="sparkles" size={16} />
        <span>在这里接入本地模型（Ollama / vLLM）和云端 API（DeepSeek、通义千问等）。默认模型用于新对话，用户可以在输入框下方按对话切换。API Key 加密保存，界面只显示掩码。</span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {editing ? (
        <ModelForm initial={editing === "new" ? null : editing} onSaved={() => { setEditing(null); reload(); }} onCancel={() => setEditing(null)} />
      ) : (
        <button type="button" className="btn-primary btn-sm self-start" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> 添加模型</button>
      )}
      <div className="model-cards">
        {items.length === 0 && <p className="side-empty">还没有配置模型，当前使用环境变量中的模型配置。</p>}
        {items.map((m) => (
          <div key={m.id} className={`panel model-card ${m.enabled ? "" : "disabled"}`}>
            <div className="model-card-head">
              <span className={`kind-badge ${m.kind}`}>{m.kind === "local" ? "本地" : "云端"}</span>
              <strong>{m.name}</strong>
              {m.is_default && <span className="tag">默认</span>}
              {!m.enabled && <span className="tag warn">已停用</span>}
            </div>
            <dl className="kv">
              <dt>模型</dt><dd><code>{m.model}</code></dd>
              <dt>接口</dt><dd><code>{m.base_url}</code> · {m.provider === "anthropic" ? "Anthropic" : "OpenAI"} 协议</dd>
              <dt>上下文</dt><dd>{(m.context_window / 1000).toFixed(0)}k tokens{m.api_key_masked && ` · Key ${m.api_key_masked}`}</dd>
              <dt>单价</dt><dd>{m.price_input == null && m.price_output == null ? "系统默认" : `输入 ${m.price_input ?? "默认"} / 输出 ${m.price_output ?? "默认"} 点每 1K token`}</dd>
              <dt>状态</dt><dd className="badge-row"><TestBadge test={m.last_test} /></dd>
            </dl>
            <div className="btn-row">
              <button type="button" className="btn-secondary btn-sm" onClick={() => test(m.id)} disabled={testing === m.id}>
                {testing === m.id ? "测试中…" : "测试连接"}
              </button>
              {!m.is_default && m.enabled && <button type="button" className="btn-secondary btn-sm" onClick={() => act(() => adminApi.setDefaultModel(m.id))}>设为默认</button>}
              <button type="button" className="btn-secondary btn-sm" onClick={() => setEditing(m)}>编辑</button>
              {!m.is_default && (
                <button type="button" className="btn-secondary btn-sm" onClick={() => act(() => adminApi.updateModel(m.id, { enabled: !m.enabled }))}>
                  {m.enabled ? "停用" : "启用"}
                </button>
              )}
              <button type="button" className="text-link danger-link" onClick={() => window.confirm(`删除模型「${m.name}」？`) && act(() => adminApi.deleteModel(m.id))}>删除</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

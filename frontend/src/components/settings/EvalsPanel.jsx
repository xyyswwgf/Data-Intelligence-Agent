import { useCallback, useEffect, useState } from "react";
import Icon from "../Icon";
import { adminApi, getEvalRun, listEvalRuns, listEvalSets, startEvalRun } from "../../api";
import { extractErrorMessage } from "../../utils/format";

const MODE_LABEL = { assistant: "智能助手", query: "SQL 查询", kb: "知识问答" };
const fmtTime = (v) => (v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—");
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function RunDetail({ runId, onClose }) {
  const [run, setRun] = useState(null);
  useEffect(() => {
    let timer;
    const load = () => getEvalRun(runId).then((r) => {
      setRun(r);
      if (r.status === "queued" || r.status === "running") timer = setTimeout(load, 3000);
    }).catch(() => {});
    load();
    return () => clearTimeout(timer);
  }, [runId]);
  if (!run) return <span className="spinner" />;
  return (
    <div className="settings-card">
      <div className="settings-card-head">
        <h4>{run.set_name} · {run.model_name}</h4>
        <button type="button" className="btn-secondary btn-sm" onClick={onClose}>收起</button>
      </div>
      <table className="mini-table eval-table">
        <thead><tr><th /><th>问题</th><th>方式</th><th>标准答案</th><th>回答摘录</th><th>耗时</th></tr></thead>
        <tbody>
          {run.results.map((r) => (
            <tr key={r.id}>
              <td>{r.passed == null ? <span className="muted">跳过</span> : r.passed ? <Icon name="check" size={15} className="ok" /> : <Icon name="x" size={15} className="bad" />}</td>
              <td>{r.question}</td>
              <td>{MODE_LABEL[r.mode] || r.mode}</td>
              <td>{r.checks.map((c, i) => <div key={i} className={c.passed === false ? "negative" : ""}>{Array.isArray(c.expected) ? c.expected.join(" / ") : String(c.expected)}</div>)}</td>
              <td className="eval-answer">{r.error ? <span className="negative">{r.error}</span> : r.answer}</td>
              <td>{(r.elapsed_ms / 1000).toFixed(1)}s</td>
            </tr>
          ))}
          {run.status !== "done" && <tr><td colSpan={6} className="muted">评测进行中：{run.done}/{run.total}…</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

/** 设置 → 管理 → 准确率评测：同一组问题在不同模型 / 不同时间的通过率 */
export default function EvalsPanel() {
  const [sets, setSets] = useState([]);
  const [models, setModels] = useState([]);
  const [runs, setRuns] = useState([]);
  const [form, setForm] = useState({ set_code: "", profile_id: "" });
  const [openRun, setOpenRun] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadRuns = useCallback(() => listEvalRuns().then((d) => setRuns(d.items)).catch(() => {}), []);
  useEffect(() => {
    listEvalSets().then((d) => {
      setSets(d.items);
      setForm((f) => ({ ...f, set_code: f.set_code || d.items[0]?.code || "" }));
    }).catch((err) => setError(extractErrorMessage(err)));
    adminApi.listModels().then((d) => setModels(d.items.filter((m) => m.enabled))).catch(() => {});
    loadRuns();
  }, [loadRuns]);

  // 有评测在运行时定时刷新进度
  const running = runs.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(loadRuns, 3000);
    return () => clearInterval(timer);
  }, [running, loadRuns]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await startEvalRun({ set_code: form.set_code, profile_id: form.profile_id ? Number(form.profile_id) : null });
      await loadRuns();
      setOpenRun(run.id);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const current = sets.find((s) => s.code === form.set_code);
  return (
    <>
      <div className="callout info">
        <Icon name="bulb" size={16} />
        <span>
          用一组固定的业务问题检验回答是否正确。每题的标准答案由参考 SQL 在评测时从当前数据算出，所以导入新数据后不用改评测集。
          换模型、改提示词之后各跑一次，就能用通过率比较效果。评测本身不消耗成员的点数。
        </span>
      </div>
      <div className="settings-card">
        <div className="form-grid">
          <label>评测集
            <select value={form.set_code} onChange={(e) => setForm({ ...form, set_code: e.target.value })}>
              {sets.map((s) => <option key={s.code} value={s.code}>{s.name}（{s.cases.length} 题）{s.private ? " · 私有" : ""}</option>)}
            </select>
          </label>
          <label>模型
            <select value={form.profile_id} onChange={(e) => setForm({ ...form, profile_id: e.target.value })}>
              <option value="">系统默认模型</option>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}（{m.kind === "local" ? "本地" : "云端"}）</option>)}
            </select>
          </label>
          <label>&nbsp;
            <button type="button" className="btn-primary" onClick={start} disabled={busy || !form.set_code}>
              <Icon name="activity" size={14} /> {busy ? "启动中…" : "开始评测"}
            </button>
          </label>
        </div>
        {current?.description && <p className="form-hint">{current.description}</p>}
        {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      </div>

      <div className="settings-card">
        <h4>评测记录</h4>
        <table className="mini-table">
          <thead><tr><th>时间</th><th>评测集</th><th>模型</th><th>进度</th><th>通过率</th><th /></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{fmtTime(r.created_at)}</td>
                <td>{r.set_name}</td>
                <td>{r.model_name}</td>
                <td>{r.status === "failed" ? <span className="negative">失败</span> : `${r.done}/${r.total}`}</td>
                <td>
                  <div className="eval-score">
                    <span className={`quota-bar ${r.accuracy != null && r.accuracy < 0.7 ? "warn" : ""}`}><span style={{ width: `${(r.accuracy || 0) * 100}%` }} /></span>
                    <b>{pct(r.accuracy)}</b> <small>{r.passed}/{r.scored}</small>
                  </div>
                </td>
                <td><button type="button" className="btn-secondary btn-sm" onClick={() => setOpenRun(openRun === r.id ? null : r.id)}>{openRun === r.id ? "收起" : "明细"}</button></td>
              </tr>
            ))}
            {runs.length === 0 && <tr><td colSpan={6} className="muted">还没有评测记录</td></tr>}
          </tbody>
        </table>
      </div>
      {openRun && <RunDetail key={openRun} runId={openRun} onClose={() => setOpenRun(null)} />}
    </>
  );
}

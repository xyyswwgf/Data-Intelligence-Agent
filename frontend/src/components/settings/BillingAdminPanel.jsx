import { useCallback, useEffect, useState } from "react";
import Icon from "../Icon";
import {
  adjustBalance,
  assignPlan,
  createBillingPlan,
  getAdminTransactions,
  getAdminUsage,
  listBillingAccounts,
  listBillingPlans,
  updateBillingPlan,
} from "../../api";
import { ROLE_LABEL, formatCredits, formatYuan } from "../../navigation";
import { extractErrorMessage } from "../../utils/format";
import { UsageChart, UsageTables } from "./BillingSections";

const TABS = [
  { id: "accounts", label: "成员额度" },
  { id: "plans", label: "方案定价" },
  { id: "usage", label: "全员用量" },
  { id: "transactions", label: "账单流水" },
];
const TX_LABEL = { subscribe: "开通方案", recharge: "充值", adjust: "调整", refund: "退款" };
const fmtTime = (v) => (v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—");
const yuanToCents = (v) => Math.round(Number(v || 0) * 100);

function AccountAction({ account, plans, onDone, onCancel }) {
  const [mode, setMode] = useState("plan");
  const [form, setForm] = useState({ plan_id: account.plan_id, months: 1, yuan: "", credits: "", note: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "plan") {
        await assignPlan(account.user_id, { plan_id: Number(form.plan_id), months: Number(form.months),
          amount_cents: yuanToCents(form.yuan), note: form.note || null });
      } else {
        await adjustBalance(account.user_id, { credits: Number(form.credits), amount_cents: yuanToCents(form.yuan),
          note: form.note || null });
      }
      onDone();
    } catch (err) {
      setError(extractErrorMessage(err));
      setBusy(false);
    }
  };

  return (
    <form className="account-action" onSubmit={submit}>
      <div className="segmented small">
        <button type="button" className={mode === "plan" ? "active" : ""} onClick={() => setMode("plan")}>开通 / 更换方案</button>
        <button type="button" className={mode === "balance" ? "active" : ""} onClick={() => setMode("balance")}>充值 / 调整余额</button>
      </div>
      <div className="form-grid">
        {mode === "plan" ? (
          <>
            <label>方案
              <select value={form.plan_id} onChange={(e) => setForm({ ...form, plan_id: e.target.value })}>
                {plans.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.name}（{formatCredits(p.monthly_credits)} 点/月）</option>)}
              </select>
            </label>
            <label>时长（月，0 = 长期）
              <input type="number" min={0} max={120} value={form.months} onChange={(e) => setForm({ ...form, months: e.target.value })} required />
            </label>
          </>
        ) : (
          <label>点数（负数为扣减）
            <input type="number" step="any" value={form.credits} onChange={(e) => setForm({ ...form, credits: e.target.value })} required />
          </label>
        )}
        <label>实收金额（元，仅记账）
          <input type="number" min={0} step="0.01" value={form.yuan} onChange={(e) => setForm({ ...form, yuan: e.target.value })} />
        </label>
        <label className="span-3">备注
          <input value={form.note} maxLength={200} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="例如：线下转账 / 门店月度分配" />
        </label>
      </div>
      {mode === "plan" && <p className="form-hint">从现在起开始新周期，本期已用额度清零；到期后自动回到默认方案。</p>}
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm" disabled={busy}>{busy ? "保存中…" : "确认"}</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>取消</button>
      </div>
    </form>
  );
}

function AccountsTab({ plans }) {
  const [items, setItems] = useState(null);
  const [active, setActive] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    listBillingAccounts().then((d) => setItems(d.items)).catch((err) => setError(extractErrorMessage(err)));
  }, []);
  useEffect(load, [load]);

  if (error) return <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>;
  if (!items) return <span className="spinner" />;
  return (
    <div className="settings-card">
      <table className="mini-table">
        <thead><tr><th>成员</th><th>方案</th><th>本期已用 / 额度</th><th>余额</th><th>近 30 天</th><th>到期</th><th /></tr></thead>
        <tbody>
          {items.map((a) => (
            <FragmentRow key={a.user_id} a={a} open={active === a.user_id} onToggle={() => setActive(active === a.user_id ? null : a.user_id)}>
              <AccountAction account={a} plans={plans} onCancel={() => setActive(null)} onDone={() => { setActive(null); load(); }} />
            </FragmentRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FragmentRow({ a, open, onToggle, children }) {
  return (
    <>
      <tr className={a.is_active ? "" : "muted"}>
        <td>{a.display_name}<small className="cell-sub">@{a.username} · {ROLE_LABEL[a.role]}</small></td>
        <td>{a.plan_name}</td>
        <td>{formatCredits(a.used_credits)} / {formatCredits(a.monthly_credits)}</td>
        <td className={a.balance < 0 ? "negative" : ""}>{formatCredits(a.balance)}</td>
        <td>{formatCredits(a.credits_30d)}</td>
        <td>{a.expires_at ? new Date(a.expires_at).toLocaleDateString("zh-CN") : "长期"}</td>
        <td><button type="button" className="btn-secondary btn-sm" onClick={onToggle}>{open ? "收起" : "开通 / 充值"}</button></td>
      </tr>
      {open && <tr className="action-row"><td colSpan={7}>{children}</td></tr>}
    </>
  );
}

const EMPTY_PLAN = { code: "", name: "", description: "", yuan: "0", monthly_credits: "1000", features: "", sort_order: 50 };

function PlanEditor({ plan, onSaved, onCancel }) {
  const [form, setForm] = useState(plan ? {
    ...plan, yuan: String(plan.price_cents / 100), monthly_credits: String(plan.monthly_credits), features: plan.features.join("\n"),
  } : EMPTY_PLAN);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const payload = {
      name: form.name, description: form.description, price_cents: yuanToCents(form.yuan),
      monthly_credits: Number(form.monthly_credits), sort_order: Number(form.sort_order) || 0,
      features: form.features.split("\n").map((f) => f.trim()).filter(Boolean),
    };
    try {
      if (plan) await updateBillingPlan(plan.id, payload);
      else await createBillingPlan({ ...payload, code: form.code });
      onSaved();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  return (
    <form className="settings-card" onSubmit={submit}>
      <h4>{plan ? `编辑「${plan.name}」` : "新建方案"}</h4>
      <div className="form-grid">
        {!plan && <label>标识（英文）<input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="enterprise" required /></label>}
        <label>名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
        <label>月费（元）<input type="number" min={0} step="0.01" value={form.yuan} onChange={(e) => setForm({ ...form, yuan: e.target.value })} /></label>
        <label>每月额度（点）<input type="number" min={0} value={form.monthly_credits} onChange={(e) => setForm({ ...form, monthly_credits: e.target.value })} required /></label>
        <label>排序<input type="number" value={form.sort_order} onChange={(e) => setForm({ ...form, sort_order: e.target.value })} /></label>
        <label className="span-3">简介<input value={form.description} maxLength={300} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <label className="span-3">卖点（每行一条，最多 12 条）
          <textarea rows={4} value={form.features} onChange={(e) => setForm({ ...form, features: e.target.value })} />
        </label>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="btn-row">
        <button type="submit" className="btn-primary btn-sm">保存</button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>取消</button>
      </div>
    </form>
  );
}

function PlansTab({ plans, reload }) {
  const [editing, setEditing] = useState(null); // plan 对象或 "new"
  const [error, setError] = useState(null);

  const patch = async (plan, payload) => {
    setError(null);
    try {
      await updateBillingPlan(plan.id, payload);
      reload();
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  };

  return (
    <>
      <div className="callout info">
        <Icon name="bulb" size={16} />
        <span>
          点数是平台内的计量单位：每次提问按模型单价（「模型接入」中设置，每 1K token 的点数）折算，先扣方案额度，用完再扣余额。
          对外收费前，请先完成生成式 AI 服务备案和相关经营许可。
        </span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {editing && (
        <PlanEditor plan={editing === "new" ? null : editing} onCancel={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }} />
      )}
      <div className="plan-grid">
        {plans.map((p) => (
          <div key={p.id} className={`plan-card ${p.enabled ? "" : "disabled"}`}>
            <div className="plan-card-head">
              <strong>{p.name}</strong>
              {p.is_default && <span className="plan-badge">默认</span>}
              {!p.enabled && <span className="plan-badge muted">已停用</span>}
            </div>
            <div className="plan-price">{p.price_cents ? <>{formatYuan(p.price_cents)}<small> / 月</small></> : "免费"}</div>
            <p>{formatCredits(p.monthly_credits)} 点 / 月 · {p.subscribers} 位成员</p>
            <ul>{p.features.map((f) => <li key={f}><Icon name="check" size={14} /> {f}</li>)}</ul>
            <div className="btn-row">
              <button type="button" className="btn-secondary btn-sm" onClick={() => setEditing(p)}>编辑</button>
              {!p.is_default && p.enabled && <button type="button" className="btn-secondary btn-sm" onClick={() => patch(p, { is_default: true })}>设为默认</button>}
              {!p.is_default && (
                <button type="button" className="btn-secondary btn-sm" onClick={() => patch(p, { enabled: !p.enabled })}>{p.enabled ? "停用" : "启用"}</button>
              )}
            </div>
          </div>
        ))}
        <button type="button" className="plan-card add" onClick={() => setEditing("new")}>
          <Icon name="plus" size={20} /> 新建方案
        </button>
      </div>
    </>
  );
}

function UsageTab() {
  const [report, setReport] = useState(null);
  useEffect(() => {
    getAdminUsage(30).then(setReport).catch(() => {});
  }, []);
  if (!report) return <span className="spinner" />;
  return (
    <>
      <div className="metric-grid">
        <div className="metric-card"><span className="metric-name">近 30 天点数</span><strong>{formatCredits(report.totals.credits)}</strong></div>
        <div className="metric-card"><span className="metric-name">请求次数</span><strong>{report.totals.requests}</strong></div>
        <div className="metric-card"><span className="metric-name">活跃成员</span><strong>{report.top_users.length}</strong></div>
        <div className="metric-card"><span className="metric-name">Token（入 + 出）</span><strong>{(report.totals.input_tokens + report.totals.output_tokens).toLocaleString()}</strong></div>
      </div>
      <div className="settings-card"><h4>每日用量</h4><UsageChart daily={report.daily} /></div>
      <UsageTables report={report} />
      <div className="settings-card">
        <h4>用量最多的成员</h4>
        <table className="mini-table">
          <thead><tr><th>成员</th><th>次数</th><th>点数</th></tr></thead>
          <tbody>
            {report.top_users.map((u) => <tr key={u.username}><td>{u.display_name} <small className="cell-sub">@{u.username}</small></td><td>{u.requests}</td><td>{formatCredits(u.credits)}</td></tr>)}
            {report.top_users.length === 0 && <tr><td colSpan={3} className="muted">暂无用量</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TransactionsTab() {
  const [items, setItems] = useState(null);
  useEffect(() => {
    getAdminTransactions().then((d) => setItems(d.items)).catch(() => setItems([]));
  }, []);
  if (!items) return <span className="spinner" />;
  return (
    <div className="settings-card">
      <table className="mini-table">
        <thead><tr><th>时间</th><th>成员</th><th>类型</th><th>内容</th><th>点数</th><th>金额</th><th>经办</th></tr></thead>
        <tbody>
          {items.map((t) => (
            <tr key={t.id}>
              <td>{fmtTime(t.created_at)}</td><td>@{t.username}</td><td>{TX_LABEL[t.kind] || t.kind}</td>
              <td>{t.note || t.plan_name || "—"}</td><td>{t.credits > 0 ? "+" : ""}{formatCredits(t.credits)}</td>
              <td>{t.amount_cents ? formatYuan(t.amount_cents) : "—"}</td><td>{t.operator ? `@${t.operator}` : "在线支付"}</td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={7} className="muted">暂无账单</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

/** 设置 → 管理 → 方案与计费 */
export default function BillingAdminPanel() {
  const [tab, setTab] = useState("accounts");
  const [plans, setPlans] = useState([]);
  const reloadPlans = useCallback(() => {
    listBillingPlans().then((d) => setPlans(d.items)).catch(() => {});
  }, []);
  useEffect(reloadPlans, [reloadPlans]);

  return (
    <div className="admin-section">
      <div className="segmented">
        {TABS.map((t) => (
          <button type="button" key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>
      {tab === "accounts" && <AccountsTab plans={plans} />}
      {tab === "plans" && <PlansTab plans={plans} reload={reloadPlans} />}
      {tab === "usage" && <UsageTab />}
      {tab === "transactions" && <TransactionsTab />}
    </div>
  );
}

import { useEffect, useState } from "react";
import ReactECharts from "echarts-for-react";
import Icon from "../Icon";
import { getMyTransactions, getMyUsage, getPublicPlans } from "../../api";
import { formatCredits, formatYuan } from "../../navigation";
import { CHART_THEME } from "../../utils/echartsTheme";
import { extractErrorMessage } from "../../utils/format";

const MODE_LABEL = { assistant: "智能助手", agent: "经营分析", kb: "知识问答", query: "SQL 查询", other: "其他" };
const TX_LABEL = { subscribe: "开通方案", recharge: "充值", adjust: "调整", refund: "退款" };
const fmtDate = (v) => (v ? new Date(v).toLocaleDateString("zh-CN") : "—");
const fmtTime = (v) => (v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—");

/** 本期额度进度：方案额度 + 余额 */
export function QuotaCard({ account }) {
  if (!account) return <div className="settings-card"><span className="spinner" /></div>;
  const quota = account.plan.monthly_credits;
  const pct = quota > 0 ? Math.min(100, (account.plan_used / quota) * 100) : 100;
  return (
    <div className="settings-card quota-card">
      <div className="quota-head">
        <div>
          <span className="plan-badge">{account.plan.name}</span>
          <small>本期 {fmtDate(account.period_start)} – {fmtDate(account.period_end)}
            {account.expires_at ? ` · 方案到期 ${fmtDate(account.expires_at)}` : ""}</small>
        </div>
        <strong>{formatCredits(account.plan_remaining)}<small> / {formatCredits(quota)} 点</small></strong>
      </div>
      <div className={`quota-bar ${pct >= 90 ? "warn" : ""}`}><span style={{ width: `${pct}%` }} /></div>
      <div className="quota-foot">
        <span>已用 {formatCredits(account.plan_used)} 点</span>
        <span>余额 <b className={account.balance < 0 ? "negative" : ""}>{formatCredits(account.balance)}</b> 点</span>
      </div>
      {!account.enforce && <p className="settings-note">当前系统只记录用量、不限制使用。</p>}
    </div>
  );
}

export function UsageChart({ daily }) {
  const option = {
    grid: { left: 8, right: 8, top: 24, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: daily.map((d) => d.day.slice(5)) },
    yAxis: { type: "value", name: "点数" },
    series: [{ type: "bar", name: "点数", data: daily.map((d) => Number(d.credits.toFixed(2))) }],
  };
  return <ReactECharts option={option} theme={CHART_THEME} style={{ height: 220 }} notMerge />;
}

export function UsageTables({ report }) {
  return (
    <div className="panel-grid">
      <div className="settings-card">
        <h4>按模型</h4>
        <table className="mini-table">
          <thead><tr><th>模型</th><th>次数</th><th>Token（入 / 出）</th><th>点数</th></tr></thead>
          <tbody>
            {report.by_model.map((m) => (
              <tr key={m.name}>
                <td>{m.name}</td><td>{m.requests}</td>
                <td>{Number(m.input_tokens).toLocaleString()} / {Number(m.output_tokens).toLocaleString()}</td>
                <td>{formatCredits(m.credits)}</td>
              </tr>
            ))}
            {report.by_model.length === 0 && <tr><td colSpan={4} className="muted">暂无用量</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="settings-card">
        <h4>按功能</h4>
        <table className="mini-table">
          <thead><tr><th>功能</th><th>次数</th><th>点数</th></tr></thead>
          <tbody>
            {report.by_mode.map((m) => (
              <tr key={m.mode}><td>{MODE_LABEL[m.mode] || m.mode}</td><td>{m.requests}</td><td>{formatCredits(m.credits)}</td></tr>
            ))}
            {report.by_mode.length === 0 && <tr><td colSpan={3} className="muted">暂无用量</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function UsageSection({ account }) {
  const [days, setDays] = useState(30);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getMyUsage(days).then(setReport).catch((err) => setError(extractErrorMessage(err)));
  }, [days]);

  return (
    <>
      <QuotaCard account={account} />
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="settings-card">
        <div className="settings-card-head">
          <h4>每日用量</h4>
          <div className="segmented small">
            {[7, 30, 90].map((d) => (
              <button type="button" key={d} className={days === d ? "active" : ""} onClick={() => setDays(d)}>{d} 天</button>
            ))}
          </div>
        </div>
        {!report ? <span className="spinner" /> : (
          <>
            <div className="metric-grid">
              <div className="metric-card"><span className="metric-name">点数</span><strong>{formatCredits(report.totals.credits)}</strong></div>
              <div className="metric-card"><span className="metric-name">请求次数</span><strong>{report.totals.requests}</strong></div>
              <div className="metric-card"><span className="metric-name">输入 Token</span><strong>{report.totals.input_tokens.toLocaleString()}</strong></div>
              <div className="metric-card"><span className="metric-name">输出 Token</span><strong>{report.totals.output_tokens.toLocaleString()}</strong></div>
            </div>
            <UsageChart daily={report.daily} />
          </>
        )}
      </div>
      {report && <UsageTables report={report} />}
      {report && report.recent.length > 0 && (
        <div className="settings-card">
          <h4>最近调用</h4>
          <table className="mini-table">
            <thead><tr><th>时间</th><th>功能</th><th>模型</th><th>Token（入 / 出）</th><th>点数</th><th>扣自</th></tr></thead>
            <tbody>
              {report.recent.map((r, i) => (
                <tr key={i}>
                  <td>{fmtTime(r.created_at)}</td><td>{MODE_LABEL[r.mode] || r.mode}</td><td>{r.model}</td>
                  <td>{r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}</td>
                  <td>{formatCredits(r.credits)}</td>
                  <td>{r.balance_credits > 0 ? (r.plan_credits > 0 ? "方案 + 余额" : "余额") : "方案"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function PlanSection({ account }) {
  const [plans, setPlans] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    getPublicPlans().then(setPlans).catch(() => setPlans({ items: [], checkout: { available: false } }));
    getMyTransactions().then((d) => setTransactions(d.items)).catch(() => {});
  }, []);

  const choose = (plan) => {
    setNotice(plans.checkout.available ? null : `${plans.checkout.message}（想要开通：${plan.name}）`);
  };

  return (
    <>
      <QuotaCard account={account} />
      {notice && <div className="callout info"><Icon name="bulb" size={16} /><span>{notice}</span></div>}
      <div className="plan-grid">
        {(plans?.items || []).map((p) => {
          const current = account?.plan.id === p.id;
          return (
            <div key={p.id} className={`plan-card ${current ? "current" : ""}`}>
              <div className="plan-card-head">
                <strong>{p.name}</strong>
                {current && <span className="plan-badge">当前方案</span>}
              </div>
              <div className="plan-price">{p.price_cents ? <>{formatYuan(p.price_cents)}<small> / 月</small></> : "免费"}</div>
              <p>{p.description}</p>
              <ul>
                {p.features.map((f) => <li key={f}><Icon name="check" size={14} /> {f}</li>)}
              </ul>
              <button type="button" className={current ? "btn-secondary" : "btn-primary"} disabled={current} onClick={() => choose(p)}>
                {current ? "使用中" : "选择此方案"}
              </button>
            </div>
          );
        })}
      </div>
      <div className="settings-card">
        <h4>账单记录</h4>
        <table className="mini-table">
          <thead><tr><th>时间</th><th>类型</th><th>内容</th><th>点数</th><th>金额</th><th>方式</th></tr></thead>
          <tbody>
            {transactions.map((t) => (
              <tr key={t.id}>
                <td>{fmtTime(t.created_at)}</td>
                <td>{TX_LABEL[t.kind] || t.kind}</td>
                <td>{t.note || t.plan_name || "—"}</td>
                <td>{t.credits > 0 ? "+" : ""}{formatCredits(t.credits)}</td>
                <td>{t.amount_cents ? formatYuan(t.amount_cents) : "—"}</td>
                <td>{t.channel === "manual" ? "管理员开通" : t.channel === "wechat" ? "微信支付" : "支付宝"}</td>
              </tr>
            ))}
            {transactions.length === 0 && <tr><td colSpan={6} className="muted">暂无账单</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon";
import { getMarket, setMarketItem } from "../../api";
import { DISCOVER_SECTIONS, formatCredits } from "../../navigation";
import { extractErrorMessage } from "../../utils/format";

const KIND_LABEL = { model: "模型", skill: "技能", tool: "工具" };
const ADMIN_TARGET = { model: "models", skill: "skills", tool: "tools" };

function ItemCard({ item, onToggle, busy }) {
  const meta = item.meta || {};
  return (
    <article className={`market-card ${item.enabled ? "" : "off"}`}>
      <header>
        <span className={`market-icon ${item.kind}`}>
          <Icon name={item.kind === "model" ? "database" : item.kind === "skill" ? "bulb" : "code"} size={18} />
        </span>
        <div className="market-title">
          <strong>{item.name}</strong>
          <small>{KIND_LABEL[item.kind]}{item.kind === "model" && meta.model ? ` · ${meta.model}` : ""}</small>
        </div>
        <label className={`switch small ${item.locked ? "locked" : ""}`} title={item.locked ? "系统默认，始终可用" : item.enabled ? "停用" : "启用"}>
          <input type="checkbox" checked={item.enabled} disabled={item.locked || busy} onChange={(e) => onToggle(item, e.target.checked)} />
          <span className="switch-track" />
        </label>
      </header>
      <p>{item.description || "暂无介绍"}</p>
      <footer>
        {item.badges.map((b) => <span key={b} className="tag">{b}</span>)}
        {item.kind === "model" && (
          <span className="market-price">
            {meta.context_window ? `${Math.round(meta.context_window / 1000)}K 上下文 · ` : ""}
            输入 {formatCredits(meta.price_input)} / 输出 {formatCredits(meta.price_output)} 点每 1K token
          </span>
        )}
        {item.kind === "skill" && meta.tools?.length > 0 && <span className="market-price">会用到 {meta.tools.length} 个工具</span>}
        {item.locked && <span className="market-price"><Icon name="lock" size={12} /> {item.kind === "model" ? "系统默认模型" : "内置能力"}</span>}
      </footer>
    </article>
  );
}

/**
 * 发现：浏览、启用 / 停用模型、技能、工具。
 * 目录目前来自本系统已上架的资源；接入 AI 盒子后，盒子提供的条目会出现在同一目录中。
 */
export default function DiscoverView({ section, navSlot, onNavigate, user, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [onlyEnabled, setOnlyEnabled] = useState(false);
  const [busyKey, setBusyKey] = useState(null);

  useEffect(() => {
    getMarket().then(setData).catch((err) => setError(extractErrorMessage(err)));
  }, []);

  const current = DISCOVER_SECTIONS.find((s) => s.id === section) || DISCOVER_SECTIONS[0];
  const all = useMemo(() => (data ? [...data.models, ...data.skills, ...data.tools, ...(data.external.items || [])] : []), [data]);
  const counts = useMemo(() => Object.fromEntries(DISCOVER_SECTIONS.map((s) => [s.id, all.filter((i) => !s.kind || i.kind === s.kind).length])), [all]);
  const q = query.trim().toLowerCase();
  const items = all.filter((i) => (!current.kind || i.kind === current.kind)
    && (!onlyEnabled || i.enabled)
    && (!q || `${i.name}${i.description}${(i.badges || []).join("")}`.toLowerCase().includes(q)));

  const toggle = async (item, enabled) => {
    setBusyKey(`${item.kind}:${item.key}`);
    setError(null);
    try {
      const saved = await setMarketItem(item.kind, item.key, enabled);
      const listKey = `${item.kind}s`;
      setData((prev) => ({ ...prev, [listKey]: prev[listKey].map((i) => (i.key === item.key ? { ...i, enabled: saved.enabled } : i)) }));
      onChanged?.();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusyKey(null);
    }
  };

  const nav = (
    <nav className="module-nav-list">
      <div className="side-section">
        <div className="side-label">分类</div>
        {DISCOVER_SECTIONS.map((s) => (
          <button type="button" key={s.id} className={`side-item ${s.id === current.id ? "active" : ""}`} onClick={() => onNavigate(s.id)}>
            <Icon name={s.icon} size={16} /> <span className="side-text">{s.label}</span>
            {data && <span className="side-badge">{counts[s.id]}</span>}
          </button>
        ))}
      </div>
      <div className="side-section">
        <div className="side-label">来源</div>
        <div className="side-item static"><Icon name="database" size={16} /><span className="side-text">本系统</span><span className="status-dot on" /></div>
        <div className="side-item static muted" title="接入后，盒子里的本地大模型、技能和工具会出现在这里">
          <Icon name="link" size={16} /><span className="side-text">AI 盒子</span><span className="side-badge">未接入</span>
        </div>
      </div>
      {user.role === "admin" && (
        <div className="side-section">
          <div className="side-label">管理</div>
          {Object.entries(ADMIN_TARGET).map(([kind, target]) => (
            <a key={kind} className="side-item muted" href={`#/settings/${target}`}>
              <Icon name="settings" size={16} /><span className="side-text">上架{KIND_LABEL[kind]}</span>
            </a>
          ))}
        </div>
      )}
    </nav>
  );

  return (
    <div className="admin-view discover-view">
      {navSlot && createPortal(nav, navSlot)}
      <header className="admin-head">
        <div>
          <h1><Icon name="compass" size={22} /> {current.id === "all" ? "发现" : current.label}</h1>
          <p className="muted">浏览可用的模型、技能和工具，按需启用或停用（只影响你自己）。</p>
        </div>
      </header>

      <div className="discover-toolbar">
        <div className="wiki-search grow">
          <Icon name="search" size={15} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`搜索${current.id === "all" ? "模型、技能、工具" : current.label}…`} />
        </div>
        <label className="check-inline">
          <input type="checkbox" checked={onlyEnabled} onChange={(e) => setOnlyEnabled(e.target.checked)} /> 只看已启用
        </label>
      </div>

      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {!data && !error && <span className="spinner" />}
      {data && (
        <>
          <div className="market-grid">
            {items.map((item) => (
              <ItemCard key={`${item.kind}:${item.key}`} item={item} onToggle={toggle} busy={busyKey === `${item.kind}:${item.key}`} />
            ))}
          </div>
          {items.length === 0 && <p className="side-empty">没有符合条件的条目</p>}
          {!data.external.connected && (
            <div className="market-banner">
              <Icon name="link" size={20} />
              <div>
                <strong>AI 盒子尚未接入</strong>
                <span>接入后，盒子里部署的本地大模型、技能和工具会出现在这里，可以一键启用；数据不出企业内网。</span>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

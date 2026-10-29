import { useState } from "react";
import { createPortal } from "react-dom";
import Icon from "../Icon";
import MemoryView from "../MemoryView";
import ModelsPanel from "../admin/ModelsPanel";
import SkillsPanel from "../admin/SkillsPanel";
import ToolsPanel from "../admin/ToolsPanel";
import { AuditPanel, SystemPanel, UsersPanel } from "../admin/AdminPanels";
import { SETTINGS_GROUPS, visibleSections } from "../../navigation";
import { AboutSection, AccountSection, GeneralSection, PrivacySection } from "./PersonalSections";
import { PlanSection, UsageSection } from "./BillingSections";
import BillingAdminPanel from "./BillingAdminPanel";

/**
 * 整页设置：个人设置、用量与账单、管理（仅管理员）合在一处。
 * 分区导航（含搜索）渲染在侧边栏，页面只显示当前分区。
 */
export default function SettingsView({ section, navSlot, onNavigate, user, account, sourceInfo, models, modelId,
  onModelChange, defaultMode, onDefaultModeChange, onPasswordChanged, onChatsDeleted }) {
  const [query, setQuery] = useState("");
  const sections = visibleSections(user);
  const current = sections.find((s) => s.id === section) || sections[0];
  const q = query.trim().toLowerCase();
  const matched = q ? sections.filter((s) => `${s.label}${s.hint}`.toLowerCase().includes(q)) : sections;

  const nav = (
    <nav className="module-nav-list settings-sidenav">
      <div className="wiki-search">
        <Icon name="search" size={15} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索设置…" aria-label="搜索设置" />
      </div>
      {SETTINGS_GROUPS.map((g) => {
        const items = matched.filter((s) => s.group === g.id);
        if (!items.length) return null;
        return (
          <div key={g.id} className="side-section">
            <div className="side-label">{g.label}</div>
            {items.map((s) => (
              <button type="button" key={s.id} className={`side-item ${s.id === current.id ? "active" : ""}`} onClick={() => onNavigate(s.id)}>
                <Icon name={s.icon} size={16} /> <span className="side-text">{s.label}</span>
              </button>
            ))}
          </div>
        );
      })}
      {matched.length === 0 && <p className="side-empty">没有匹配的设置</p>}
    </nav>
  );

  return (
    <div className="admin-view settings-view">
      {navSlot && createPortal(nav, navSlot)}
      <header className="admin-head">
        <div>
          <h1><Icon name={current.icon} size={22} /> {current.label}</h1>
          <p className="muted">{current.hint}</p>
        </div>
      </header>
      {current.id === "general" && (
        <GeneralSection models={models} modelId={modelId} onModelChange={onModelChange}
          defaultMode={defaultMode} onDefaultModeChange={onDefaultModeChange} />
      )}
      {current.id === "account" && <AccountSection user={user} account={account} onPasswordChanged={onPasswordChanged} />}
      {current.id === "privacy" && <PrivacySection onOpenMemory={() => onNavigate("memory")} onChatsDeleted={onChatsDeleted} />}
      {current.id === "memory" && <MemoryView embedded />}
      {current.id === "usage" && <UsageSection account={account} />}
      {current.id === "plan" && <PlanSection account={account} />}
      {current.id === "members" && <UsersPanel currentUser={user} />}
      {current.id === "billing-admin" && <BillingAdminPanel />}
      {current.id === "models" && <ModelsPanel />}
      {current.id === "skills" && <SkillsPanel />}
      {current.id === "tools" && <ToolsPanel />}
      {current.id === "audit" && <AuditPanel />}
      {current.id === "system" && <SystemPanel />}
      {current.id === "about" && <AboutSection sourceInfo={sourceInfo} />}
    </div>
  );
}

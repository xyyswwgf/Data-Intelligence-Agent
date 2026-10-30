import { useEffect, useState } from "react";
import Icon from "../Icon";
import { listIndustryPacks, setIndustryPack } from "../../api";
import { extractErrorMessage } from "../../utils/format";

/** 设置 → 管理 → 行业包：选择平台当前面向的行业（推荐问题、专属分析、指标口径随之切换） */
export default function PacksPanel({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    listIndustryPacks().then(setData).catch((err) => setError(extractErrorMessage(err)));
  }, []);

  const activate = async (code) => {
    setBusy(code);
    setError(null);
    try {
      await setIndustryPack(code);
      setData(await listIndustryPacks());
      onChanged?.();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="callout info">
        <Icon name="bulb" size={16} />
        <span>
          平台核心（智能助手、SQL 查询、知识库、导入表分析）与行业无关；行业包提供该行业的指标口径、专属分析流程、
          首页推荐问题和专属数据导入。切换后对所有成员立即生效，已有数据和知识库不受影响。
        </span>
      </div>
      {error && <div className="callout danger"><Icon name="alert" size={16} /><span>{error}</span></div>}
      {!data && !error && <span className="spinner" />}
      <div className="plan-grid">
        {(data?.items || []).map((p) => (
          <div key={p.code} className={`plan-card ${p.active ? "current" : ""}`}>
            <div className="plan-card-head">
              <strong>{p.name}</strong>
              {p.active && <span className="plan-badge">使用中</span>}
            </div>
            <p>{p.description}</p>
            <ul>
              <li><Icon name="check" size={14} /> {p.analysis_label ? `专属分析：${p.analysis_label}` : "分析导入的任意数据表"}</li>
              <li><Icon name="check" size={14} /> {Object.values(p.suggestions).flat().length} 个推荐问题</li>
              {p.store_import && <li><Icon name="check" size={14} /> 门店数据导入（台账、收款、ERP、指导书）</li>}
            </ul>
            <button type="button" className={p.active ? "btn-secondary" : "btn-primary"} disabled={p.active || busy === p.code}
              onClick={() => activate(p.code)}>
              {p.active ? "使用中" : busy === p.code ? "切换中…" : "切换到此行业包"}
            </button>
          </div>
        ))}
      </div>
    </>
  );
}

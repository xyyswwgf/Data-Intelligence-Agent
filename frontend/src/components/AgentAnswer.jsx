import ReactECharts from "echarts-for-react";
import Icon from "./Icon";
import ResultChart from "./ResultChart";
import ResultTable from "./ResultTable";
import { downloadAnalysisReport } from "../api";
import { CHART_THEME } from "../utils/echartsTheme";
import { formatCell, formatMetric, formatPercent } from "../utils/format";

// ---------------------------------------------------------------------------
// 指标卡片
// ---------------------------------------------------------------------------

const LOWER_IS_BETTER = /退款|成本|花费|缺失|异常/;

function MetricGrid({ metrics }) {
  if (!metrics?.length) return null;
  return (
    <div className="metric-grid">
      {metrics.map((metric) => {
        const hasDelta = metric.change_rate !== null && metric.change_rate !== undefined;
        const up = Number(metric.change) >= 0;
        // 成本、退款类指标"越低越好"：上升标红、下降标绿
        const good = LOWER_IS_BETTER.test(metric.name) ? !up : up;
        return (
          <div className="metric-card" key={metric.name}>
            <span className="metric-name">{metric.name}</span>
            <strong>{formatMetric(metric.current, metric.name)}</strong>
            {hasDelta && (
              <span className={`delta ${good ? "up" : "down"}`}>
                {up ? "↑" : "↓"} {formatPercent(Math.abs(metric.change_rate))}
                <small>{metric.comparison || "环比"}</small>
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 图表
// ---------------------------------------------------------------------------

function AgentCharts({ charts }) {
  if (!charts?.length) return null;
  return (
    <div className={`chart-grid ${charts.length === 1 ? "single" : ""}`}>
      {charts.map((chart, index) => (
        <div className={`panel chart-panel ${index === 0 && charts.length > 2 ? "wide" : ""}`} key={`${chart.title}-${index}`}>
          <h4 className="panel-title">
            <Icon name={chart.type === "line" ? "trend" : "chart"} size={15} /> {chart.title}
          </h4>
          <ReactECharts
            option={chart.echarts_option}
            theme={CHART_THEME}
            style={{ height: 280, width: "100%" }}
            notMerge
            lazyUpdate
          />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 归因
// ---------------------------------------------------------------------------

// 对比型归因（上期 / 本期 / 变化）
const COMPARE_SECTIONS = [
  { key: "region", title: "地区 GMV 归因", label: "省份", value: "change", icon: "map" },
  { key: "category", title: "品类 GMV 归因", label: "品类", value: "change", icon: "chart" },
  { key: "refund", title: "退款率排行", label: "品类", value: "current", percent: true, icon: "refund" },
  { key: "roi", title: "渠道 ROI 变化", label: "渠道", value: "change", icon: "coins" },
  { key: "conversion_region", title: "转化率变化", label: "渠道", value: "change", percent: true, icon: "target" },
  { key: "trend", title: "周期环比变化", label: "周期", value: "change", icon: "trend" },
];

// 明细型结果（CSV 通用 Agent），直接按行列展示
const TABLE_SECTIONS = {
  group_compare: "分组对比",
  missing: "字段缺失",
  anomaly: "异常值候选",
};

function CompareCard({ section, rows, focusRegion }) {
  const title = section.key === "conversion_region" && focusRegion ? `${focusRegion}${section.title}` : section.title;
  return (
    <div className="panel">
      <h4 className="panel-title">
        <Icon name={section.icon} size={15} /> {title}
      </h4>
      <table className="mini-table">
        <thead>
          <tr>
            <th>{section.label}</th>
            <th>上期</th>
            <th>本期</th>
            <th>{section.value === "current" ? "当前值" : "变化"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 5).map((row, index) => {
            const value = row[section.value];
            const num = Number(value);
            const tone = section.value === "current" || !num ? "" : num > 0 ? "up" : "down";
            return (
              <tr key={`${row[section.label]}-${index}`}>
                <td>{row[section.label]}</td>
                <td>{section.percent ? formatPercent(row.previous) : formatMetric(row.previous)}</td>
                <td>{section.percent ? formatPercent(row.current) : formatMetric(row.current)}</td>
                <td className={`num ${tone}`}>{section.percent ? formatPercent(value) : formatMetric(value)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RowsCard({ title, rows }) {
  const columns = Object.keys(rows[0]);
  return (
    <div className="panel">
      <h4 className="panel-title">
        <Icon name="table" size={15} /> {title}
      </h4>
      <table className="mini-table">
        <thead>
          <tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 10).map((row, index) => (
            <tr key={index}>
              {columns.map((col) => (
                <td key={col}>{col.includes("率") ? formatPercent(row[col]) : formatCell(row[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AttributionPanel({ attribution }) {
  if (!attribution) return null;
  const compareCards = COMPARE_SECTIONS.filter((section) => attribution[section.key]?.length).map((section) => (
    <CompareCard key={section.key} section={section} rows={attribution[section.key]} focusRegion={attribution.focus_region} />
  ));
  const tableCards = Object.entries(TABLE_SECTIONS)
    .filter(([key]) => attribution[key]?.length)
    .map(([key, title]) => <RowsCard key={key} title={title} rows={attribution[key]} />);
  if (!compareCards.length && !tableCards.length) return null;
  return (
    <div className="panel-grid">
      {compareCards}
      {tableCards}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 报告 / 知识 / SQL
// ---------------------------------------------------------------------------

function Insights({ report }) {
  if (!report?.findings?.length && !report?.recommendations?.length) return null;
  return (
    <div className="insight-grid">
      <div className="panel">
        <h4 className="panel-title">
          <Icon name="sparkles" size={15} /> 关键发现
        </h4>
        <ul className="insight-list">
          {report.findings?.map((item, index) => <li key={index}>{item}</li>)}
        </ul>
      </div>
      <div className="panel accent">
        <h4 className="panel-title">
          <Icon name="bulb" size={15} /> 建议动作
        </h4>
        <ol className="insight-list numbered">
          {report.recommendations?.map((item, index) => <li key={index}>{item}</li>)}
        </ol>
      </div>
    </div>
  );
}

function Evidence({ knowledge, queries }) {
  if (!knowledge?.length && !queries?.length) return null;
  return (
    <div className="evidence">
      {knowledge?.length > 0 && (
        <details className="evidence-item">
          <summary>
            <Icon name="bulb" size={14} /> 引用的业务知识 <span className="count">{knowledge.length}</span>
          </summary>
          {knowledge.map((item, index) => (
            <p key={index}>
              <code>{item.source}</code> {item.content}
            </p>
          ))}
        </details>
      )}
      {queries?.length > 0 && (
        <details className="evidence-item">
          <summary>
            <Icon name="code" size={14} /> SQL 执行记录 <span className="count">{queries.length}</span>
          </summary>
          {queries.map((query, index) => (
            <div className="sql-trace" key={`${query.id}-${index}`}>
              <div className="sql-trace-head">
                {query.purpose}
                <span>
                  {query.row_count} 行{query.truncated ? " · 已截断" : ""}
                </span>
              </div>
              <pre className="sql-code">{query.sql}</pre>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 对外组件
// ---------------------------------------------------------------------------

export function AgentAnswer({ result }) {
  const report = result.report || {};
  const failed = result.errors?.length > 0;
  return (
    <div className="answer">
      {failed && (
        <div className="callout danger">
          <Icon name="alert" size={16} />
          <span>分析未完成：{result.errors.map((e) => `[${e.node}] ${e.message}`).join("；")}</span>
        </div>
      )}
      {report.summary && (
        <div className="summary">
          <p>{report.summary}</p>
          <div className="summary-meta">
            {result.period?.current && (
              <span className="tag">
                {result.period.previous} → {result.period.current}
              </span>
            )}
            {result.table_name && <span className="tag">{result.table_name}</span>}
            {result.run_id && (
              <span className="export-links">
                <button type="button" onClick={() => downloadAnalysisReport(result.run_id, "markdown")}>
                  <Icon name="download" size={13} /> Markdown
                </button>
                <button type="button" onClick={() => downloadAnalysisReport(result.run_id, "html")}>
                  <Icon name="download" size={13} /> HTML
                </button>
              </span>
            )}
          </div>
        </div>
      )}
      <MetricGrid metrics={result.metrics} />
      <AgentCharts charts={result.charts} />
      <Insights report={report} />
      <AttributionPanel attribution={result.attribution} />
      <Evidence knowledge={result.knowledge} queries={result.sql_queries} />
    </div>
  );
}

export function QueryAnswer({ result }) {
  return (
    <div className="answer">
      <div className="summary">
        <p>
          查询返回 <strong>{result.row_count}</strong> 行结果
          {result.truncated ? "（超过上限，已截断）" : ""}。
        </p>
        {result.attempts > 1 && (
          <div className="summary-meta">
            <span className="tag warn">首次 SQL 执行报错，已根据数据库错误自动修正（共 {result.attempts} 次）</span>
          </div>
        )}
      </div>
      <ResultChart columns={result.columns} data={result.data} chartConfig={result.chartConfig} />
      <ResultTable columns={result.columns} data={result.data} sql={result.sql} truncated={result.truncated} />
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import Composer from "./components/Composer";
import Sidebar from "./components/Sidebar";
import DataDrawer from "./components/DataDrawer";
import Icon from "./components/Icon";
import { AgentAnswer, QueryAnswer } from "./components/AgentAnswer";
import { CompletedSteps, PendingSteps } from "./components/ThinkingSteps";
import { detectChartConfig } from "./utils/chartDetector";
import { extractErrorMessage } from "./utils/format";
import {
  queryNL,
  analyzeQuestion,
  analyzeTableQuestion,
  getAnalysisHistory,
  getAnalysisDetail,
  apiClient,
} from "./api";
import "./App.css";

const SUGGESTIONS = {
  agent: [
    { icon: "trend", title: "本月销售额为什么下降？", desc: "趋势 + 地区 / 品类 / 渠道归因" },
    { icon: "map", title: "哪些地区拖累了整体利润？", desc: "定位下滑省份与转化漏斗" },
    { icon: "coins", title: "哪些渠道 ROI 变差？", desc: "净销售额 vs 营销花费" },
    { icon: "refund", title: "哪些商品退款率异常升高？", desc: "品类退款率排行" },
  ],
  table: [
    { icon: "trend", title: "这个表按月份有什么变化？", desc: "自动识别时间列与指标" },
    { icon: "chart", title: "哪个类别的数量最多？", desc: "按维度分组对比" },
    { icon: "alert", title: "这个表里有什么异常数据？", desc: "z-score 异常值初筛" },
    { icon: "table", title: "哪些字段缺失比较严重？", desc: "数据质量检查" },
  ],
  query: [
    { icon: "code", title: "每个品类的销售额是多少？", desc: "分组聚合" },
    { icon: "trend", title: "按月统计订单数和 GMV", desc: "时间序列" },
    { icon: "map", title: "销售额最高的 5 个省份", desc: "排序 + LIMIT" },
    { icon: "coins", title: "各渠道的营销花费", desc: "多表关联" },
  ],
};

function greeting() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

let turnSeq = 0;
const nextTurnId = () => `turn-${Date.now()}-${turnSeq++}`;

// ── 单轮对话 ──
function Turn({ turn, innerRef }) {
  const kind = turn.mode === "query" ? "query" : turn.table ? "table" : "agent";
  return (
    <article className="turn" ref={innerRef}>
      <div className="bubble-row">
        <div className="bubble">
          {turn.table && turn.mode === "agent" && (
            <span className="bubble-tag">
              <Icon name="table" size={12} /> {turn.table}
            </span>
          )}
          {turn.question}
        </div>
      </div>
      <div className="agent-row">
        <span className={`orb orb-xs ${turn.status === "pending" ? "spinning" : ""}`} />
        <div className="agent-body">
          <div className="agent-name">
            DataMind <span>{turn.mode === "query" ? "SQL 查询" : turn.table ? "CSV 通用分析" : "经营分析 Agent"}</span>
          </div>
          {turn.status === "pending" && <PendingSteps kind={kind} />}
          {turn.status === "error" && (
            <div className="callout danger">
              <Icon name="alert" size={16} />
              <span>{turn.error}</span>
            </div>
          )}
          {turn.status === "done" && turn.mode === "agent" && (
            <>
              <CompletedSteps steps={turn.result.steps} />
              <AgentAnswer result={turn.result} />
            </>
          )}
          {turn.status === "done" && turn.mode === "query" && <QueryAnswer result={turn.result} />}
        </div>
      </div>
    </article>
  );
}

// ── 主 App ──
function App() {
  const [mode, setMode] = useState("agent");
  const [turns, setTurns] = useState([]);
  const [historyItems, setHistoryItems] = useState([]);
  const [targetTable, setTargetTable] = useState(null);
  const [sourceInfo, setSourceInfo] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const lastTurnRef = useRef(null);

  const loading = turns.some((t) => t.status === "pending");

  const fetchInfo = useCallback(() => {
    apiClient.get("/api/info").then((res) => setSourceInfo(res.data)).catch(() => setSourceInfo(null));
  }, []);

  const fetchHistory = useCallback(() => {
    // 分析历史是增强能力，后端未就绪时不阻断主流程
    getAnalysisHistory().then((res) => setHistoryItems(res.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    fetchInfo();
    fetchHistory();
  }, [fetchInfo, fetchHistory]);

  // 新提问 / 回答返回时，把最新一轮对话滚到视口顶部，让回答自然向下展开
  const lastStatus = turns.length ? turns[turns.length - 1].status : null;
  useEffect(() => {
    lastTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length, lastStatus]);

  const updateTurn = (id, patch) => setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  const ask = async (question, options = {}) => {
    if (loading) return;
    const activeMode = options.mode || mode;
    const table = options.table !== undefined ? options.table : targetTable;
    const id = nextTurnId();
    setTurns((prev) => [...prev, { id, question, mode: activeMode, table: activeMode === "agent" ? table : null, status: "pending" }]);

    try {
      if (activeMode === "agent") {
        const data = table ? await analyzeTableQuestion(table, question) : await analyzeQuestion(question);
        updateTurn(id, { status: "done", result: data });
        fetchHistory();
      } else {
        const data = await queryNL(question);
        updateTurn(id, { status: "done", result: { ...data, chartConfig: detectChartConfig(data.columns, data.data) } });
      }
    } catch (err) {
      updateTurn(id, { status: "error", error: extractErrorMessage(err) });
    }
  };

  const handleNewChat = () => {
    if (loading) return;
    setTurns([]);
    setSidebarOpen(false);
  };

  const handleSelectTable = (tableName) => {
    setTargetTable(tableName);
    setMode("agent");
    setSidebarOpen(false);
  };

  const handleQueryTable = (tableName) => {
    setDrawerOpen(false);
    setTargetTable(null);
    setMode("query");
    ask(`查看 ${tableName} 表的前 100 行数据`, { mode: "query", table: null });
  };

  const handleAnalyzeTable = (tableName) => {
    setDrawerOpen(false);
    handleSelectTable(tableName);
  };

  const handleSelectHistory = async (runId) => {
    if (loading) return;
    setSidebarOpen(false);
    try {
      const data = await getAnalysisDetail(runId);
      // 回看 CSV 表的分析时，同步切到该表，后续追问仍针对同一张表
      setTargetTable(data.table_name || null);
      setMode("agent");
      setTurns((prev) => [
        ...prev,
        { id: nextTurnId(), question: data.question, mode: "agent", table: data.table_name || null, status: "done", result: data },
      ]);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        { id: nextTurnId(), question: "查看历史分析", mode: "agent", table: null, status: "error", error: extractErrorMessage(err) },
      ]);
    }
  };

  const activeRunId = [...turns].reverse().find((t) => t.result?.run_id)?.result.run_id;
  const suggestionKey = mode === "query" ? "query" : targetTable ? "table" : "agent";

  const composer = (
    <Composer
      onSubmit={(q) => ask(q)}
      loading={loading}
      mode={mode}
      onModeChange={setMode}
      targetTable={targetTable}
      onClearTable={() => setTargetTable(null)}
      onOpenData={() => setDrawerOpen(true)}
      autoFocus
    />
  );

  return (
    <div className="shell">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNewChat={handleNewChat}
        onOpenData={() => setDrawerOpen(true)}
        tables={sourceInfo?.tables}
        targetTable={targetTable}
        onSelectTable={handleSelectTable}
        history={historyItems}
        activeRunId={activeRunId}
        onSelectHistory={handleSelectHistory}
        sourceInfo={sourceInfo}
      />

      <main className="main">
        <header className="topbar">
          <button type="button" className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
            <Icon name="menu" size={20} />
          </button>
          <div className="topbar-title">
            {targetTable ? (
              <>
                <Icon name="table" size={15} /> {targetTable}
              </>
            ) : mode === "query" ? (
              <>
                <Icon name="code" size={15} /> 自然语言查询
              </>
            ) : (
              <>
                <Icon name="sparkles" size={15} /> 经营分析 Agent
              </>
            )}
          </div>
          <button type="button" className="ghost-btn" onClick={() => setDrawerOpen(true)}>
            <Icon name="database" size={15} /> 数据源
          </button>
        </header>

        {turns.length === 0 ? (
          <section className="hero">
            <div className="hero-glow" aria-hidden="true" />
            <span className="orb orb-lg" />
            <h1>
              {greeting()}，<span className="gradient-text">今天想分析什么？</span>
            </h1>
            <p className="hero-sub">
              {targetTable
                ? `已选中 ${targetTable}，Agent 会自动识别时间列、指标列和维度列`
                : "用一句话描述业务问题，Agent 会规划、查询、归因，并生成图表和报告"}
            </p>
            <div className="hero-composer">{composer}</div>
            <div className="suggestions">
              {SUGGESTIONS[suggestionKey].map((s) => (
                <button type="button" key={s.title} className="suggestion" onClick={() => ask(s.title)} disabled={loading}>
                  <span className="suggestion-icon">
                    <Icon name={s.icon} size={16} />
                  </span>
                  <strong>{s.title}</strong>
                  <small>{s.desc}</small>
                </button>
              ))}
            </div>
            <div className="capabilities">
              <span>LangGraph 工作流</span>
              <span>只读 SQL 网关</span>
              <span>多维归因</span>
              <span>ECharts 图表</span>
              <span>CSV 自动识别</span>
            </div>
          </section>
        ) : (
          <>
            <section className="thread">
              {turns.map((turn, index) => (
                <Turn key={turn.id} turn={turn} innerRef={index === turns.length - 1 ? lastTurnRef : null} />
              ))}
            </section>
            <div className="dock">
              {composer}
              <p className="dock-hint">Enter 发送 · Shift + Enter 换行 · 所有 SQL 均在只读事务中执行</p>
            </div>
          </>
        )}
      </main>

      <DataDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        info={sourceInfo}
        onImportComplete={fetchInfo}
        onQueryTable={handleQueryTable}
        onAnalyzeTable={handleAnalyzeTable}
      />
    </div>
  );
}

export default App;

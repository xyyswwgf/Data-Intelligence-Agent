import { useEffect, useRef } from "react";
import Composer from "./Composer";
import Icon from "./Icon";
import KbAnswer from "./KbAnswer";
import { AgentAnswer, QueryAnswer } from "./AgentAnswer";
import { CompletedSteps, PendingSteps } from "./ThinkingSteps";
import { AssistantFinal, ReasoningTrace } from "./AssistantAnswer";

const SUGGESTIONS = {
  assistant: [
    { icon: "trend", title: "6 月销售额最高的 3 个省份是哪些？", desc: "自己找表、写 SQL、核对口径" },
    { icon: "sparkles", title: "本月 GMV 为什么下降？给出三条建议", desc: "调用归因分析 + 结合知识库" },
    { icon: "book", title: "净销售额怎么算？上月是多少？", desc: "知识库口径 + 数据库取数" },
    { icon: "bulb", title: "记住：我负责华北区域，汇报用表格", desc: "写入长期记忆，之后自动参考" },
  ],
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
  kb: [
    { icon: "book", title: "GMV 和净销售额的口径是什么？", desc: "指标定义" },
    { icon: "map", title: "华北区域包括哪些省份？", desc: "业务规则" },
    { icon: "trend", title: "销售下降应该怎么分析？", desc: "分析方法论" },
    { icon: "coins", title: "渠道 ROI 怎么计算？", desc: "指标公式" },
  ],
  query: [
    { icon: "code", title: "每个品类的销售额是多少？", desc: "分组聚合" },
    { icon: "trend", title: "按月统计订单数和 GMV", desc: "时间序列" },
    { icon: "map", title: "销售额最高的 5 个省份", desc: "排序 + LIMIT" },
    { icon: "coins", title: "各渠道的营销花费", desc: "多表关联" },
  ],
};

const MODE_LABEL = { assistant: "智能助手", agent: "经营分析 Agent", table: "CSV 通用分析", kb: "知识问答", query: "SQL 查询" };

function greeting() {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 12) return "早上好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

function turnKind(turn) {
  if (turn.mode === "agent") return turn.table ? "table" : "agent";
  return turn.mode;
}

const HERO_SUB = {
  assistant: "我会自己规划步骤：查知识库、找数据表、写 SQL、做归因，并记住你的偏好",
  kb: "基于企业知识库回答，每个结论都标注来源页面，可点击核对原文",
  query: "用自然语言查数据，自动生成并校验只读 SQL",
};

function Turn({ turn, innerRef, onOpenPage }) {
  const kind = turnKind(turn);
  return (
    <article className="turn" ref={innerRef}>
      <div className="bubble-row">
        <div className="bubble">
          {kind === "table" && (
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
            DataMind <span>{MODE_LABEL[kind]}</span>
            {turn.model?.profile_name && (
              <span className={`model-chip ${turn.model.kind}`}>{turn.model.kind === "local" ? "本地" : "云端"} · {turn.model.profile_name}</span>
            )}
          </div>
          {turn.mode === "assistant" && (
            <ReasoningTrace events={turn.events || []} live={turn.status === "pending"} />
          )}
          {turn.status === "pending" && turn.mode !== "assistant" && <PendingSteps kind={kind} />}
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
          {turn.status === "done" && turn.mode === "kb" && <KbAnswer result={turn.result} onOpenPage={onOpenPage} />}
          {turn.status === "done" && turn.mode === "assistant" && (
            <AssistantFinal content={turn.content} citations={turn.citations} usage={turn.usage} onOpenPage={onOpenPage} />
          )}
        </div>
      </div>
    </article>
  );
}

export default function ChatView({
  turns, loading, mode, onModeChange, targetTable, onClearTable, onOpenData, onAsk, onOpenPage,
  models, modelId, onModelChange, skills, skill, onSkillChange,
}) {
  const lastTurnRef = useRef(null);
  const lastStatus = turns.length ? turns[turns.length - 1].status : null;

  // 新提问 / 回答返回时，把最新一轮对话滚到视口顶部，让回答自然向下展开
  useEffect(() => {
    lastTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length, lastStatus]);

  const suggestionKey = (mode === "agent" || mode === "assistant") && targetTable ? "table" : mode;
  const composer = (
    <Composer
      onSubmit={(q) => onAsk(q)}
      loading={loading}
      mode={mode}
      onModeChange={onModeChange}
      targetTable={targetTable}
      onClearTable={onClearTable}
      onOpenData={onOpenData}
      autoFocus
      models={models}
      modelId={modelId}
      onModelChange={onModelChange}
      skills={skills}
      skill={skill}
      onSkillChange={onSkillChange}
    />
  );

  if (turns.length === 0) {
    return (
      <section className="hero">
        <div className="hero-glow" aria-hidden="true" />
        <span className="orb orb-lg" />
        <h1>
          {greeting()}，<span className="gradient-text">今天想分析什么？</span>
        </h1>
        <p className="hero-sub">
          {HERO_SUB[mode] && !(mode === "assistant" && targetTable)
            ? HERO_SUB[mode]
            : targetTable
              ? `已选中 ${targetTable}，Agent 会自动识别时间列、指标列和维度列`
              : "用一句话描述业务问题，Agent 会规划、查询、归因，并生成图表和报告"}
        </p>
        <div className="hero-composer">{composer}</div>
        <div className="suggestions">
          {SUGGESTIONS[suggestionKey].map((s) => (
            <button type="button" key={s.title} className="suggestion" onClick={() => onAsk(s.title)} disabled={loading}>
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
          <span>RAG 知识库</span>
          <span>只读 SQL 网关</span>
          <span>多维归因</span>
          <span>CSV 自动识别</span>
        </div>
      </section>
    );
  }

  return (
    <>
      <section className="thread">
        {turns.map((turn, index) => (
          <Turn key={turn.id} turn={turn} onOpenPage={onOpenPage} innerRef={index === turns.length - 1 ? lastTurnRef : null} />
        ))}
      </section>
      <div className="dock">
        {composer}
        <p className="dock-hint">Enter 发送 · Shift + Enter 换行 · 所有 SQL 均在只读事务中执行</p>
      </div>
    </>
  );
}

import { useEffect, useRef } from "react";
import Composer from "./Composer";
import Icon from "./Icon";
import KbAnswer from "./KbAnswer";
import { AgentAnswer, QueryAnswer } from "./AgentAnswer";
import { CompletedSteps, PendingSteps } from "./ThinkingSteps";
import { AssistantFinal, ReasoningTrace } from "./AssistantAnswer";

// 与行业无关的推荐问题；行业相关的推荐问题由当前行业包提供（/api/info 中的 pack.suggestions）
const COMMON_SUGGESTIONS = {
  table: [
    { icon: "trend", title: "这个表按月份有什么变化？", desc: "自动识别时间列与指标" },
    { icon: "chart", title: "哪个类别的数量最多？", desc: "按维度分组对比" },
    { icon: "alert", title: "这个表里有什么异常数据？", desc: "z-score 异常值初筛" },
    { icon: "table", title: "哪些字段缺失比较严重？", desc: "数据质量检查" },
  ],
};

const MODE_LABEL = { assistant: "智能助手", agent: "专属分析", table: "表格分析", kb: "知识问答", query: "SQL 查询" };

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

function Turn({ turn, innerRef, onOpenPage, pack }) {
  const kind = turnKind(turn);
  const label = kind === "agent" && pack?.analysis_label ? pack.analysis_label : MODE_LABEL[kind];
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
            DataMind <span>{label}</span>
            {turn.model?.profile_name && (
              <span className={`model-chip ${turn.model.kind}`}>{turn.model.kind === "local" ? "本地" : "云端"} · {turn.model.profile_name}</span>
            )}
          </div>
          {turn.mode === "assistant" && (
            <ReasoningTrace events={turn.events || []} live={turn.status === "pending"} draft={turn.draft} />
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
          {turn.status === "pending" && turn.draft?.answer && (
            <AssistantFinal content={turn.draft.answer} streaming onOpenPage={onOpenPage} />
          )}
          {turn.status === "done" && turn.mode === "assistant" && (
            <AssistantFinal content={turn.content} citations={turn.citations} usage={turn.usage} onOpenPage={onOpenPage} />
          )}
        </div>
      </div>
    </article>
  );
}

export default function ChatView({
  pack, turns, loading, mode, onModeChange, targetTable, onClearTable, onOpenData, onAsk, onOpenPage,
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
      pack={pack}
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
          {(COMMON_SUGGESTIONS[suggestionKey] || pack?.suggestions?.[suggestionKey] || pack?.suggestions?.assistant || []).map((s) => (
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
          {pack && <span className="pack-chip">行业包 · {pack.name}</span>}
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
          <Turn key={turn.id} turn={turn} pack={pack} onOpenPage={onOpenPage} innerRef={index === turns.length - 1 ? lastTurnRef : null} />
        ))}
      </section>
      <div className="dock">
        {composer}
        <p className="dock-hint">Enter 发送 · Shift + Enter 换行 · 所有 SQL 均在只读事务中执行</p>
      </div>
    </>
  );
}

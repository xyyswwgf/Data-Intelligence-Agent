import { useEffect, useRef } from "react";
import Composer from "./Composer";
import Icon from "./Icon";
import KbAnswer from "./KbAnswer";
import { AgentAnswer, QueryAnswer } from "./AgentAnswer";
import { CompletedSteps, PendingSteps } from "./ThinkingSteps";
import { AssistantFinal, ReasoningTrace } from "./AssistantAnswer";

const SUGGESTIONS = {
  assistant: [
    { icon: "trend", title: "8 月各门店毛利和目标达成情况怎么样？", desc: "自己找表、写 SQL、核对口径" },
    { icon: "sparkles", title: "乐堤港为什么没达成？给出三条建议", desc: "调用门店分析 + 结合门店指导书" },
    { icon: "book", title: "Magic V Flip 2 的进店价和提成是多少？", desc: "知识库产品政策 + 数据库取数" },
    { icon: "bulb", title: "记住：我负责望京店，汇报金额用万元", desc: "写入长期记忆，之后自动参考" },
  ],
  agent: [
    { icon: "trend", title: "本月各门店经营怎么样？", desc: "销售额 / 毛利 / 手机台数 / 目标达成" },
    { icon: "target", title: "哪些门店和产品线没完成毛利目标？", desc: "定位差距最大的考核项" },
    { icon: "coins", title: "南锣店员的业绩和提成", desc: "店员毛利、个人目标达成" },
    { icon: "alert", title: "哪些门店长库龄库存最多？", desc: "库龄超过 90 天的商品" },
  ],
  table: [
    { icon: "trend", title: "这个表按月份有什么变化？", desc: "自动识别时间列与指标" },
    { icon: "chart", title: "哪个类别的数量最多？", desc: "按维度分组对比" },
    { icon: "alert", title: "这个表里有什么异常数据？", desc: "z-score 异常值初筛" },
    { icon: "table", title: "哪些字段缺失比较严重？", desc: "数据质量检查" },
  ],
  kb: [
    { icon: "book", title: "店长店提的档位和点位是怎么规定的？", desc: "店长店员考核" },
    { icon: "coins", title: "8 月 X80 Pro Max 的提成是多少？", desc: "产品政策" },
    { icon: "alert", title: "清尾机怎么计算分值？", desc: "清尾明细" },
    { icon: "map", title: "移动 79 元畅享套餐的红包和毛利", desc: "运营商政策" },
  ],
  query: [
    { icon: "code", title: "每个门店 8 月的销售额和毛利", desc: "分组聚合" },
    { icon: "trend", title: "每天卖了多少台手机", desc: "时间序列" },
    { icon: "map", title: "毛利最高的 5 个店员", desc: "排序 + LIMIT" },
    { icon: "coins", title: "各门店库龄超过 90 天的库存金额", desc: "多表关联" },
  ],
};

const MODE_LABEL = { assistant: "智能助手", agent: "门店经营分析", table: "CSV 通用分析", kb: "知识问答", query: "SQL 查询" };

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

import { useEffect, useState } from "react";
import Icon from "./Icon";

// 后端节点名 → 展示名
const STEP_LABELS = {
  planner: "规划分析路径",
  knowledge: "检索业务知识",
  sql: "生成分析 SQL",
  executor: "执行只读查询",
  analysis: "计算核心指标",
  attribution: "多维归因",
  visualization: "生成图表",
  report: "撰写分析报告",
  schema_profiler: "字段画像",
  intent: "识别分析意图",
  generic_sql: "生成分析 SQL",
  generic_analysis: "整理分析结果",
};

// 请求进行中时展示的预期步骤（后端一次性返回，这里按节奏推进给用户反馈）
const PENDING_PLANS = {
  agent: ["planner", "knowledge", "sql", "executor", "analysis", "attribution", "visualization", "report"],
  table: ["schema_profiler", "intent", "generic_sql", "executor", "generic_analysis", "visualization", "report"],
  query: ["understand", "generate", "guard", "execute"],
  kb: ["retrieve", "rerank", "compose"],
};
const QUERY_LABELS = {
  understand: "理解问题与表结构",
  generate: "生成 SQL",
  guard: "安全校验",
  execute: "只读执行查询",
  retrieve: "检索知识库（向量 + 关键词）",
  rerank: "融合排序，挑选证据",
  compose: "生成带引用的回答",
};

const labelOf = (name) => STEP_LABELS[name] || QUERY_LABELS[name] || name;

/** 请求进行中：步骤逐个点亮，最后一步保持"进行中"直到结果返回 */
export function PendingSteps({ kind }) {
  const plan = PENDING_PLANS[kind] || PENDING_PLANS.agent;
  const [active, setActive] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setActive((i) => Math.min(i + 1, plan.length - 1));
    }, 650);
    return () => clearInterval(timer);
  }, [plan.length]);

  return (
    <div className="thinking is-live">
      <div className="thinking-head">
        <span className="pulse-dot" />
        <span>正在思考</span>
        <span className="thinking-meta">{labelOf(plan[active])}…</span>
      </div>
      <ol className="step-list">
        {plan.map((name, i) => (
          <li key={name} className={i < active ? "done" : i === active ? "running" : "todo"}>
            <span className="step-mark">{i < active ? <Icon name="check" size={12} strokeWidth={2.6} /> : null}</span>
            <span className="step-label">{labelOf(name)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** 请求完成：可折叠的真实执行轨迹（来自后端 steps） */
export function CompletedSteps({ steps }) {
  if (!steps?.length) return null;
  const total = steps.reduce((sum, s) => sum + (s.elapsed_ms || 0), 0);
  const failed = steps.some((s) => s.status === "failed");
  return (
    <details className={`thinking ${failed ? "has-error" : ""}`}>
      <summary className="thinking-head">
        <Icon name={failed ? "alert" : "check"} size={14} strokeWidth={2.4} />
        <span>{failed ? "执行中断" : `已完成 ${steps.length} 个步骤`}</span>
        <span className="thinking-meta">用时 {total} ms</span>
        <Icon name="chevron" size={14} className="thinking-chevron" />
      </summary>
      <ol className="step-list detailed">
        {steps.map((step, i) => (
          <li key={`${step.name}-${i}`} className={step.status === "completed" ? "done" : step.status}>
            <span className="step-mark">
              {step.status === "completed" && <Icon name="check" size={12} strokeWidth={2.6} />}
              {step.status === "failed" && <Icon name="x" size={12} strokeWidth={2.6} />}
            </span>
            <div className="step-body">
              <div className="step-label">
                {labelOf(step.name)} <small>{step.elapsed_ms} ms</small>
              </div>
              <p>{step.summary}</p>
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

"""
推理 Agent — ReAct 循环（Reason → Act → Observe）+ 反思

  1. 组装上下文：系统指令 + 记忆 + 会话摘要 + 最近对话 + 问题（按 token 预算）
  2. 模型思考（原生 thinking / reasoning 或显式计划），决定调用哪些工具
  3. 执行工具（安全网关、只读角色约束不变），把精简结果作为「观察」回传
  4. 工具出错时把错误原文交给模型自我修正；达到步数上限时强制基于已有证据作答
  5. 回答落库，后台抽取长期记忆、按需压缩会话

全过程以事件流输出（SSE），前端实时展示推理轨迹：
  context / thinking / tool_call / tool_result / answer / done / error
未配置大模型时退化为规则路由（经营分析 / CSV 分析 / 知识检索），平台仍然可用。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Iterator

from backend.assistant import memory as memory_store
from backend.assistant import sessions
from backend.assistant.context import build_context, usage_report
from backend.assistant import skills as skill_store
from backend.assistant.tools import ToolContext, args_preview, execute, load_registry, tools_schema
from backend.config import AGENT_MAX_STEPS
from backend.llm_gateway import LLMError, describe, get_provider
from backend.market import disabled_keys
from backend.packs import active_pack

logger = logging.getLogger("nl2sql.assistant")

_SYSTEM = """你是 DataMind，企业内部的数据智能助手，服务于 {user_name}（角色：{role}）。今天是 {today}。

## 你的能力
你可以调用工具：检索企业知识库、查看数据表结构、执行只读 SQL、运行行业专属分析（如有）、分析用户导入的 CSV 表、记住用户的长期信息。

## 推理方式
1. 先想清楚：回答这个问题需要哪些事实？哪些来自知识库（口径、规则），哪些来自数据（数字）？
2. 用工具获取证据。不知道表结构时先 list_tables / describe_tables，再写 SQL；SQL 报错就根据报错修正后重试。
3. {pack_hint}导入的 CSV 表用 analyze_imported_table；口径、制度、规则类问题先 search_knowledge。
4. 证据足够就停止调用工具，不要重复查询同样的内容。最多 {max_steps} 步。
5. 业务数据不一定更新到今天。用户说「本月 / 上月 / 最近」时，先确认数据的实际时间范围；若数据未覆盖，
   按数据中最新的月份理解，并在回答里明确说明你采用的时间范围。
6. 回答前自检：数字是否都来自工具结果？口径是否与知识库一致？有没有回答到用户真正的问题？

## 回答要求
- 中文，先给结论，再分点说明依据；涉及数字必须来自工具结果，不得编造。
- 只有本轮 search_knowledge 返回的页面才能引用，在句末标注 [[页面slug]]；来自表注释或常识的内容不要标注引用。
- 证据不足时明确说明缺什么，并建议下一步。
- 用户让你记住什么，调用 remember 工具；结合记忆里的偏好调整回答方式。{target}"""


def _event(event_type: str, **data: Any) -> dict[str, Any]:
    return {"type": event_type, "ts": round(time.time(), 3), **data}


def _system_prompt(user: dict, target_table: str | None) -> str:
    target = f"\n\n当前用户正在分析导入表 {target_table}，涉及「这个表」时指的就是它。" if target_table else ""
    pack = active_pack()
    return _SYSTEM.format(user_name=user["display_name"], role=user["role"], today=date.today().isoformat(),
                          max_steps=AGENT_MAX_STEPS, target=target, pack_hint=pack.assistant_hint)


def run_turn(
    user: dict,
    session_id: str,
    question: str,
    target_table: str | None = None,
    profile_id: int | None = None,
    skill: str | None = None,
    remember: bool = True,
) -> Iterator[dict]:
    """
    执行一轮对话，逐步产出事件；最后一个事件是 done（含落库的消息）或 error。

    profile_id：本轮使用的模型配置（为空时沿用会话上次的模型，再退回默认模型）
    skill：用户手动指定的技能，指引直接注入本轮上下文
    remember：为 False 时本轮不读写长期记忆（准确率评测用，避免把评测问答写进管理员的记忆）
    """
    session = sessions.get_session(user["id"], session_id)
    if not session:
        yield _event("error", message="会话不存在")
        return
    question = question.strip()
    provider = get_provider(profile_id or session.get("model_profile_id"))
    if provider.profile_id and provider.profile_id != session.get("model_profile_id"):
        sessions.set_session_model(session_id, provider.profile_id)
    model_info = describe(provider)
    user_msg = sessions.add_message(session_id, "user", question, "assistant",
                                    payload={"table": target_table, "skill": skill, "model": model_info})
    if session["message_count"] == 0:
        title = sessions.auto_title(session_id, question)
        yield _event("session", title=title)
    yield _event("model", **model_info)

    use_memory = remember and memory_store.memory_enabled_for(user["id"])
    trace: list[dict] = []

    # 明确的「记住…」指令：不依赖大模型也能写入记忆
    explicit = memory_store.explicit_memory_request(question) if use_memory else None
    if explicit:
        saved = memory_store.save_memory(user["id"], explicit, "instruction" if "以后" in question else "fact",
                                         "explicit", session_id)
        ev = _event("memory", action=saved["action"], memory=saved["memory"])
        trace.append(ev)
        yield ev

    started = time.perf_counter()
    try:
        if provider.configured():
            gen = _react(provider, user, session, question, target_table, use_memory, trace, skill)
            answer, citations, payload, usage = yield from gen
        elif explicit:
            answer, citations, payload, usage = f"好的，已经记住：{explicit}", [], None, {"steps": 0, "mode": "rule"}
        else:
            gen = _fallback(user, session_id, question, target_table, trace)
            answer, citations, payload, usage = yield from gen
    except Exception as exc:
        logger.exception("智能助手执行失败")
        answer, citations, payload, usage = f"抱歉，这次处理失败了：{exc}", [], None, {}
        trace.append(_event("error", message=str(exc)))
        yield trace[-1]
    elapsed = int((time.perf_counter() - started) * 1000)
    message = sessions.add_message(
        session_id, "assistant", answer, "assistant",
        payload={"citations": citations, "result": payload, "usage": usage, "elapsed_ms": elapsed, "model": model_info},
        trace=trace,
    )
    yield _event("done", message_id=message["id"], seq=message["seq"], user_seq=user_msg["seq"],
                 content=answer, citations=citations, elapsed_ms=elapsed, usage=usage, model=model_info)

    # 轮次结束后的「后台整理」：抽取长期记忆、按需压缩会话（不阻塞本轮响应）
    if use_memory and not explicit and provider.configured():
        memory_store.extract_in_background(user["id"], session_id, question, answer, provider)
    try:
        sessions.maybe_compact(session_id, provider)
    except Exception:
        logger.exception("会话压缩失败")


def _react(provider, user, session, question, target_table, use_memory, trace, skill=None) -> Iterator[dict]:
    off_skills = disabled_keys(user["id"], "skill")
    available_skills = [s for s in skill_store.list_skills(enabled_only=True) if s["name"] not in off_skills]
    registry = load_registry(user["role"], use_memory, bool(available_skills), disabled_keys(user["id"], "tool"))
    specs = registry.specs
    system = _system_prompt(user, target_table) + skill_store.catalog_prompt(available_skills)
    if skill:
        chosen = skill_store.get_skill(skill)
        if chosen:
            # 用户手动指定的技能：指引直接注入，不需要模型再调用 use_skill
            system += "\n\n## 用户为本轮指定的技能（必须遵循）\n" + skill_store.render_instructions(chosen)
            ev = _event("skill", name=chosen["name"], title=chosen["title"], source="user")
            trace.append(ev)
            yield ev
    window = provider.settings.context_window
    ctx = build_context(system, tools_schema(specs), session, user["id"], question, use_memory, window=window)
    report = usage_report(ctx, window)
    report["tools"] = [s.name for s in specs]
    ev = _event("context", **report)
    trace.append(ev)
    yield ev

    tool_ctx = ToolContext(user["id"], session["id"], use_memory, target_table, role=user["role"])
    messages = list(ctx.messages)
    citations: list[dict] = []
    payload: dict[str, Any] = {"tables": [], "analyses": []}
    usage = {"input_tokens": 0, "output_tokens": 0, "steps": 0, "context": report["used"], "window": report["window"]}
    seen_calls: set[str] = set()

    for step in range(1, AGENT_MAX_STEPS + 2):
        final_round = step > AGENT_MAX_STEPS
        try:
            # 流式调用：文字边生成边推给前端（delta 事件不落库）；这一步若转为调用工具，前端丢弃草稿
            stream = provider.stream_chat(
                ctx.system, messages,
                tools=None if final_round else specs,
                thinking=True,  # 是否真正开启由 Provider 按 LLM_THINKING_BUDGET / LLM_REASONING_EFFORT 决定
            )
            while True:
                try:
                    channel, piece = next(stream)
                except StopIteration as stop:
                    resp = stop.value
                    break
                yield _event("delta", step=step, channel="answer" if channel == "text" else "reasoning", text=piece)
        except LLMError as exc:
            ev = _event("error", message=str(exc))
            trace.append(ev)
            yield ev
            return f"大模型调用失败：{exc}", citations, payload, usage
        usage["input_tokens"] += resp.input_tokens
        usage["output_tokens"] += resp.output_tokens
        usage["steps"] = step

        if resp.reasoning.strip():
            ev = _event("thinking", step=step, text=resp.reasoning.strip()[:4000])
            trace.append(ev)
            yield ev
        if resp.tool_calls and resp.text.strip():
            ev = _event("thinking", step=step, text=resp.text.strip()[:2000], kind="plan")
            trace.append(ev)
            yield ev

        if not resp.tool_calls:
            answer = _verify_references(resp.text.strip() or "（模型没有给出回答）", citations)
            return answer, _merge_citations(citations, answer), payload, usage

        messages.append({"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls,
                         "provider_blocks": resp.provider_blocks})
        for call in resp.tool_calls:
            key = f"{call.name}:{args_preview(call.arguments)}"
            ev = _event("tool_call", step=step, id=call.id, name=call.name, args=call.arguments)
            trace.append(ev)
            yield ev
            t0 = time.perf_counter()
            if key in seen_calls:
                result_text, ok, summary, display = "这个调用和之前完全相同，结果见上文，请直接基于已有结果继续。", False, "重复调用已跳过", {}
            else:
                seen_calls.add(key)
                result = execute(call.name, call.arguments, tool_ctx, registry)
                result_text, ok, summary, display = result.for_model, result.ok, result.summary, result.display
                citations.extend(display.get("citations", []))
                if "columns" in display:
                    payload["tables"].append(display)
                if "analysis" in display:
                    payload["analyses"].append(display["analysis"])
            ev = _event("tool_result", step=step, id=call.id, name=call.name, ok=ok, summary=summary,
                        elapsed_ms=int((time.perf_counter() - t0) * 1000), display=display)
            trace.append({**ev, "display": _slim_display(display)})
            yield ev
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result_text})

        if step == AGENT_MAX_STEPS:
            messages.append({"role": "user", "content": "已达到工具调用步数上限，请基于目前获得的信息直接给出最终回答，并说明还缺哪些信息。"})

    return "（推理步数用尽，未能得出结论）", citations, payload, usage


def _slim_display(display: dict) -> dict:
    """落库的轨迹里只保留展示需要的数据，大表截到 200 行。"""
    slim = dict(display)
    if isinstance(slim.get("data"), list) and len(slim["data"]) > 200:
        slim["data"] = slim["data"][:200]
        slim["truncated"] = True
    return slim


def _verify_references(answer: str, citations: list[dict]) -> str:
    """引用校验：只保留本轮检索到的页面或知识库中真实存在的页面，其余 [[...]] 降级为普通文本，防止编造来源。"""
    refs = set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", answer))
    if not refs:
        return answer
    from backend.kb.wiki import slugify
    from backend.database import execute_sql

    retrieved = {c["slug"] for c in citations}
    existing = {row[0] for row in execute_sql(
        "SELECT slug FROM wiki_pages WHERE NOT is_deleted AND slug = ANY(:s)",
        {"s": [slugify(r) for r in refs]},
    )["data"]}
    valid = retrieved | existing

    def replace(match: re.Match) -> str:
        space, target, label = match.group(1), match.group(2), match.group(3)
        if slugify(target) in valid or target in valid:
            return match.group(0)
        # 无效引用：有显示文字就保留文字，纯引用标记连同前面的空格一起去掉
        return f"{space}{label}" if label else ""

    return re.sub(r"(\s*)\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", replace, answer)


def _merge_citations(citations: list[dict], answer: str) -> list[dict]:
    """去重，并标记回答里实际引用到的页面。"""
    cited_slugs = set(re.findall(r"\[\[([^\]|]+)", answer))
    seen, out = set(), []
    for c in citations:
        if c["chunk_id"] in seen:
            continue
        seen.add(c["chunk_id"])
        out.append({**c, "n": len(out) + 1, "cited": c["slug"] in cited_slugs or not cited_slugs})
    return out


# ---------------------------------------------------------------------------
# 未配置大模型时的规则路由
# ---------------------------------------------------------------------------

def _fallback(user, session_id, question, target_table, trace) -> Iterator[dict]:
    tool_ctx = ToolContext(user["id"], session_id, False, target_table, role=user["role"])
    registry = load_registry(user["role"], False, False)
    if target_table:
        name, args = "analyze_imported_table", {"table_name": target_table, "question": question}
    elif (pack := active_pack()).analysis_tool and any(w in question for w in pack.analysis_words):
        name, args = pack.analysis_tool, {"question": question}
    else:
        name, args = "search_knowledge", {"query": question}
    ev = _event("thinking", step=1, kind="plan",
                text=f"未配置大模型，按规则选择工具 {name}。配置 LLM 后可进行多步推理、写 SQL 和自我修正。")
    trace.append(ev)
    yield ev
    ev = _event("tool_call", step=1, id="rule-1", name=name, args=args)
    trace.append(ev)
    yield ev
    t0 = time.perf_counter()
    result = execute(name, args, tool_ctx, registry)
    ev = _event("tool_result", step=1, id="rule-1", name=name, ok=result.ok, summary=result.summary,
                elapsed_ms=int((time.perf_counter() - t0) * 1000), display=result.display)
    trace.append({**ev, "display": _slim_display(result.display)})
    yield ev

    payload = {"tables": [], "analyses": [result.display["analysis"]] if "analysis" in result.display else []}
    citations = [{**c, "cited": True} for c in result.display.get("citations", [])]
    if "analysis" in result.display:
        report = result.display["analysis"].get("report") or {}
        answer = report.get("summary") or "分析完成，详见下方结果。"
        if report.get("findings"):
            answer += "\n\n" + "\n".join(f"- {f}" for f in report["findings"][:5])
    elif citations:
        answer = "以下是知识库中最相关的内容（未启用大模型，按相关度摘录）：\n\n" + "\n".join(
            f"- 《{c['title']}》：{c['snippet']} [[{c['slug']}]]" for c in citations[:3])
    else:
        answer = "没有找到相关信息。配置大模型后，助手可以自主查询数据库并进行多步推理。"
    return answer, citations, payload, {"steps": 1, "mode": "rule"}

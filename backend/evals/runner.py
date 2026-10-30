"""
评测执行

  python -m backend.evals.runner --set retail_store [--profile 模型配置ID] [--case 用例ID ...]

检查类型（一个用例可以有多个检查，全部通过才算通过）：
  number    参考 SQL 返回一个数，回答中要出现这个数（容许四舍五入、万 / 亿 / % 等写法）
  text      参考 SQL 返回一段文字（如门店名、店员名），回答中要出现它
  rows      参考 SQL 返回一列数（SQL 查询模式），查询结果里要包含每一个
  citation  回答引用了指定前缀的知识库页面
  contains  回答中包含任意一个关键词

用例可以带 params：{"名称": "返回一个值的 SQL"}，评测时先执行得到值，代入问题里的 {名称}，
并作为参考 SQL 的绑定参数 :名称 —— 例如「{store}店的毛利」中的门店从门店表里选，评测集不用写死门店名。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.config import APP_DATA_DIR
from backend.database import get_connection

logger = logging.getLogger("nl2sql.evals")

_BUILTIN_DIR = Path(__file__).parent / "sets"
_NUMBER = re.compile(r"(-?\d[\d,，]*(?:\.\d+)?)\s*(万亿|亿|万|%|％)?")
_REL_TOL = 0.005  # 相对误差 0.5%：覆盖「保留两位小数」「以万元为单位」之类的四舍五入


# ---------------------------------------------------------------------------
# 评测集
# ---------------------------------------------------------------------------


def _set_dirs() -> list[Path]:
    return [_BUILTIN_DIR, Path(APP_DATA_DIR) / "evals"]


def list_sets() -> list[dict[str, Any]]:
    sets = []
    for directory in _set_dirs():
        for path in sorted(directory.glob("*.json")) if directory.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("评测集 %s 无法读取: %s", path, exc)
                continue
            data["private"] = directory != _BUILTIN_DIR
            sets.append(data)
    return sets


def get_set(code: str) -> dict[str, Any]:
    found = next((s for s in list_sets() if s["code"] == code), None)
    if not found:
        raise LookupError(f"评测集 {code} 不存在")
    return found


# ---------------------------------------------------------------------------
# 判分
# ---------------------------------------------------------------------------


def extract_numbers(answer: str) -> list[float]:
    """回答中出现的所有数，按单位换算（25.41万 → 254100，8.3% → 0.083 同时保留 8.3）。"""
    values: list[float] = []
    for raw, unit in _NUMBER.findall(answer or ""):
        try:
            value = float(raw.replace(",", "").replace("，", ""))
        except ValueError:
            continue
        if unit == "万":
            values.append(value * 1e4)
        elif unit == "亿":
            values.append(value * 1e8)
        elif unit == "万亿":
            values.append(value * 1e12)
        elif unit in ("%", "％"):
            values += [value / 100, value]
        else:
            values.append(value)
    return values


def close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(abs(expected) * _REL_TOL, 0.01)


def _reference(sql: str, params: dict | None = None) -> list[Any]:
    with get_connection() as conn:
        return [row[0] for row in conn.execute(text(sql), params or {}).all()]


def _params(case: dict) -> dict[str, Any]:
    with get_connection() as conn:
        return {name: conn.execute(text(sql)).scalar() for name, sql in (case.get("params") or {}).items()}


def _cells(result: dict | None) -> list[float]:
    values = []
    for row in (result or {}).get("data") or []:
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                values.append(float(cell))
            elif isinstance(cell, str):
                values += extract_numbers(cell)
    return values


def check(spec: dict, answer: str, citations: list[dict], result: dict | None,
          params: dict | None = None) -> dict[str, Any]:
    kind = spec["type"]
    if kind == "contains":
        ok = any(word in answer for word in spec["any"])
        return {"type": kind, "passed": ok, "expected": " / ".join(spec["any"])}
    if kind == "citation":
        cited = [c.get("slug", "") for c in citations if c.get("cited") is not False]
        ok = any(slug.startswith(spec["slug_prefix"]) for slug in cited)
        return {"type": kind, "passed": ok, "expected": f"引用 {spec['slug_prefix']}…", "actual": cited[:5]}

    expected = [v for v in _reference(spec["sql"], params) if v is not None]
    if not expected:
        return {"type": kind, "passed": None, "expected": "参考 SQL 没有数据（跳过）"}
    if kind == "text":
        target = str(expected[0])
        return {"type": kind, "passed": target in answer, "expected": target}
    if kind == "number":
        target = float(expected[0])
        ok = any(close(n, target) for n in extract_numbers(answer))
        return {"type": kind, "passed": ok, "expected": round(target, 4)}
    if kind == "rows":
        cells = _cells(result)
        targets = [float(v) for v in expected]
        missing = [t for t in targets if not any(close(c, t) for c in cells)]
        return {"type": kind, "passed": not missing, "expected": [round(t, 2) for t in targets],
                "actual": f"缺少 {len(missing)} 个" if missing else "全部命中"}
    raise ValueError(f"未知的检查类型：{kind}")


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _ask(case: dict, user: dict, profile_id: int | None) -> dict[str, Any]:
    """按用例的模式提问，返回 {answer, citations, result, tokens}。"""
    mode = case.get("mode", "assistant")
    if mode == "assistant":
        from backend.assistant import sessions
        from backend.assistant.agent import run_turn

        sid = sessions.create_session(user["id"], title=f"{sessions.EVAL_TITLE_PREFIX}{case['id']}")["id"]
        try:
            done, error = None, None
            for event in run_turn(user, sid, case["question"], None, profile_id, None, remember=False):
                if event["type"] == "done":
                    done = event
                elif event["type"] == "error":
                    error = event.get("message")
        finally:
            sessions.delete_session(user["id"], sid)
        if done is None:
            raise RuntimeError(error or "没有得到回答")
        usage = done.get("usage") or {}
        return {"answer": done["content"], "citations": done.get("citations") or [], "result": None,
                "tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)}

    from backend.llm_gateway import get_provider

    provider = get_provider(profile_id)
    if mode == "query":
        from backend.nl2sql import answer_query

        result = answer_query(case["question"], provider=provider)
        return {"answer": result.get("sql", ""), "citations": [], "result": result, "tokens": 0}
    if mode == "kb":
        from backend.kb import rag
        from backend.kb.spaces import readable_spaces

        result = rag.ask(case["question"], provider=provider, spaces=readable_spaces(user["id"]))
        return {"answer": result["answer"], "citations": result["citations"], "result": None, "tokens": 0}
    raise ValueError(f"未知的模式：{mode}")


def run_case(case: dict, user: dict, profile_id: int | None) -> dict[str, Any]:
    started = time.perf_counter()
    question = case["question"]
    try:
        params = _params(case)
        question = case["question"].format(**params) if params else case["question"]
        got = _ask({**case, "question": question}, user, profile_id)
        checks = [check(spec, got["answer"], got["citations"], got["result"], params) for spec in case["checks"]]
        decided = [c["passed"] for c in checks if c["passed"] is not None]
        passed = all(decided) if decided else None
        error = None
    except Exception as exc:
        logger.exception("评测用例 %s 失败", case["id"])
        got, checks, passed, error = {"answer": "", "tokens": 0}, [], False, str(exc)
    return {
        "id": case["id"], "mode": case.get("mode", "assistant"), "question": question,
        "passed": passed, "checks": checks, "answer": (got["answer"] or "")[:1200], "error": error,
        "elapsed_ms": int((time.perf_counter() - started) * 1000), "tokens": got.get("tokens", 0),
    }


def _user(user_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(text("SELECT id, username, display_name, role FROM users WHERE id = :u"), {"u": user_id}).mappings().one()
    return dict(row)


def _model_name(profile_id: int | None) -> str:
    from backend.llm_gateway import describe, get_provider

    info = describe(get_provider(profile_id))
    return info.get("profile_name") or info.get("model") or "未配置"


def create_run(set_code: str, profile_id: int | None, user_id: int, case_ids: list[str] | None = None) -> int:
    eval_set = get_set(set_code)
    cases = [c for c in eval_set["cases"] if not case_ids or c["id"] in case_ids]
    if not cases:
        raise ValueError("没有可运行的用例")
    with get_connection() as conn:
        return conn.execute(text("""
            INSERT INTO eval_runs (set_code, set_name, profile_id, model_name, total, created_by, cases)
            VALUES (:s, :n, :p, :m, :t, :u, CAST(:c AS jsonb)) RETURNING id
        """), {"s": set_code, "n": eval_set["name"], "p": profile_id, "m": _model_name(profile_id), "t": len(cases),
               "u": user_id, "c": json.dumps(cases, ensure_ascii=False)}).scalar()


def execute_run(run_id: int) -> dict[str, Any]:
    """逐个执行用例，每完成一个就写回进度（前端轮询可以看到进度）。"""
    with get_connection() as conn:
        run = conn.execute(text("SELECT * FROM eval_runs WHERE id = :id"), {"id": run_id}).mappings().one()
        conn.execute(text("UPDATE eval_runs SET status = 'running', started_at = now() WHERE id = :id"), {"id": run_id})
    user = _user(run["created_by"])
    results: list[dict] = []
    for case in run["cases"]:
        results.append(run_case(case, user, run["profile_id"]))
        scored = [r for r in results if r["passed"] is not None]
        with get_connection() as conn:
            conn.execute(text("""
                UPDATE eval_runs SET results = CAST(:r AS jsonb), done = :d, passed = :p, scored = :sc WHERE id = :id
            """), {"r": json.dumps(results, ensure_ascii=False, default=str), "d": len(results),
                   "p": sum(1 for r in scored if r["passed"]), "sc": len(scored), "id": run_id})
    with get_connection() as conn:
        conn.execute(text("UPDATE eval_runs SET status = 'done', finished_at = now() WHERE id = :id"), {"id": run_id})
    return get_run(run_id)


def start_run(set_code: str, profile_id: int | None, user_id: int, case_ids: list[str] | None = None) -> dict[str, Any]:
    """后台执行（一次评测可能要几分钟），立即返回运行记录。"""
    run_id = create_run(set_code, profile_id, user_id, case_ids)

    def work():
        try:
            execute_run(run_id)
        except Exception as exc:
            logger.exception("评测运行 %s 失败", run_id)
            with get_connection() as conn:
                conn.execute(text("UPDATE eval_runs SET status = 'failed', error = :e, finished_at = now() WHERE id = :id"),
                             {"e": str(exc)[:500], "id": run_id})

    threading.Thread(target=work, name=f"eval-{run_id}", daemon=True).start()
    return get_run(run_id)


def _public(row) -> dict[str, Any]:
    data = dict(row)
    data.pop("cases", None)
    data["accuracy"] = round(data["passed"] / data["scored"], 4) if data.get("scored") else None
    return data


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT id, set_code, set_name, profile_id, model_name, status, total, done, passed, scored, error,
                   created_at, started_at, finished_at
            FROM eval_runs ORDER BY id DESC LIMIT :l
        """), {"l": limit}).mappings().all()
    return [_public(r) for r in rows]


def get_run(run_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(text("SELECT * FROM eval_runs WHERE id = :id"), {"id": run_id}).mappings().first()
    if not row:
        raise LookupError("评测记录不存在")
    return _public(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行准确率评测")
    parser.add_argument("--set", required=True, help="评测集标识，如 retail_store")
    parser.add_argument("--profile", type=int, default=None, help="模型配置 ID，留空用默认模型")
    parser.add_argument("--case", action="append", help="只运行指定用例（可多次）")
    parser.add_argument("--user", default="admin", help="以哪个用户的身份提问（影响可读的知识空间）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    from backend.migrations import run_migrations

    run_migrations()
    with get_connection() as conn:
        user_id = conn.execute(text("SELECT id FROM users WHERE username = :u"), {"u": args.user}).scalar()
    if not user_id:
        raise SystemExit(f"用户 {args.user} 不存在")
    run = execute_run(create_run(args.set, args.profile, user_id, args.case))
    for r in run["results"]:
        mark = "✓" if r["passed"] else ("-" if r["passed"] is None else "✗")
        print(f"{mark} {r['id']:<20} {r['elapsed_ms'] / 1000:>6.1f}s  {r['question']}")
        for c in r["checks"]:
            if c["passed"] is False:
                print(f"      期望 {c['type']}: {c.get('expected')}")
        if r["error"]:
            print(f"      错误：{r['error']}")
    acc = run["accuracy"]
    print(f"\n{run['model_name']}：{run['passed']}/{run['scored']} 通过" + (f"（准确率 {acc:.0%}）" if acc is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

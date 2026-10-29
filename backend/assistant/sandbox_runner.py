"""
Python 沙箱子进程入口 — 由 python_sandbox.run_sandboxed 以 `python -I` 方式启动，不要在主进程中导入执行。

  stdin  : {"code", "columns", "rows", "timeout", "memory_mb"}
  stdout : 一行 JSON {"ok", "result", "chart", "stdout", "error"}

子进程内的防护（在 AST 校验之外再加一层）：
  - 资源限制：CPU 时间、内存、禁止写文件（RLIMIT_FSIZE=0）
  - 运行时屏蔽网络、子进程、进程替换相关函数
  - 受限的内置函数集，import 只允许白名单模块
  - 父进程以空环境变量启动本进程，拿不到数据库密码等任何凭证
"""

import contextlib
import datetime
import io
import json
import math
import sys

PAYLOAD = json.loads(sys.stdin.read())
_real_stdout = sys.stdout

import collections  # noqa: E402
import functools  # noqa: E402
import itertools  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ALLOWED_MODULES = {
    "pandas": pd, "numpy": np, "math": math, "statistics": statistics, "datetime": datetime,
    "re": re, "collections": collections, "itertools": itertools, "functools": functools, "json": json,
}


def _blocked(*_args, **_kwargs):
    raise PermissionError("沙箱中禁止网络、子进程和文件操作")


def _harden() -> None:
    import os
    import resource
    import socket
    import subprocess

    timeout = int(PAYLOAD.get("timeout", 20))
    for limit, value in (
        (resource.RLIMIT_CPU, (timeout, timeout + 1)),
        (resource.RLIMIT_FSIZE, (0, 0)),
        (resource.RLIMIT_AS, (PAYLOAD.get("memory_mb", 1024) * 1024 * 1024,) * 2),
    ):
        try:
            resource.setrlimit(limit, value)
        except (ValueError, OSError):
            pass  # 部分平台（如 macOS 的 RLIMIT_AS）不支持，其他防护层仍然生效
    for name in ("socket", "create_connection", "create_server", "socketpair", "fromfd"):
        setattr(socket, name, _blocked)
    for name in ("Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
        setattr(subprocess, name, _blocked)
    for name in ("system", "popen", "fork", "forkpty", "kill", "killpg", "remove", "unlink", "rmdir", "removedirs",
                 "rename", "replace", "chmod", "chown", "mkdir", "makedirs", "symlink", "link", "truncate",
                 "putenv", "unsetenv", "listdir", "scandir", "walk", "open", "startfile"):
        if hasattr(os, name):
            setattr(os, name, _blocked)
    for name in dir(os):
        if name.startswith(("exec", "spawn", "posix_spawn")):
            setattr(os, name, _blocked)


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name not in ALLOWED_MODULES:
        raise ImportError(f"沙箱中只允许导入：{', '.join(sorted(ALLOWED_MODULES))}")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "int", "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "reversed", "round",
        "set", "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
        "Exception", "ValueError", "KeyError", "TypeError", "IndexError", "ZeroDivisionError", "ArithmeticError",
    )
}
SAFE_BUILTINS["__import__"] = _safe_import


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _jsonable(float(value))
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta, datetime.timedelta)):
        return str(value)
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _serialize(result):
    if result is None:
        return None
    if isinstance(result, pd.Series):
        result = result.to_frame(name=str(result.name) if result.name is not None else "value")
    if isinstance(result, pd.DataFrame):
        frame = result
        if not isinstance(frame.index, pd.RangeIndex):
            frame = frame.reset_index()
        total = len(frame)
        frame = frame.head(500)
        return {
            "kind": "table",
            "columns": [str(c) for c in frame.columns],
            "data": [[_jsonable(v) for v in row] for row in frame.itertuples(index=False, name=None)],
            "row_count": total,
            "truncated": total > 500,
        }
    if isinstance(result, dict):
        return {"kind": "value", "value": {str(k): _jsonable(v) for k, v in list(result.items())[:200]}}
    if isinstance(result, (list, tuple)):
        return {"kind": "value", "value": [_jsonable(v) for v in list(result)[:500]]}
    return {"kind": "value", "value": _jsonable(result)}


def _load_frame():
    df = pd.DataFrame(PAYLOAD.get("rows", []), columns=PAYLOAD.get("columns", []))
    for col in df.columns:
        sample = df[col].dropna().head(20)
        if len(sample) and all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$", v) for v in sample):
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def main() -> None:
    df = _load_frame()
    _harden()
    namespace = {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np, "df": df, "math": math}
    output = io.StringIO()
    response = {"ok": True, "result": None, "chart": None, "stdout": "", "error": None}
    try:
        with contextlib.redirect_stdout(output):
            exec(compile(PAYLOAD["code"], "<analysis>", "exec"), namespace)
        response["result"] = _serialize(namespace.get("result"))
        chart = namespace.get("chart")
        response["chart"] = {str(k): _jsonable(v) if not isinstance(v, list) else [_jsonable(x) for x in v]
                             for k, v in chart.items()} if isinstance(chart, dict) else None
    except BaseException as exc:  # 用户代码的任何异常都转成结构化错误返回给模型修正
        response["ok"] = False
        response["error"] = f"{type(exc).__name__}: {exc}"[:1000]
    response["stdout"] = output.getvalue()[:4000]
    _real_stdout.write(json.dumps(response, ensure_ascii=False, default=str))
    _real_stdout.flush()


if __name__ == "__main__":
    main()

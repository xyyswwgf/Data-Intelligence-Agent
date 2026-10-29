"""
Python 分析沙箱 — 让智能助手用 pandas 处理已经通过只读 SQL 取出的数据

防护分层（任何一层失效，其余层仍然生效）：
  1. 数据来源：只能处理 run_sql 同一套安全网关取出的数据（只读角色 + READ ONLY 事务），代码本身不接触数据库
  2. AST 静态校验：import 白名单；禁止下划线开头的属性（__class__ 等逃逸入口）；
     禁止 eval / exec / open / getattr 等内置函数；禁止 pandas / numpy 的文件读写与 eval/query；
     禁止 str.format（可通过格式串遍历属性）以及 os / sys / io 等模块名属性
  3. 独立子进程：`python -I` 隔离模式、空环境变量（拿不到任何凭证）、临时工作目录、
     CPU 时间与内存上限、禁止写文件、运行时屏蔽网络和子进程、超时强制结束
  4. 受限内置函数集，代码只能通过 `result` / `chart` 变量返回结果，输出长度有上限

与 6 月版本的区别：不再把列名拼接进代码模板执行（列名可被构造成注入代码），也不提供跳过校验的 local 模式。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

from backend.config import PYTHON_SANDBOX_MEMORY_MB, PYTHON_SANDBOX_TIMEOUT_SECONDS

_RUNNER = os.path.join(os.path.dirname(__file__), "sandbox_runner.py")

ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics", "datetime", "re", "collections", "itertools",
                   "functools", "json"}
BLOCKED_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__", "getattr", "setattr", "delattr", "hasattr",
    "globals", "locals", "vars", "dir", "help", "breakpoint", "memoryview", "type", "object", "super",
    "classmethod", "staticmethod", "property", "exit", "quit", "__builtins__",
}
# 模块名作为属性出现时同样禁止（例如 pd.io.common.os）
BLOCKED_ATTRS = {
    "os", "sys", "subprocess", "socket", "shutil", "builtins", "importlib", "pathlib", "ctypes", "io", "pickle",
    "marshal", "inspect", "gc", "signal", "threading", "multiprocessing", "urllib", "http", "requests", "glob",
    "tempfile", "ctypeslib", "f2py", "distutils", "testing", "lib", "core", "compat", "util", "plotting",
    # 格式串可以遍历任意属性：'{0.__class__}'.format(x)
    "format", "format_map",
    # 可执行表达式 / 字符串代码
    "eval", "query", "pipe",
    # numpy 文件读写
    "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromfile",
    "tofile", "memmap", "fromregex",
    "system", "popen",
}
# pandas 的 read_* 全部禁止；to_* 只放行纯内存转换
ALLOWED_TO_METHODS = {"to_dict", "to_list", "to_numpy", "to_frame", "to_datetime", "to_numeric", "to_timedelta",
                      "to_period", "to_timestamp", "to_string", "to_records", "to_flat_index", "to_series",
                      "to_pydatetime", "to_offset"}


class SandboxRejected(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("代码未通过安全校验：" + "；".join(problems))


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []

    def _flag(self, message: str) -> None:
        if message not in self.problems:
            self.problems.append(message)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # 只允许导入顶层模块本身：pandas.io.common 这类子模块带有文件读写函数
            if alias.name not in ALLOWED_IMPORTS:
                self._flag(f"不允许 import {alias.name}（允许：{', '.join(sorted(ALLOWED_IMPORTS))}）")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level or module not in ALLOWED_IMPORTS:
            self._flag(f"不允许 from {module or '.'} import …")
        for alias in node.names:
            if alias.name.startswith("_") or alias.name in BLOCKED_ATTRS or alias.name == "*":
                self._flag(f"不允许导入 {alias.name}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_NAMES or node.id.startswith("__"):
            self._flag(f"不允许使用 {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("_"):
            self._flag(f"不允许访问下划线属性 .{attr}")
        elif attr in BLOCKED_ATTRS:
            self._flag(f"不允许访问 .{attr}")
        elif attr.startswith("read_"):
            self._flag(f"不允许读取文件（.{attr}），数据请通过 sql 参数获取")
        elif attr.startswith("to_") and attr not in ALLOWED_TO_METHODS:
            self._flag(f"不允许导出 / 写文件（.{attr}）")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._flag("不允许使用 global")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._flag("不允许使用 nonlocal")


def validate_code(code: str) -> list[str]:
    if len(code) > 20_000:
        return ["代码过长（上限 20000 字符）"]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"语法错误（第 {exc.lineno} 行）：{exc.msg}"]
    checker = _Checker()
    checker.visit(tree)
    return checker.problems


@dataclass
class SandboxResult:
    ok: bool
    result: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    stdout: str = ""
    error: str | None = None
    elapsed_ms: int = 0
    problems: list[str] = field(default_factory=list)


def run_sandboxed(
    code: str,
    columns: list[str],
    rows: list[list[Any]],
    timeout: int = PYTHON_SANDBOX_TIMEOUT_SECONDS,
    memory_mb: int = PYTHON_SANDBOX_MEMORY_MB,
) -> SandboxResult:
    import time

    problems = validate_code(code)
    if problems:
        raise SandboxRejected(problems)
    payload = json.dumps({"code": code, "columns": columns, "rows": rows, "timeout": timeout,
                          "memory_mb": memory_mb}, ensure_ascii=False, default=str)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sandbox_") as workdir:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", _RUNNER],
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout + 10,  # 墙钟超时：兜底 sleep 等不占 CPU 的阻塞
                cwd=workdir,
                env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1",
                     "OPENBLAS_NUM_THREADS": "1", "HOME": workdir},
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(False, error=f"执行超时（超过 {timeout} 秒），请减少数据量或简化计算",
                                 elapsed_ms=int((time.perf_counter() - started) * 1000))
    elapsed = int((time.perf_counter() - started) * 1000)
    if proc.returncode != 0 or not proc.stdout.strip():
        stderr = proc.stderr.strip().splitlines()
        reason = stderr[-1] if stderr else f"进程退出码 {proc.returncode}"
        if proc.returncode in (-9, -24, 137, 152):  # SIGKILL / SIGXCPU
            reason = f"超出资源限制（CPU {timeout} 秒或内存 {memory_mb}MB）"
        return SandboxResult(False, error=f"沙箱执行失败：{reason}", elapsed_ms=elapsed)
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return SandboxResult(data["ok"], data.get("result"), data.get("chart"), data.get("stdout", ""),
                         data.get("error"), elapsed)

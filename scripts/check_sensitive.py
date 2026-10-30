#!/usr/bin/env python3
"""
提交前的敏感信息检查（git pre-commit 钩子调用，也可以手动运行）

  python scripts/check_sensitive.py                  # 检查暂存区（即将提交的内容）
  python scripts/check_sensitive.py --all            # 检查仓库中所有受版本控制的文件
  python scripts/check_sensitive.py --build-denylist # 从本机数据库生成真实名称清单（门店、店员、供应商、收款终端）

拦截：
  - 数据文件（.xlsx / .xls / .xlsm / .csv，sample_data/ 中的虚构样例除外）和 .env
  - 手机号、身份证号（测试里使用的虚构号码除外）
  - 本机真实名称清单（~/.datamind/denylist.txt，只存在本机、不进仓库）中的任何名称
发现问题时退出码为 1，提交会被阻止；输出中的名称只显示首字，避免在终端或日志里再次泄露。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DENYLIST = Path(os.path.expanduser(os.getenv("SENSITIVE_DENYLIST", "~/.datamind/denylist.txt")))
DATA_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv"}
ALLOWED_DATA_DIRS = ("sample_data/",)
FAKE_PHONES = {"13800138000", "13900001111"}  # 测试用的虚构号码
FAKE_IDS = {"110101199003071234"}  # 公开的示例身份证号（测试用）
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dXx])")
# 太常见、会误报的词不放进名称清单
STOPWORDS = {"商品", "门店", "总仓", "总库", "总部", "总公司", "个人", "公司", "收款", "线上", "线下", "微信", "支付宝",
             "荣耀", "示例", "未知", "其他", "本店", "仓库"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _files(all_files: bool) -> list[str]:
    if all_files:
        return [f for f in _git("ls-files").splitlines() if f]
    return [f for f in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if f]


def _content(path: str, all_files: bool) -> str:
    try:
        raw = (Path(path).read_bytes() if all_files else
               subprocess.run(["git", "show", f":{path}"], check=True, capture_output=True).stdout)
    except (OSError, subprocess.CalledProcessError):
        return ""
    if b"\0" in raw[:8000]:
        return ""  # 二进制文件按类型检查，不扫描内容
    return raw.decode("utf-8", errors="ignore")


def _mask(word: str) -> str:
    return word[0] + "*" * (len(word) - 1)


def load_denylist() -> list[str]:
    if not DENYLIST.exists():
        return []
    return [w.strip() for w in DENYLIST.read_text(encoding="utf-8").splitlines() if len(w.strip()) >= 2]


def check(all_files: bool = False) -> list[str]:
    problems: list[str] = []
    denylist = load_denylist()
    for path in _files(all_files):
        lower = path.lower()
        if Path(lower).suffix in DATA_SUFFIXES and not lower.startswith(ALLOWED_DATA_DIRS):
            problems.append(f"{path}: 数据文件不能提交（真实报表只导入本地数据库）")
            continue
        if Path(path).name == ".env":
            problems.append(f"{path}: .env 含密码和密钥，不能提交")
            continue
        if path == "scripts/check_sensitive.py":
            continue
        text = _content(path, all_files)
        for n, line in enumerate(text.splitlines(), start=1):
            for phone in PHONE.findall(line):
                if phone not in FAKE_PHONES:
                    problems.append(f"{path}:{n}: 手机号 {phone[:3]}****{phone[-4:]}")
            if any(m not in FAKE_IDS for m in ID_CARD.findall(line)):
                problems.append(f"{path}:{n}: 疑似身份证号")
            for word in denylist:
                if word in line:
                    problems.append(f"{path}:{n}: 真实名称「{_mask(word)}」")
    return problems


def build_denylist() -> int:
    """从本机数据库和门店配置收集真实名称，写入 ~/.datamind/denylist.txt（权限 600）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy import text

    from backend.database import get_connection
    from backend.store_data.schema import DEFAULT_STORES, _config

    words: set[str] = set()
    for s in _config().get("stores") or []:
        words |= {s.get("name"), s.get("short"), s.get("mall"), *(s.get("aliases") or [])}
    queries = [
        "SELECT DISTINCT salesperson FROM fact_store_sales",
        "SELECT DISTINCT owner FROM fact_store_targets WHERE owner_type = 'staff'",
        "SELECT DISTINCT supplier FROM fact_stock_age",
        "SELECT DISTINCT supplier FROM fact_purchase_in",
        "SELECT DISTINCT company FROM fact_purchase_in",
        "SELECT DISTINCT warehouse FROM fact_purchase_in",
        "SELECT DISTINCT warehouse FROM fact_stock_age",
        "SELECT DISTINCT merchant_label FROM fact_payments",
        "SELECT store_name FROM dim_store UNION SELECT short_name FROM dim_store UNION SELECT mall FROM dim_store",
    ]
    with get_connection() as conn:
        for q in queries:
            try:
                words |= set(conn.execute(text(q)).scalars().all())
            except Exception:
                conn.rollback()
    fictional = {w for s in DEFAULT_STORES for w in (s[1], s[2], s[3], *s[4])}
    words = {str(w).strip() for w in words if w}
    words = {w for w in words if len(w) >= 2 and w not in STOPWORDS and w not in fictional}
    DENYLIST.parent.mkdir(parents=True, exist_ok=True)
    DENYLIST.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    os.chmod(DENYLIST, 0o600)
    print(f"已写入 {DENYLIST}：{len(words)} 个真实名称（只保存在本机）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="提交前的敏感信息检查")
    parser.add_argument("--all", action="store_true", help="检查所有受版本控制的文件")
    parser.add_argument("--build-denylist", action="store_true", help="从本机数据库生成真实名称清单")
    args = parser.parse_args()
    if args.build_denylist:
        return build_denylist()
    problems = check(args.all)
    if not DENYLIST.exists():
        print(f"提示：没有找到真实名称清单 {DENYLIST}，只检查了数据文件、手机号和身份证号。"
              "在有真实数据的电脑上运行 --build-denylist 生成。")
    if problems:
        print("发现可能的敏感信息，已阻止提交：")
        for p in problems[:50]:
            print("  " + p)
        if len(problems) > 50:
            print(f"  ……共 {len(problems)} 处")
        return 1
    print(f"敏感信息检查通过（{'全部文件' if args.all else '暂存区'}，名称清单 {len(load_denylist())} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

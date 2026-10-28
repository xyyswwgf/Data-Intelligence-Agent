"""Python 分析沙箱：静态校验、子进程隔离、资源限制、结果序列化（无需数据库）。"""

import pytest

from backend.assistant.python_sandbox import SandboxRejected, run_sandboxed, validate_code

COLS = ["省份", "日期", "gmv"]
ROWS = [["北京", "2026-05-01", 100.0], ["北京", "2026-06-01", 80.0], ["上海", "2026-05-01", 90.0], ["上海", "2026-06-01", 99.0]]


def test_pandas_analysis_returns_table_chart_and_stdout():
    run = run_sandboxed(
        "import pandas as pd\n"
        "df['月'] = df['日期'].dt.strftime('%Y-%m')\n"
        "p = df.pivot_table(index='省份', columns='月', values='gmv', aggfunc='sum')\n"
        "p['环比'] = (p['2026-06'] / p['2026-05'] - 1).round(3)\n"
        "print(f'最差：{p[\"环比\"].idxmin()}')\n"
        "result = p.sort_values('环比')\n"
        "chart = {'type': 'bar', 'x': '省份', 'y': ['环比']}\n",
        COLS, ROWS,
    )
    assert run.ok, run.error
    assert run.result["columns"] == ["省份", "2026-05", "2026-06", "环比"]
    assert run.result["data"][0][0] == "北京" and run.result["data"][0][3] == -0.2
    assert run.chart == {"type": "bar", "x": "省份", "y": ["环比"]}
    assert run.stdout.strip() == "最差：北京"


def test_scalar_dict_and_runtime_error():
    assert run_sandboxed("result = df['gmv'].sum()", COLS, ROWS).result == {"kind": "value", "value": 369.0}
    run = run_sandboxed("result = df['不存在的列']", COLS, ROWS)
    assert not run.ok and "KeyError" in run.error


@pytest.mark.parametrize("code", [
    "import os\nos.system('id')",
    "import subprocess",
    "import pandas.io.common as c",
    "from pandas.io import common",
    "x = ().__class__.__mro__",
    "x = df._data",
    "x = '{0.__class__}'.format(df)",
    "x = pd.read_pickle('/tmp/x')",
    "x = pd.read_csv('/etc/passwd')",
    "df.to_csv('/tmp/x.csv')",
    "df.to_parquet('/tmp/x')",
    "x = np.load('/tmp/x.npy', allow_pickle=True)",
    "np.save('/tmp/x', df)",
    "x = open('/etc/passwd').read()",
    "x = getattr(df, 'shape')",
    "x = eval('1+1')",
    "exec('x = 1')",
    "x = df.query('gmv > 1')",
    "x = df.eval('gmv * 2')",
    "x = pd.io",
    "x = __builtins__",
    "x = type(df)",
    "global df",
])
def test_static_check_blocks_escape_attempts(code):
    assert validate_code(code)
    with pytest.raises(SandboxRejected):
        run_sandboxed(code, COLS, ROWS)


def test_allowed_code_passes_static_check():
    assert validate_code("import numpy as np\nfrom collections import Counter\nresult = df.to_dict('records')") == []


def test_runtime_isolation_blocks_what_static_check_cannot_see():
    # 即使静态校验被绕过，子进程里也拿不到凭证、不能写文件
    from backend.assistant import python_sandbox

    original = python_sandbox.validate_code
    python_sandbox.validate_code = lambda code: []
    try:
        env = run_sandboxed("import os\nresult = dict(os.environ)", COLS, ROWS)
        assert not env.ok  # os 不在允许导入的模块里（运行时 import 拦截）
        leaked = run_sandboxed("result = __import__('json').dumps(1)", COLS, ROWS)
        assert leaked.ok  # 白名单模块仍可用
        write = run_sandboxed("import pandas as pd\ndf.to_csv('x.csv')", COLS, ROWS)
        assert not write.ok  # RLIMIT_FSIZE=0 / os.open 被屏蔽
    finally:
        python_sandbox.validate_code = original


def test_cpu_limit_kills_infinite_loop():
    run = run_sandboxed("while True:\n    pass", COLS, ROWS, timeout=2)
    assert not run.ok and ("资源限制" in run.error or "超时" in run.error)
    assert run.elapsed_ms < 15000

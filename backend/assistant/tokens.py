"""token 估算：不同模型分词器不同，这里用保守近似（中文 1 字≈1 token，其他约 4 字符≈1 token）。"""

import re

_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + max(0, len(text) - cjk) // 4 + 1


def truncate_to_tokens(text: str, budget: int) -> str:
    if estimate_tokens(text) <= budget:
        return text
    # 二分找到不超过预算的最长前缀
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= budget - 3:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…（已截断）"

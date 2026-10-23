"""
Embedding 客户端 — OpenAI 兼容的 /v1/embeddings 接口

本地 Ollama（默认）、硅基流动、OpenAI、vLLM / Xinference 等都兼容该协议，切换只需改配置。
"""

from __future__ import annotations

import logging
import time

import httpx

from backend.config import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    EMBEDDING_TIMEOUT_SECONDS,
)

logger = logging.getLogger("nl2sql.kb.embeddings")


class EmbeddingError(RuntimeError):
    pass


# 服务不可用时短暂熔断，避免每次检索都等待超时
_circuit_open_until = 0.0
_CIRCUIT_SECONDS = 30


def enabled() -> bool:
    return EMBEDDING_PROVIDER != "none"


def available() -> bool:
    return enabled() and time.monotonic() >= _circuit_open_until


def model_name() -> str:
    return EMBEDDING_MODEL


def embed(texts: list[str]) -> list[list[float]]:
    global _circuit_open_until
    if not enabled():
        raise EmbeddingError("Embedding 未启用（EMBEDDING_PROVIDER=none）")
    if not available():
        raise EmbeddingError("Embedding 服务暂不可用（熔断中）")
    headers = {"Content-Type": "application/json"}
    if EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDING_API_KEY}"
    vectors: list[list[float]] = []
    try:
        with httpx.Client(timeout=EMBEDDING_TIMEOUT_SECONDS) as client:
            for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = [t[:8000] for t in texts[i:i + EMBEDDING_BATCH_SIZE]]
                resp = client.post(
                    f"{EMBEDDING_BASE_URL}/embeddings",
                    headers=headers,
                    json={"model": EMBEDDING_MODEL, "input": batch},
                )
                if resp.status_code >= 400:
                    raise EmbeddingError(f"Embedding 接口返回 {resp.status_code}: {resp.text[:300]}")
                data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
                if len(data) != len(batch):
                    raise EmbeddingError("Embedding 接口返回的向量数量与输入不一致")
                for item in data:
                    vec = item["embedding"]
                    if len(vec) != EMBEDDING_DIM:
                        raise EmbeddingError(
                            f"向量维度 {len(vec)} 与配置 EMBEDDING_DIM={EMBEDDING_DIM} 不一致，请检查模型配置"
                        )
                    vectors.append(vec)
    except EmbeddingError:
        _circuit_open_until = time.monotonic() + _CIRCUIT_SECONDS
        raise
    except Exception as exc:
        _circuit_open_until = time.monotonic() + _CIRCUIT_SECONDS
        raise EmbeddingError(f"Embedding 服务调用失败: {exc}") from exc
    return vectors


def health() -> dict:
    """管理页展示用：实际调用一次，返回可用性和延迟。"""
    global _circuit_open_until
    if not enabled():
        return {"enabled": False, "ok": False, "model": EMBEDDING_MODEL, "base_url": EMBEDDING_BASE_URL}
    _circuit_open_until = 0.0
    started = time.perf_counter()
    try:
        embed(["健康检查"])
        return {"enabled": True, "ok": True, "model": EMBEDDING_MODEL, "base_url": EMBEDDING_BASE_URL,
                "latency_ms": int((time.perf_counter() - started) * 1000)}
    except EmbeddingError as exc:
        return {"enabled": True, "ok": False, "model": EMBEDDING_MODEL, "base_url": EMBEDDING_BASE_URL, "error": str(exc)}


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

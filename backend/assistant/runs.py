"""
对话运行管理：一轮对话在后台线程里跑完，SSE 连接只是「旁听」

为什么不直接在 HTTP 请求里迭代事件生成器：
  - 用户刷新页面、切走或网络断开时，StreamingResponse 会关闭生成器，Agent 在半路被打断：
    回答不落库、已经消耗的 token 不计费、会话里只剩一条没有回答的提问。
  - 放到后台线程后，连接断开只影响「旁听」，这一轮照样跑完、落库、计费；刷新后能看到完整回答。

同时提供：
  - 停止生成：前端点「停止」→ cancel(session_id)，Agent 在下一个检查点（模型输出片段之间、工具调用之间）收尾
  - 并发保护：同一个会话同时只允许一轮在跑，避免两轮交错写消息、上下文错乱
"""

from __future__ import annotations

import contextvars
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator

_DONE = object()
_lock = threading.Lock()
_active: dict[str, "Run"] = {}


class RunBusy(RuntimeError):
    pass


@dataclass
class Run:
    session_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    events: "queue.Queue" = field(default_factory=queue.Queue)


def is_running(session_id: str) -> bool:
    with _lock:
        return session_id in _active


def cancel(session_id: str) -> bool:
    """请求停止；返回是否确实有正在运行的一轮。"""
    with _lock:
        run = _active.get(session_id)
    if run is None:
        return False
    run.cancel.set()
    return True


def start(session_id: str, make_events: Callable[[threading.Event], Iterator[dict]]) -> Iterator[dict]:
    """
    在后台线程中执行 make_events(cancel_event) 产出的事件流，返回给 SSE 连接消费的迭代器。
    调用方断开（迭代器被关闭）不影响后台执行。
    """
    run = Run(session_id)
    with _lock:
        if session_id in _active:
            raise RunBusy("这个对话正在回答上一个问题，请等待完成或先停止")
        _active[session_id] = run

    ctx = contextvars.copy_context()  # 计费上下文等跟随到后台线程

    def worker() -> None:
        try:
            for event in make_events(run.cancel):
                run.events.put(event)
        except Exception as exc:  # 兜底：事件流内部已处理常规错误，这里只防线程静默死亡
            run.events.put({"type": "error", "message": f"处理失败：{exc}"})
        finally:
            with _lock:
                _active.pop(session_id, None)
            run.events.put(_DONE)

    threading.Thread(target=ctx.run, args=(worker,), name=f"chat-run-{session_id[:8]}", daemon=True).start()

    def listen() -> Iterator[dict]:
        while True:
            event = run.events.get()
            if event is _DONE:
                return
            yield event

    return listen()

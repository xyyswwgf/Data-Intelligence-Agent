"""对话后台执行：断开连接不中断、同一会话不并发、停止生成。"""

import threading
import time

from backend.assistant import runs
from backend.tests.conftest import requires_db


def test_run_survives_disconnect():
    finished = threading.Event()

    def make(cancel):
        yield {"type": "a"}
        time.sleep(0.05)
        yield {"type": "b"}
        finished.set()

    events = runs.start("s-disconnect", make)
    assert next(events)["type"] == "a"
    events.close()  # 浏览器断开：只是不再旁听
    assert finished.wait(2), "后台这一轮应该照样跑完"
    for _ in range(50):
        if not runs.is_running("s-disconnect"):
            break
        time.sleep(0.02)
    assert not runs.is_running("s-disconnect")


def test_one_run_per_session_and_cancel():
    release = threading.Event()

    def make(cancel):
        yield {"type": "start"}
        cancel.wait(2)
        yield {"type": "stopped" if cancel.is_set() else "timeout"}
        release.set()

    events = runs.start("s-busy", make)
    assert next(events)["type"] == "start"
    try:
        runs.start("s-busy", make)
        raise AssertionError("同一会话不应允许并发")
    except runs.RunBusy:
        pass
    assert runs.cancel("s-busy") is True
    assert [e["type"] for e in events] == ["stopped"]
    assert release.wait(2)
    assert runs.cancel("s-none") is False


def test_worker_exception_becomes_error_event():
    def make(cancel):
        yield {"type": "a"}
        raise RuntimeError("boom")

    assert [e["type"] for e in runs.start("s-err", make)] == ["a", "error"]


@requires_db
def test_stop_endpoint_and_running_flag(client, make_user):
    headers = make_user("viewer")
    sid = client.post("/api/chat/sessions", headers=headers).json()["id"]
    assert client.post(f"/api/chat/sessions/{sid}/stop", headers=headers).json() == {"stopping": False}
    assert client.get(f"/api/chat/sessions/{sid}", headers=headers).json()["running"] is False
    other = make_user("viewer")
    assert client.post(f"/api/chat/sessions/{sid}/stop", headers=other).status_code == 404

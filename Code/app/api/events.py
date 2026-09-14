"""SSE 事件契约：图上发生的事 → 前端能看到的事件。

铁律（写在这里，因为它决定了整个接口的形状）：
  **只有出口节点能产生 final 事件**，其余节点最多产生 status。
  过程可以流式，正文必须整段审后发送 —— 两者物理上不可兼得。
"""

from __future__ import annotations

import json
from typing import Any

#: 事件类型（与 Workflow/langgraph-pseudocode.md §8 的契约一致）
EVENT_STATUS = "status"
EVENT_AWAITING = "awaiting_confirmation"
EVENT_FINAL = "final"
EVENT_HANDOFF = "handoff"
EVENT_BLOCKED = "blocked"
EVENT_DONE = "done"
EVENT_ERROR = "error"

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",     # 关掉 nginx 缓冲，否则 SSE 会被攒着一起发
}


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def ping() -> str:
    """注释行心跳：防止代理/浏览器在长时间无数据时断开连接。"""
    return ": ping\n\n"


def map_patch(node: str, patch: Any) -> list[tuple[str, dict]]:
    """
    把一个节点的状态更新翻译成对用户可见的事件。

    返回列表是因为：一个超步里可能有多个节点同时完成（例如紧急场景
    send 与 human_handoff 并行），需要产生多个事件。
    """
    events: list[tuple[str, dict]] = []
    if not isinstance(patch, dict):
        return events

    # ── 1. 挂起等确认 ──
    interrupts = patch.get("__interrupt__")
    if interrupts:
        payload = getattr(interrupts[0], "value", interrupts[0]) if interrupts else {}
        payload = payload if isinstance(payload, dict) else {"plan": str(payload)}
        events.append((EVENT_AWAITING, {
            "plan": payload.get("plan", ""),
            "plan_hash": payload.get("plan_hash"),
            "expires_at": payload.get("expires_at"),
        }))
        return events

    # ── 2. 出站内容：只有 send 能产生 final ──
    outbound = patch.get("outbound")
    if node == "send" and isinstance(outbound, dict) and outbound.get("kind") == "reply":
        events.append((EVENT_FINAL, {
            "text": outbound.get("text", ""),
            "token": outbound.get("token"),
            "kind": "reply",
        }))

    # ── 3. 硬性阻断 ──
    if node == "audit_block":
        events.append((EVENT_BLOCKED, {"rule_ids": patch.get("blocked_rule_ids") or []}))

    # ── 4. 转人工（与 send 可并行，所以单独一条）──
    if node == "human_handoff" and patch.get("handoff_ticket"):
        ticket = patch["handoff_ticket"] or {}
        events.append((EVENT_HANDOFF, {
            "ticket_id": ticket.get("ticket_id"),
            "priority": ticket.get("priority"),
            "reason": ticket.get("reason"),
            "text": patch.get("handoff_notice") or "",
        }))

    return events


def error_event(code: str, message: str, **extra: Any) -> tuple[str, dict]:
    return (EVENT_ERROR, {"code": code, "message": message, **extra})

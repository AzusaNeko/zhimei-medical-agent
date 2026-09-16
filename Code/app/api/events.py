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
#: 节点执行轨迹 —— **默认不发送**，只有请求带 ?trace=1 时才有。
#:
#: 为什么默认关掉：它是给演示与排障用的**执行元数据**，不是给终端用户看的。
#: 把 "硬性阻断 / 二次复核 / 修订预算" 这些内部关卡名摊在一个真实顾客面前，
#: 既没有意义，也等于把风控结构交底。所以它跟 status 是两回事：
#:   · status 是**文案**，固定几句，可以放心给用户看（"正在查阅审核资料…"）；
#:   · node  是**结构**，暴露的是图长什么样。
#: 用查询参数而不是全局开关，是为了让演示页和生产页共用同一套接口代码。
EVENT_NODE = "node"

#: 会话已被人工客服接管（`ai_enabled = false`）时，用户**仍然可以继续说话**：
#: 消息照常落库、转给坐席，但 **AI 不会作答**。
#:
#: ★ 这个事件与 409 `human_takeover` 不是一回事，别混：
#:   · 早期版本在接管期间直接返回 **409**，把用户挡在门外。后果很荒唐 ——
#:     顾客刚被告知"已为您转接人工客服"，下一句就发不出去了，只能干等，
#:     而且他想补充的"我疼得更厉害了"也没人能收到。
#:   · 现在改成 **200 + 本事件**：记下来、转过去、**不抢话**。
#:     "AI 不与坐席抢话"这条不变量依然成立 —— 它约束的是 AI 不许自动回复，
#:     不是用户不许说话。
EVENT_TAKEOVER = "human_takeover"


def takeover_event(text: str, *, ticket_id: str | None = None, accepted: bool = False,
                   agent_name: str | None = None, executed: bool = False) -> tuple[str, dict]:
    """接管期间的"已记录、未作答"事件。

    `executed` 显式写出来并恒为 False：这段逻辑也可能被"确认执行"走到
    （用户在接管期间点了确认），那时最要紧的一件事就是让客户端**明确知道
    操作没有执行** —— 对客系统里"看起来像成功了"是最坏的失败。
    """
    return (EVENT_TAKEOVER, {"text": text, "ticket_id": ticket_id,
                             "accepted": accepted, "agent_name": agent_name,
                             "executed": executed})


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


# ══════════════════════════════════════════════════════════════
#  节点输出摘要（**只在 trace 模式下发送**）
# ══════════════════════════════════════════════════════════════

#: 每个节点摘要的总长度上限（字符）。超出就截断并标注。
#: 为什么必须有上限：有些节点的 patch 很大（kb_evidence 的证据列表、
#: risk_gate 的三份面板意见），原样塞进 SSE 会让一帧几万字符 ——
#: 演示页面卡住、日志爆炸，而且没人会真去读那么多。
NODE_DETAIL_LIMIT = 1800

#: 不进摘要的键。
#:
#: · audit_log —— 每个节点都会追加一条自己的事件，对"这个节点产出了什么"
#:   没有信息量（它说的就是"我跑过了"，而轨迹本身已经在说这件事），
#:   留着只会把真正有用的字段挤掉。
#: · hard_rule_hits_history —— 跨轮累积的历史转存，属于历史而非本轮产出。
#:
#: ★ `panel_reviews` **不能**跳过（我一开始跳了，是错的）：
#:   三个审查面板（review_medical / review_ad / review_privacy）**唯一的产出**
#:   就是这个字段。跳过它，点开审查节点只会看到"（无输出）" ——
#:   而"这三位分别判了什么"恰恰是最值得看的东西。
#:   它确实会累积多轮，但 _brief() 已经把列表截到 4 项、字典深度截到 2 层，
#:   实测摘要最长 1702 字符，远在上限之内。
_DETAIL_SKIP = {"audit_log", "__interrupt__", "hard_rule_hits_history"}


def _brief(value: Any, depth: int = 0) -> Any:
    """把任意值压成"够看懂、又不会失控"的形态。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 400 else value[:400] + f"…（共 {len(value)} 字）"
    if isinstance(value, (list, tuple)):
        head = [_brief(v, depth + 1) for v in value[:4]]
        if len(value) > 4:
            head.append(f"…共 {len(value)} 项")
        return head
    if isinstance(value, dict):
        if depth >= 2:
            return f"<{len(value)} 个字段>"
        return {k: _brief(v, depth + 1) for k, v in list(value.items())[:10]}
    s = str(value)
    return s[:200] + ("…" if len(s) > 200 else "")


def node_detail(patch: Any) -> dict:
    """把某个节点写回的状态压成一小段可读摘要。

    ★ 这是**调试信息**，与 status 事件的性质完全不同，必须看清这个区别：

        status  —— 固定话术，给终端用户看的（"正在查阅审核资料…"）
        node    —— 图的执行结构 + 节点做出来的东西，给演示和排障看的

      所以它只在 `?trace=1` 时发送。而且这里会带出**未经审查的中间产物**
      （草稿、审查意见、检索到的证据原文）—— 这些内容在正常流程里
      是绝对不该给顾客看到的（顾客只应看到 final 里那份"已审正文"）。

      结论：**trace 模式绝不能在面向真实顾客的页面上开启**。
      聊天页里那个勾选框是演示开关，不是产品功能。
    """
    if not isinstance(patch, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k in _DETAIL_SKIP:
            continue
        out[k] = _brief(v)
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) <= NODE_DETAIL_LIMIT:
        return out
    # 超限：按"字段数保留 + 整体截断"处理，并明确标注被截断了
    trimmed: dict[str, Any] = {}
    for k in out:
        trimmed[k] = out[k]
        if len(json.dumps(trimmed, ensure_ascii=False, default=str)) > NODE_DETAIL_LIMIT - 60:
            trimmed.pop(k)
            break
    trimmed["_truncated"] = f"摘要超长已截断（原 {len(text)} 字符）"
    return trimmed

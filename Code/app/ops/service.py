"""
运营后台的领域逻辑：工单状态机 + 处置动作 + 不变量。

**不变量集中在这一层**，而不是散落在路由里 —— 因为它们是要被测试断言的东西：
  1. `accepted_at` 为空时，任何接口都不得向用户告知"人工已接入"
  2. 接单才能回复（避免没人负责的工单被随手回掉）
  3. 接单 → 关掉 AI；关单 → 恢复 AI（这是"AI 不与坐席抢话"的唯一开关）
  4. 坐席回复走**规则层轻校验**：block 级硬拦、revise 级软提示；不调 LLM（否则工单没法干活）
  5. 坐席不能代为执行预约/退费类操作 —— 只能引导用户走系统流程
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..runtime import Runtime
from .deps import Agent, mask_for

#: 工单状态机（见 Workflow/ops-console.md）
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open":        {"accepted", "closed"},
    "accepted":    {"in_progress", "escalated", "closed"},
    "in_progress": {"escalated", "closed"},
    "escalated":   {"in_progress", "closed"},
    "closed":      {"open"},            # reopen
}

#: SLA 基线（对应 mvp-config-rules.md §5.5 的"稳妥版"）
SLA_SECONDS = {"P0": 60, "P1": 600, "P2": 48 * 3600}

#: 坐席回复里出现这些词，说明他想直接替用户办事 —— 必须拦住
OPERATION_WORDS = ["已为您取消", "已为您改约", "已为您退款", "已帮您退", "已经退给您", "已扣费"]


class OpsError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


#: **待处理**的状态集合。队列默认只看这些。
#:
#: ★ 为什么默认要过滤：队列是"**要做的事**"，不是"做过的事"。
#:   已结束（closed）的工单留在里面，坐席每次打开面板都要重新扫一遍历史，
#:   真正待接单的那张反而被埋在下面 —— 而且它的 `msg_count` 还会变，
#:   让面板误以为"这个顾客又说话了"，在工单结束之后继续弹提示。
#:   要看历史得显式要（`statuses=["closed"]`）。
ACTIVE_STATUSES: tuple[str, ...] = ("open", "accepted", "in_progress", "escalated")


# ════════════════════════════════════════════════════════════════
#  队列与详情
# ════════════════════════════════════════════════════════════════
async def list_queue(rt: Runtime, agent: Agent, *, statuses: list[str] | None = None,
                     priorities: list[str] | None = None, include_test: bool = False,
                     limit: int = 50) -> list[dict]:
    # ★ 不给 statuses 就默认只要待处理的 —— 见 ACTIVE_STATUSES 的说明。
    #   放在**服务层**而不是路由层：这样所有调用方（REST、SSE 快照）自动一致，
    #   不会出现"接口过滤了、推送没过滤"这种两套口径的老毛病。
    effective = list(statuses) if statuses else list(ACTIVE_STATUSES)
    rows = await rt.deps.pg.list_tickets(statuses=effective, priorities=priorities,
                                         include_test=include_test, limit=limit)
    now = datetime.now(timezone.utc)
    out = []
    for t in rows:
        created = _parse(t.get("created_at"))
        waited = int((now - created).total_seconds()) if created else 0
        sla = SLA_SECONDS.get(t.get("priority", "P2"), SLA_SECONDS["P2"])
        out.append({
            "ticket_id": t.get("ticket_id"),
            "session_id": t.get("session_id"),
            "reason": t.get("reason"),
            "priority": t.get("priority"),
            "status": t.get("status"),
            "wait_seconds": waited,
            "sla_seconds": sla,
            "sla_breached": waited > sla and t.get("status") == "open",
            "accepted_at": t.get("accepted_at"),
            "assigned_to": t.get("assigned_to"),
            # 测试工单带标记：队列默认已经把它们过滤掉了，但"显示测试工单"
            # 打开时，界面上必须能一眼看出哪张是测试的 —— 否则就等于没标记。
            "is_test": bool(t.get("is_test")),
            # 会话里的消息条数。接管期间用户仍然可以发言（AI 不答但要转给坐席），
            # 坐席台靠这个计数变化发现"顾客又补充了内容"，从而自动刷新详情 ——
            # 否则坐席会一直盯着一份他接单那一刻的快照。
            "msg_count": int(t.get("msg_count") or 0),
            # 队列行必须能看出"这事大概是什么"，否则坐席得点进去才知道要不要先接
            "context": _context_line(t.get("profile_summary"), t.get("reason")),
            # ★ 发起人（**脱敏**）。坐席在队列里需要知道"这是谁的事"才好排优先级，
            #   但队列是一屏几十行的列表，没必要把姓名全摊开 ——
            #   只留姓，后面一律打码。要看全名点进详情（那里按角色权限决定）。
            "user_name": _mask_name(t.get("user_name")),
        })
    return out


#: 队列行里给名字打码。
def _mask_name(name: Any) -> str:
    """只保留**第一个字**，其余打码。拿不到名字就给个中性占位。

    ★ 为什么连"姓 + 名"都不给：队列是**一屏几十行**的列表，
      坐席扫一眼只需要区分"是不是同一个人的事"。全名在这个场景里没有增量信息，
      却把一批顾客的姓名集中暴露在一个页面上 —— 收益为零、风险非零。
      要看全名，点进详情（那里有按角色的明文权限控制，而且一次只看一个人）。

    ★ 单字名字（如"李"）原样返回：再打码就没有信息了，而且那种名字本身极短。
    """
    raw = str(name or "").strip()
    if not raw:
        return "（未登记）"
    if len(raw) == 1:
        return raw
    return raw[0] + "*" * (len(raw) - 1)


async def get_detail(rt: Runtime, agent: Agent, ticket_id: str) -> dict:
    ticket = await rt.deps.pg.get_ticket(ticket_id)
    if ticket is None:
        raise OpsError("ticket_not_found", "工单不存在", status=404)

    messages = await rt.deps.pg.list_messages(ticket.get("session_id") or "", limit=50)
    events = await rt.deps.pg.list_handoff_events(ticket_id)
    report = ticket.get("risk_report") or {}

    # ★ 发起人：坐席必须知道"这是谁的事"，否则接起来还得先问一遍。
    #   邮箱按角色脱敏 —— 与手机号同一条规则（service 看不到完整联系方式，
    #   doctor / compliance / admin 可以）。显示名与 user_id 不脱敏：
    #   前者是用户自己起的名字，后者是内部标识，都不属于敏感个人信息。
    user = await rt.deps.pg.get_user(ticket.get("user_id"))
    if user:
        user_out = {
            "user_id": user.get("user_id"),
            "display_name": user.get("display_name"),
            "email": mask_for(agent, user.get("email") or "") or None,
            "email_verified": user.get("email_verified"),
            "registered_at": user.get("created_at"),
        }
    else:
        # 用户在转人工之后被删除了 —— 如实说明，不要显示成空白让人以为是 bug
        user_out = {"user_id": ticket.get("user_id"), "display_name": None,
                    "email": None, "email_verified": None, "registered_at": None,
                    "_note": "该用户记录已不存在" if ticket.get("user_id") else "工单未绑定用户"}

    # 会话详情要包含"AI 未发出的草稿与审查意见" —— 坐席需要知道 AI 想说什么、
    # 为什么被拦下来，这决定了他怎么接话
    return {
        "ticket": {
            "ticket_id": ticket.get("ticket_id"),
            "session_id": ticket.get("session_id"),
            "user_id": ticket.get("user_id"),
            "reason": ticket.get("reason"),
            "priority": ticket.get("priority"),
            "status": ticket.get("status"),
            "profile_summary": ticket.get("profile_summary"),
            "accepted_at": ticket.get("accepted_at"),
            "assigned_to": ticket.get("assigned_to"),
            "closed_at": ticket.get("closed_at"),
            "close_reason": ticket.get("close_reason"),
            "created_at": ticket.get("created_at"),
            "is_test": bool(ticket.get("is_test")),
            # ★ 这条是硬约束：没接单就不能告诉用户"人工已接入"
            "human_joined": bool(ticket.get("accepted_at")),
        },
        "user": user_out,
        "last_turns": ticket.get("last_turns") or messages[-10:],
        "messages": [{"role": m.get("role"), "content": _mask(agent, m.get("content", "")),
                      "review_kind": m.get("review_kind"), "risk_level": m.get("risk_level"),
                      "created_at": m.get("created_at")} for m in messages],
        "events": events,
        "risk_report": {
            "verdict": report.get("verdict"),
            "risk_level": report.get("risk_level"),
            "hard_rule_hits": report.get("hard_rule_hits") or [],
            "panel_reviews": report.get("panel_reviews") or [],
            "evidence": report.get("evidence") or [],
            "emergency_hits": report.get("emergency_hits") or [],
            "draft": _mask(agent, report.get("draft") or ""),
            "escalation_used": report.get("escalation_used"),
            # 同族复核要显式标示 —— 可解释比好看重要
            "escalation_independent": report.get("escalation_independent"),
            "same_family_review": bool(report.get("escalation_used")
                                       and report.get("escalation_independent") is False),
            # ★ 顾客删掉了这个对话。必须显式告诉坐席 ——
            #   否则他看到的是一个**空白的**会话详情，会以为系统坏了，
            #   或者以为顾客什么都没说过。两种误判都会让他接错话。
            "transcript_deleted": bool(report.get("transcript_deleted")),
        },
    }


# ════════════════════════════════════════════════════════════════
#  处置动作
# ════════════════════════════════════════════════════════════════
async def accept(rt: Runtime, agent: Agent, ticket_id: str) -> dict:
    ticket = await _require_ticket(rt, ticket_id)
    # ★ 报错顺序按"对坐席最有解释力"排：
    #   已关闭 → 说"已关闭，需先重新打开"；已被人接 → 说"已被接单"；
    #   剩下的才是状态机非法转移。反过来只会得到绕口的"accepted 不能变为 accepted"。
    if (ticket.get("status") or "") == "closed":
        raise OpsError("ticket_closed", "工单已关闭，需先重新打开", status=409)
    if ticket.get("accepted_at"):
        raise OpsError("already_accepted", "该工单已被接单", status=409)
    _assert_transition(ticket, "accepted")

    updated = await rt.deps.pg.update_ticket(
        ticket_id, status="accepted", assigned_to=agent.agent_id,
        accepted_at=datetime.now(timezone.utc).isoformat())
    await rt.deps.pg.log_handoff_event(ticket_id, "accept", f"agent:{agent.agent_id}",
                                       {"name": agent.name, "role": agent.role})
    # ★ 接单 = 关掉 AI，之后用户消息直接进人工队列
    await rt.deps.pg.set_ai_enabled(ticket.get("session_id") or "", False)
    return {"ticket": updated, "ai_enabled": False,
            "notice": "已接管，可以回复用户了"}


async def reply(rt: Runtime, agent: Agent, ticket_id: str, text: str) -> dict:
    ticket = await _require_ticket(rt, ticket_id)
    if not ticket.get("accepted_at"):
        raise OpsError("not_accepted", "必须先接单才能回复（避免没人负责的工单被随手回掉）",
                       status=409)
    if not text.strip():
        raise OpsError("empty_reply", "回复内容不能为空")

    # ── 规则层轻校验（毫秒级，不调 LLM）──
    hits = rt.deps.rules.check_text(text)
    blocked = [h.as_dict() for h in hits if h.action == "block"]
    warned = [h.as_dict() for h in hits if h.action != "block"]
    if blocked:
        raise OpsError("blocked_content",
                       "命中禁止发送项：" + "、".join(h["rule_id"] for h in blocked),
                       status=403)
    if any(w in text for w in OPERATION_WORDS):
        raise OpsError("no_agent_operation",
                       "坐席不能代为执行预约/退费操作，请引导用户走系统流程", status=403)

    await rt.deps.pg.save_agent_message(session_id=ticket.get("session_id") or "",
                                        agent_id=agent.agent_id, content=text,
                                        ticket_id=ticket_id)
    await rt.deps.pg.log_handoff_event(ticket_id, "reply", f"agent:{agent.agent_id}",
                                       {"content_hash": rt.deps.security.hash_text(text),
                                        "rule_hits": [h.rule_id for h in hits]})
    if ticket.get("status") == "accepted":
        await rt.deps.pg.update_ticket(ticket_id, status="in_progress")
    return {"sent": True, "warnings": warned}


async def escalate(rt: Runtime, agent: Agent, ticket_id: str, reason: str = "") -> dict:
    ticket = await _require_ticket(rt, ticket_id)
    _assert_transition(ticket, "escalated")
    updated = await rt.deps.pg.update_ticket(ticket_id, status="escalated", priority="P1")
    await rt.deps.pg.log_handoff_event(ticket_id, "escalate", f"agent:{agent.agent_id}",
                                       {"reason": reason})
    return {"ticket": updated}


async def close(rt: Runtime, agent: Agent, ticket_id: str, reason: str) -> dict:
    ticket = await _require_ticket(rt, ticket_id)
    _assert_transition(ticket, "closed")
    if not (reason or "").strip():
        raise OpsError("reason_required", "关闭工单必须填写原因")
    updated = await rt.deps.pg.update_ticket(
        ticket_id, status="closed", close_reason=reason,
        closed_at=datetime.now(timezone.utc).isoformat())
    await rt.deps.pg.log_handoff_event(ticket_id, "close", f"agent:{agent.agent_id}",
                                       {"reason": reason})
    # 关单 → 恢复 AI 自动应答
    await rt.deps.pg.set_ai_enabled(ticket.get("session_id") or "", True)
    return {"ticket": updated, "ai_enabled": True}


async def reopen(rt: Runtime, agent: Agent, ticket_id: str) -> dict:
    ticket = await _require_ticket(rt, ticket_id)
    _assert_transition(ticket, "open")
    updated = await rt.deps.pg.update_ticket(ticket_id, status="open", closed_at=None,
                                             close_reason=None)
    await rt.deps.pg.log_handoff_event(ticket_id, "reopen", f"agent:{agent.agent_id}")
    await rt.deps.pg.set_ai_enabled(ticket.get("session_id") or "", False)
    return {"ticket": updated}


async def misreport(rt: Runtime, agent: Agent, ticket_id: str, *, source: str, ref_id: str,
                    raw_message: str, verdict: str, note: str = "") -> dict:
    """标记误报 —— 这是紧急词表调优的唯一数据来源，必须有这个入口。"""
    await _require_ticket(rt, ticket_id)
    await rt.deps.pg.save_misreport(ticket_id=ticket_id, source=source, ref_id=ref_id,
                                    raw_message=raw_message, verdict=verdict, note=note)
    await rt.deps.pg.log_handoff_event(ticket_id, "mark_misreport", f"agent:{agent.agent_id}",
                                       {"source": source, "ref_id": ref_id, "verdict": verdict})
    return {"ok": True}


async def metrics(rt: Runtime, agent: Agent) -> dict:
    data = await rt.deps.pg.ops_metrics()
    data["sla_baseline"] = SLA_SECONDS
    return data


# ════════════════════════════════════════════════════════════════
#  内部工具
# ════════════════════════════════════════════════════════════════
async def _require_ticket(rt: Runtime, ticket_id: str) -> dict:
    ticket = await rt.deps.pg.get_ticket(ticket_id)
    if ticket is None:
        raise OpsError("ticket_not_found", "工单不存在", status=404)
    return ticket


def _assert_transition(ticket: dict, target: str) -> None:
    current = ticket.get("status") or "open"
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise OpsError("invalid_transition",
                       f"工单状态 {current} 不能直接变为 {target}", status=409)


def _context_line(profile_summary: str | None, reason: str | None) -> str:
    summary = (profile_summary or "").strip().splitlines()
    head = summary[0][:40] if summary else "（无画像摘要）"
    return f"{reason or 'other'} · {head}"


def _mask(agent: Agent, text: str) -> str:
    if agent.sees_raw_pii:
        return text
    from ..services import text as T
    return T.mask(text)


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None

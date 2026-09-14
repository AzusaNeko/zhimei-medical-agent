"""
出口层：send / human_handoff / audit_block

铁律：**所有出站内容都必须持有放行凭据**。
唯一的例外是「预审通过的固定模板」（转人工提示、紧急提示）——
它们不走 per-message 审查，但同样写 chat_message 与审计，并且明确标记 review_kind='template'。
这个例外必须写在代码注释里，否则下一个人会以为这里漏了校验。
"""

from __future__ import annotations

from typing import Callable

from ...prompts import emergency as EM
from ...services import text as T
from ...services.deps import Deps


def make_exit_nodes(deps: Deps) -> dict[str, Callable]:

    # ══════════════ 唯一的出站口（必须持凭据）══════════════
    async def send(state: dict) -> dict:
        content = (state.get("draft") or {}).get("content", "")
        if not content:
            return {"audit_log": [{"event": "send_skipped", "reason": "empty_content"}]}

        token = state.get("release_token")
        kind = state.get("review_kind") or "content"
        # ★ 强制校验：内容 / 方案 / 审查类别三者必须与凭据一致
        deps.security.verify_token(token, content=content,
                                   plan_hash=state.get("plan_hash"), review_kind=kind)

        masked = T.mask(content)     # 出站前再脱敏一次（双保险）
        await deps.pg.save_message(
            session_id=state.get("session_id", ""), turn_id=state.get("turn_id"),
            role="assistant", content=masked,
            content_hash=deps.security.hash_text(content), release_token=token,
            risk_level=state.get("risk_level"), review_kind=kind,
            meta={"intents": state.get("intents") or []})

        return {"outbound": {"kind": "reply", "text": masked, "token": token},
                "audit_log": [{"event": "sent", "review_kind": kind,
                               "risk_level": state.get("risk_level")}]}

    # ══════════════ 人工接管 ══════════════
    async def human_handoff(state: dict) -> dict:
        """
        创建工单 + 发送【预审模板】的转接提示。
        注意：模板不走 per-message 审查（这是唯一被允许的例外），但同样落库与审计。
        """
        profile = await deps.pg.load_profile(state.get("user_id"))
        turns = await deps.pg.recent_turns(state.get("session_id", ""), limit=5)
        reason, priority = _handoff_reason(state)

        ticket = await deps.pg.create_handoff_ticket(
            session_id=state.get("session_id", ""),
            thread_id=state.get("thread_id") or state.get("session_id", ""),
            user_id=state.get("user_id"),
            reason=reason, priority=priority,
            profile_summary=(profile or {}).get("summary") or "（无画像摘要）",
            last_turns=turns,
            risk_report={
                "verdict": state.get("verdict"),
                "risk_level": state.get("risk_level"),
                "hard_rule_hits": state.get("hard_rule_hits"),
                "panel_reviews": state.get("panel_reviews"),
                "escalation_used": state.get("escalation_used"),
                "escalation_independent": state.get("escalation_independent"),
                "emergency_hits": state.get("emergency_hits"),
                "draft": (state.get("draft") or {}).get("content", ""),
            })

        # 转接提示固定用"已转接"话术：紧急场景下预警提示已经由 send 发出去了，
        # 这里再发一遍同样的急诊模板会让用户收到两条重复长文。
        notice = EM.HANDOFF_NOTICE
        await deps.pg.save_message(
            session_id=state.get("session_id", ""), turn_id=state.get("turn_id"),
            role="assistant", content=notice, review_kind="template",
            risk_level=state.get("risk_level"),
            meta={"ticket_id": ticket["ticket_id"], "priority": priority, "reason": reason})

        return {"handoff_ticket": ticket,
                # ★ 用 handoff_notice 而不是 outbound：紧急场景会与 send 并行走，
                #   两者同时写 outbound 会撞 InvalidUpdateError
                "handoff_notice": notice,
                "audit_log": [{"event": "handoff_created", "reason": reason,
                               "priority": priority, "ticket_id": ticket["ticket_id"]}]}

    # ══════════════ 硬性阻断（不产生任何业务出站内容）═════════════
    async def audit_block(state: dict) -> dict:
        rule_ids = T.dedupe([h["rule_id"] for h in (state.get("hard_rule_hits") or [])])
        await deps.pg.write_hard_rule_hits(state.get("hard_rule_hits") or [])
        await deps.pg.save_message(
            session_id=state.get("session_id", ""), turn_id=state.get("turn_id"),
            role="system", content=f"[blocked] rules={rule_ids}",
            review_kind="block", meta={"rule_ids": rule_ids})
        return {"blocked_rule_ids": rule_ids,
                "outbound": {"kind": "blocked", "text": "", "rule_ids": rule_ids},
                "audit_log": [{"event": "blocked", "rule_ids": rule_ids}]}

    return {"send": send, "human_handoff": human_handoff, "audit_block": audit_block}


def _handoff_reason(state: dict) -> tuple[str, str]:
    if state.get("emergency"):
        tier = _emergency_tier(state)
        return ("emergency", "P0" if tier == "P0" else "P1")
    if state.get("blocked_rule_ids"):
        return ("blocked", "P1")
    verdict = state.get("verdict")
    if verdict == "human":
        return ("risk_high", "P1")
    if state.get("retry_ok") is False:
        return ("revision_exhausted", "P2")
    if state.get("plan") and (state.get("plan") or {}).get("mode") == "clarify_exhausted":
        return ("clarify_exhausted", "P2")
    if state.get("verdict") == "need_info":
        return ("need_info", "P2")
    return ("other", "P2")


def _emergency_tier(state: dict) -> str | None:
    """取命中的最高档（不能直接取 hits[0]：列表里可能先出现 P1 泛词）。"""
    order = {"P0": 0, "P1": 1, "P2": 2}
    hits = state.get("emergency_hits") or []
    tiers = [h.get("tier") for h in hits if h.get("tier")]
    if not tiers:
        return None
    return sorted(tiers, key=lambda t: order.get(t, 9))[0]

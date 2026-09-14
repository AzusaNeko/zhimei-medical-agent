"""
修订回路与终止控制：feedback / retry_check / revise / plan_void

★ revise 是【全图唯一】用 Command(goto=...) 的节点：
  目标要运行时才知道（state.origin_agent），无法用静态路由表表达。
  图代码里那一条"回到草稿产出层"的箭头，就是这里的 Command。
"""

from __future__ import annotations

from typing import Callable

from langgraph.types import Command

from ...prompts import release as P
from ...services import text as T
from ...services.deps import Deps
from ..state import begin_review_round
from ...prompts.emergency import HANDOFF_NOTICE


def make_revision_nodes(deps: Deps) -> dict[str, Callable]:

    # ══════════════ 记录修改意见并累加计数 ══════════════
    async def feedback(state: dict) -> dict:
        """
        把审查发现整理成可执行的修改清单；计数【只在代码里】累加，绝不让模型数数。
        """
        findings = [
            f"{f.get('span', '')} → {f.get('suggest_fix', '')}"
            for r in (state.get("panel_reviews") or [])
            for f in (r.get("findings") or [])
        ]
        hard_hits = [f"{h['rule_id']} {h['title']}（命中：{h['span']}）"
                     for h in (state.get("hard_rule_hits") or [])]
        try:
            out = await deps.llm.structured(
                "revision_feedback", _FeedbackOut, system=P.REVISION_FEEDBACK_SYSTEM,
                user=P.REVISION_FEEDBACK_USER.format(
                    draft=(state.get("draft") or {}).get("content", ""),
                    findings="\n".join(findings) or "（无）",
                    hard_hits="\n".join(hard_hits) or "（无）"))
            feedback_list = list(out.feedback) or findings or hard_hits
        except Exception:  # noqa: BLE001
            feedback_list = findings + hard_hits

        n = int(state.get("revision_count", 0)) + 1
        return {
            "review_feedback": T.dedupe(feedback_list),
            "revision_count": n,
            "audit_log": [{"event": "revision_feedback", "round": state.get("review_round"),
                           "revision": n, "feedback": feedback_list}],
        }

    # ══════════════ 复审预算检查 ══════════════
    async def retry_check(state: dict) -> dict:
        n = int(state.get("revision_count", 0))
        ok = n <= deps.settings.max_revision
        return {"retry_ok": ok,
                "audit_log": [{"event": "retry_check", "revision": n,
                               "limit": deps.settings.max_revision, "retry": ok}]}

    # ══════════════ 定向修订（唯一的动态跳转点）═════════════
    async def revise(state: dict) -> Command:
        origin = state.get("origin_agent") or _guess_origin(state)
        return Command(
            goto=origin,
            update={
                "review_feedback": state.get("review_feedback") or [],
                "audit_log": [{"event": "revise_dispatch", "to": origin,
                               "revision": state.get("revision_count", 0)}],
            },
        )

    # ══════════════ 作废方案（用户否认 / 方案过期 / 哈希不符）═════════════
    async def plan_void(state: dict) -> dict:
        reason = {"mismatch": "方案已变更，需重新确认", "denied": "已取消本次操作"}\
            .get(state.get("confirm_result") or "", "本次操作未执行")
        content = (f"{reason}，未做任何改动。如需重新办理请告诉我。\n"
                   f"（本条为通用回复，具体仍以医生面诊评估为准。）")
        await deps.pg.save_confirmation(session_id=state.get("session_id", ""),
                                        plan_hash=state.get("plan_hash") or "",
                                        confirmed=False,
                                        raw_reply=str(state.get("confirm_result")))
        # 作废回复同样要过审 —— 保证"任何出站都有凭据"这条不破例
        return {
            "drafts": [{"agent": "plan_void", "turn_id": state.get("turn_id"),
                        "revision": 0, "content": content, "citations": [], "gaps": [],
                        "route_hint": None, "risk_tags": [], "operation": None}],
            "plan": {"targets": [], "mode": "plan_void"},
            # ★ 本轮只保留作废回复：否则旧的"操作方案"草稿会被 reducer 保留，
            #   aggregate 把两份内容拼在一起 → operation 还在 → 又挂起一次等用户确认
            "supersede_drafts": True,
            "operation": None,
            "plan_hash": None,
            "audit_log": [{"event": "plan_void", "result": state.get("confirm_result")}],
        }

    return {"feedback": feedback, "retry_check": retry_check,
            "revise": revise, "plan_void": plan_void}


def _guess_origin(state: dict) -> str:
    """回退目标：按本轮实际产出草稿的 Agent 推断（正常情况下 origin_agent 已由 gate 写入）"""
    order = ["k_agent", "r_agent", "c_agent", "b_agent", "p_agent", "clarify", "emergency_draft"]
    agents = {d.get("agent") for d in (state.get("drafts") or [])}
    for a in order:
        if a in agents:
            return a
    return "k_agent"


# 单独定义，避免从 prompts 反向导入 schema
from ...graph.schemas import RevisionFeedbackOut as _FeedbackOut  # noqa: E402

"""
入口与调度层节点（总控的第 1、2 个承担点 + 意图识别 Agent）。

对应图上的：normalize / emergency_screen / classify / dispatch

注意一处约定：
  图上的菱形 = 路由点。但 `dispatch` 在代码里【既是节点也是路由】：
    · dispatch 节点负责"规划"（算出本轮要跑哪些专业模块、是否澄清、是否兜底）
    · after_dispatch 路由负责"分派"（返回 Send 列表或目标节点名）
  这样图与代码 1:1 对应，也让"规划"这件事可以被单独测试。
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from langgraph.types import Send

from ...prompts import understand as P
from ...prompts.system import render_recent_turns, render_slots
from ...settings import Settings
from ...services import text as T
from ...services.deps import Deps
from .. import progress
from ..schemas import ClassifyOut, EmergencyTriage, SupervisorPlan


def make_intake_nodes(deps: Deps) -> dict[str, Callable]:
    settings: Settings = deps.settings

    # ══════════════ normalize：纯规则 ══════════════
    async def normalize(state: dict) -> dict:
        """
        总控第 1 个承担点：会话绑定 + 输入清洗 + 生成本轮 turn_id + 重置本轮字段。
        ★ 只重置「本轮」字段；clarify_count 是跨轮次预算，绝不能在这里清零。
        """
        session_id = state.get("session_id") or ""

        # ★ 顺序至关重要：**先建会话行并绑定用户，再读权限**。
        #   反过来的话（先 load_auth 再 ensure_session），首次会话里用户还没绑上，
        #   load_auth 返回 verified=False，这个错误的权限对象会一路存进 state，
        #   表现为"所有预约类请求一律被判 need_info"——而表面上一切正常，极难排查。
        flags_note = None
        ensure = getattr(deps.pg, "ensure_session", None)
        if ensure is not None:
            try:
                await ensure(session_id, channel=state.get("channel", "cli"),
                             user_id=state.get("user_id"))
            except Exception as exc:  # noqa: BLE001
                flags_note = str(exc)[:120]

        session = await deps.pg.get_session(session_id)
        if session.get("ai_enabled") is False:
            # 已被人工接管 → 本轮不进图（API 层也会拦，这里是双保险）
            return {"audit_log": [{"event": "blocked_by_human_takeover",
                                   "session_id": session_id}]}

        auth = await deps.pg.load_auth(session_id)
        cleaned = T.clean(state.get("user_input", ""))
        flags = T.prescan(cleaned)

        # ★ 取「之前的对话」必须在**保存本轮消息之前**。
        #   反过来的话，用户这句话会作为最后一条出现在"最近对话"里，
        #   于是 classify 看到的是"用户刚说了一遍"的重复内容 —— 白白占 token，
        #   还可能让模型把指代解析到它自己身上。
        #
        # ★ 为什么必须在这里取、而不是让各节点自己查库：
        #   这个字段原本只有 state 声明和读取，**没有任何地方写入**，
        #   于是 classify 的 Prompt 里"最近对话"永远是"（无）"。
        #   后果就是用户上一轮刚讲过"热玛吉和超声炮"，这一轮问"那个怎么样"，
        #   模型完全接不上，只会反问"您说的是哪个项目"—— 用户会感觉它失忆了。
        try:
            recent_turns = await deps.pg.recent_turns(session_id, limit=6)
        except Exception as exc:  # noqa: BLE001
            # 取历史失败不能阻断对话，但留痕
            recent_turns = []
            flags = {**flags, "_history_failed": [str(exc)[:120]]}

        # ★ 用户消息必须落库：转人工时随工单携带的"最近 5 轮"就读这张表，
        #   不存的话坐席只能看到 AI 说过什么，看不到用户到底问了什么。
        turn_id = str(uuid.uuid4())
        try:
            await deps.pg.save_message(session_id=session_id, turn_id=turn_id,
                                       role="user", content=cleaned,
                                       meta={"channel": state.get("channel", "")})
        except Exception as exc:  # noqa: BLE001
            # 落库失败不阻断对话，但必须留痕
            flags = {**flags, "_save_failed": [str(exc)[:120]]}
        if flags_note:
            flags = {**flags, "_session_ensure_failed": [flags_note]}

        return {
            "thread_id": session.get("thread_id") or session_id,
            "turn_id": turn_id,
            "user_id": auth.get("user_id") or session.get("user_id"),
            "auth": auth,
            "user_input": cleaned,
            "slot_flags": flags,
            "recent_turns": recent_turns,
            # ── 每轮重置（漏一个就会出现"上一轮的东西漏到这一轮"）──
            "review_round": 0,
            "revision_count": 0,
            "review_feedback": [],
            "confirmed": None,
            "confirm_result": None,
            "execution_result": None,
            "outbound": None,
            "handoff_ticket": None,
            "handoff_notice": None,
            "drafts": [],
            "emergency": False,
            "emergency_hits": [],
            "emergency_hint": False,
            "plan": None,
            "operation": None,
            "plan_hash": None,
            "release_token": None,
            "output_kind": "",
            "force_human": False,
            "supersede_drafts": False,
            "final_ok": False,
            "retry_ok": True,
            "review_input_ok": True,
            "kb_evidence": [],
            "kb_draft_content": "",
            "kb_citations": [],
            "kb_gaps": [],
            "kb_claim_checks": [],
            "kb_verify_round": 0,
            "kb_exit": None,
            # ── 刻意不重置：clarify_count ──
            "audit_log": [{"event": "normalize", "user_id": auth.get("user_id"),
                           "verified": auth.get("verified", False),
                           "pii_flags": list(flags.keys()),
                           "clarify_count": int(state.get("clarify_count", 0))}],
        }

    # ══════════════ emergency_screen：规则优先 + 模型兜底 ══════════════
    async def emergency_screen(state: dict) -> dict:
        """
        规则层先跑（毫秒级、可解释）；规则没命中才问模型，且模型【只能加不能减】。
        """
        raw = state.get("user_input", "")
        hits = deps.rules.match_emergency(raw)
        tier = deps.rules.emergency_tier(hits)

        if tier is None and state.get("emergency_hint"):
            # 意图识别标了提示但规则没命中：交给模型兜底判断，只回答"是否升级"
            try:
                verdict = await deps.llm.structured(
                    "emergency_triage", EmergencyTriage,
                    system=P.EMERGENCY_TRIAGE_SYSTEM,
                    user=P.EMERGENCY_TRIAGE_USER.format(user_input=raw))
                if verdict.escalate:
                    tier = "P1"
                    hits = [{"tier": "P1", "cluster": "model_hint", "term": "model",
                             "clause": raw[:40]}]
            except Exception as exc:  # noqa: BLE001
                # 兜底失败不等于安全 —— 保留提示，按 P1 处理
                tier = "P1"
                hits = [{"tier": "P1", "cluster": "fallback", "term": "hint",
                         "clause": raw[:40]}]
                return {"emergency": True, "emergency_hits": hits, "priority": "emergency",
                        "audit_log": [{"event": "emergency_model_fallback", "error": str(exc)}]}

        if tier is None:
            return {"emergency": False, "priority": "normal"}

        return {
            "emergency": True,
            "priority": "emergency",
            "emergency_hits": [h if isinstance(h, dict) else h.as_dict() for h in hits],
            "audit_log": [{"event": "emergency_hit", "tier": tier,
                           "lexicon_version": deps.rules.em_version,
                           "hits": [h if isinstance(h, dict) else h.as_dict() for h in hits]}],
        }

    # ══════════════ classify：意图识别 Agent ══════════════
    async def classify(state: dict) -> dict:
        """
        职责边界：只把自然语言变成结构化字段。
        不做路由（路由是规则表的事）、不做医学判断、不生成用户可见文本。
        """
        ents = deps.rules.entities
        progress.emit("classify")          # 过程流式：固定文案，不含模型输出
        payload = P.CLASSIFY_USER.format(
            now=deps.now_iso(),
            projects="、".join(ents.get("projects", [])),
            stores="、".join(ents.get("stores", [])),
            doctors="、".join(ents.get("doctors", [])),
            history_slots=render_slots(state.get("slots")),
            recent_turns=render_recent_turns(state.get("recent_turns", [])),
            user_input=state.get("user_input", ""),
        )
        try:
            out: ClassifyOut = await deps.llm.structured(
                "classify", ClassifyOut, system=P.CLASSIFY_SYSTEM, user=payload)
        except Exception as exc:  # noqa: BLE001
            # 两级降级：先关键词路由，再澄清
            guess = deps.rules.keyword_route(state.get("user_input", ""))
            mode = "keyword" if guess else "clarify"
            return {
                "intents": guess or ["clarify"],
                "slots": state.get("slots", {}),
                "uncertain": True,
                "audit_log": [{"event": "classify_degraded", "mode": mode, "error": str(exc)}],
            }

        # ★ 用户这次说清楚了 → 澄清预算复原；仍不明确则保留计数
        reset = {} if "clarify" in out.intents else {"clarify_count": 0}
        merged_slots = dict(state.get("slots") or {})
        merged_slots.update({k: v for k, v in out.slots.model_dump().items() if v})

        return {
            "intents": list(out.intents),
            "slots": merged_slots,
            "emergency_hint": out.emergency_hint,
            **reset,
            "audit_log": [{"event": "classify", "intents": list(out.intents),
                           "slots": {k: v for k, v in merged_slots.items() if v},
                           "time_note": out.time_note, "entity_note": out.entity_note,
                           "confidence": out.confidence}],
        }

    # ══════════════ dispatch：任务规划（纯规则，必要时才问模型）══════════════
    async def dispatch(state: dict) -> dict:
        """
        总控第 2 个承担点。规则表能覆盖的组合一律不问模型（省成本 + 编排确定性）。
        """
        intents = deps.rules.resolve_intents(state.get("intents", []))
        plan: dict[str, Any] = {"intents": intents, "targets": deps.rules.targets_for(intents),
                                "mode": "rule"}
        # 子图内部进度不冒泡到父图流，这里替知识科普子图先发一条
        if "k_agent" in plan["targets"]:
            progress.emit("retrieve")

        if state.get("emergency"):
            plan.update({"mode": "emergency", "targets": ["emergency_draft"]})
            return {"plan": plan}

        if not intents:
            # 意图不明确：预算内追问，超预算降级兜底（不新增节点）
            exhausted = int(state.get("clarify_count", 0)) >= settings.max_clarify
            has_slot = any(v for v in (state.get("slots") or {}).values())
            mode = "clarify_exhausted" if exhausted else "clarify"
            target = ("k_agent" if has_slot else "human_handoff") if exhausted else "clarify"
            plan.update({"mode": mode, "targets": [target]})
            return {"plan": plan}

        if len(intents) > 1 and _needs_llm_planning(intents):
            try:
                out: SupervisorPlan = await deps.llm.structured(
                    "supervisor_plan", SupervisorPlan,
                    system=P.SUPERVISOR_PLAN_SYSTEM,
                    user=P.SUPERVISOR_PLAN_USER.format(
                        user_input=state.get("user_input", ""),
                        intents="、".join(intents),
                        slots=render_slots(state.get("slots")),
                        turn_no=int(state.get("review_round", 0))))
                ordered = [i for i in out.ordered if i in deps.rules.routing] or intents
                plan.update({"mode": "llm", "intents": ordered,
                             "targets": deps.rules.targets_for(ordered),
                             "reason": out.reason, "defer": out.defer,
                             "need_human": out.need_human, "human_reason": out.human_reason})
                if out.need_human:
                    plan.update({"mode": "human", "targets": ["human_handoff"]})
            except Exception as exc:  # noqa: BLE001
                plan["llm_error"] = str(exc)   # 规划失败就按规则结果走，不阻断

        return {"plan": plan}

    return {"normalize": normalize, "emergency_screen": emergency_screen,
            "classify": classify, "dispatch": dispatch}


def _needs_llm_planning(intents: list[str]) -> bool:
    """
    哪些多意图组合需要 LLM 参与？
    规则表已经覆盖的常见组合（如 booking + knowledge_edu）直接走规则，不问模型。
    """
    covered = {
        frozenset({"booking", "knowledge_edu"}),
        frozenset({"knowledge_edu", "recommend"}),
        frozenset({"recommend", "clinic_info"}),
        frozenset({"booking", "clinic_info"}),
    }
    key = frozenset(intents)
    if key in covered:
        return False
    return len(intents) >= 3 or ("postcare" in intents and len(intents) >= 2)


# ══════════════ 路由：分派 ══════════════
def make_after_dispatch(deps: Deps) -> Callable:
    def after_dispatch(state: dict):
        """
        返回 list[Send] → 多意图并行；返回节点名 → 单路。
        紧急优先：只跑 emergency_draft，不跑常规 Agent。
        """
        plan = state.get("plan") or {}
        targets = list(plan.get("targets") or [])
        if not targets:
            return "clarify"
        if state.get("emergency"):
            return "emergency_draft"
        if len(targets) == 1:
            return targets[0]
        return [Send(t, {"slots": state.get("slots", {}), "auth": state.get("auth", {}),
                         "turn_id": state.get("turn_id"), "review_feedback":
                         state.get("review_feedback", [])}) for t in targets]

    return after_dispatch

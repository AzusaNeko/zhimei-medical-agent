"""
风险审查子图（主图里的 risk_gate）· 必经关卡。

同一份编译后的子图被主图调用两次：
  risk_gate（review_kind=content / operation / emergency）与 recheck（review_kind=result_reply）

⚠️ 与设计图的一处【有意差异】（必须知道，否则会以为代码漏了）：
   设计图里子图内部画了 feedback → retry_check → resubmit 的自有修订环。
   代码里【故意不实现它】—— 修订环只保留在父图一层（risk_gate → feedback →
   retry_check → revise → 回到原专业 Agent）。
   原因：两套修订预算会互相吃掉对方的次数，而且"退回谁修订"这件事只有父图知道。
   子图的职责收敛为一件事：**给出结论**（pass / revise / need_info / block / human）。
"""

from __future__ import annotations

import asyncio
from typing import Callable

from langgraph.graph import END, START, StateGraph

from ..prompts import review as P
from ..prompts.system import render_evidence
from ..services import text as T
from ..services.deps import Deps
from . import progress
from .schemas import EscalationOpinion, PanelOpinion
from .state import ZhimeiState

NEED_INFO_TEXT = ("需要您补充必要信息后我才能继续处理，请提供您的预约手机号或预约编号，"
                  "以便我核实归属。具体以医生面诊评估为准。")


def build_risk_subgraph(deps: Deps):
    settings = deps.settings

    def _issue(state: dict, *, verdict: str) -> dict:
        """签发放行凭据 + 写审计。只有"要发给用户"的结论才需要凭据。"""
        content = (state.get("draft") or {}).get("content", "")
        token = deps.security.sign_release(content=content,
                                           plan_hash=state.get("plan_hash"),
                                           review_kind=state.get("review_kind") or "content")
        return {"verdict": verdict, "release_token": token}

    async def _write_audit(state: dict, *, verdict: str, risk_level: str) -> None:
        content = (state.get("draft") or {}).get("content", "")
        await deps.pg.write_audit(
            thread_id=state.get("thread_id") or state.get("session_id", ""),
            session_id=state.get("session_id", ""),
            review_round=int(state.get("review_round", 0)),
            review_kind=state.get("review_kind") or "content",
            verdict=verdict, risk_level=risk_level,
            content_hash=deps.security.hash_text(content),
            panel_reviews=state.get("panel_reviews") or [],
            escalation_used=bool(state.get("escalation_used")),
            escalation_independent=bool(state.get("escalation_independent")),
            # ★ 把"为什么升级"直接落库。
            #   原来只有一个 escalation_used 布尔值，想知道原因只能把 panel_reviews
            #   这个 JSONB 摊开反推 —— 上面那条"93% 的升级是浪费"的结论就是这么挖出来的，
            #   花了三条 SQL。原因本来就是判定函数的返回值，顺手写下来几乎零成本。
            escalation_reason=_reason_of(state) if state.get("escalation_used") else None,
            model_versions={r: s.model for r, s in deps.llm.roles.items()}
            if hasattr(deps.llm, "roles") else {})
        hits = state.get("hard_rule_hits") or []
        if hits:
            await deps.pg.write_hard_rule_hits(hits)

    # ══════════════ 1 审查请求标准化 ══════════════
    async def gate_in(state: dict) -> dict:
        raw_pii = T.prescan(state.get("user_input", ""))
        progress.emit("review")
        return {"audit_log": [{"event": "gate_in",
                               "review_kind": state.get("review_kind"),
                               "round": state.get("review_round", 0),
                               "raw_pii": list(raw_pii.keys())}]}

    # ══════════════ 2 输入完整性与权限校验 ══════════════
    async def review_input(state: dict) -> dict:
        """
        信息缺失不是低风险 —— 缺了必要输入就必须补充，不能"没查到就放过"。
        """
        auth = state.get("auth") or {}
        draft = state.get("draft") or {}
        problems: list[str] = []
        if not draft.get("content"):
            problems.append("待审内容为空")
        if state.get("review_kind") == "operation":
            if not state.get("operation"):
                problems.append("操作参数缺失")
            if not auth.get("verified"):
                problems.append("身份未核验，无法审核操作类请求")
        ok = not problems
        return {"review_input_ok": ok,
                "review_feedback": problems if not ok else state.get("review_feedback") or [],
                "audit_log": [{"event": "review_input", "ok": ok, "problems": problems}]}

    async def need_info(state: dict) -> dict:
        text = NEED_INFO_TEXT
        return {
            "draft": {**(state.get("draft") or {}), "content": text},
            **_issue({**state, "draft": {"content": text}}, verdict="need_info"),
            "review_feedback": state.get("review_feedback") or ["缺少必要输入信息"],
            "audit_log": [{"event": "need_info", "problems": state.get("review_feedback")}],
        }

    # ══════════════ 3 紧急信号复查（对象是原始消息）══════════════
    async def emergency_check(state: dict) -> dict:
        return {"audit_log": [{"event": "emergency_recheck",
                               "emergency": bool(state.get("emergency")),
                               "hits": state.get("emergency_hits") or []}]}

    # ══════════════ 4 确定性硬规则 ══════════════
    async def hard_rules(state: dict) -> dict:
        content = (state.get("draft") or {}).get("content", "")
        # 只看【本轮】的历次命中：跨轮次的重复命中才说明"改写解决不了问题"
        prior = [h for h in (state.get("hard_rule_hits_history") or [])
                 if h.get("turn_id") == state.get("turn_id")]
        hits = deps.rules.check_text(content, prior_hits=prior)
        return {"hard_rule_hits": [h.as_dict() for h in hits],
                "audit_log": [{"event": "hard_rules",
                               "rule_version": deps.rules.hard_version,
                               "prior_rounds": len(prior),
                               "hits": [h.rule_id for h in hits]}]}

    async def block(state: dict) -> dict:
        ids = T.dedupe([h["rule_id"] for h in (state.get("hard_rule_hits") or [])])
        await _write_audit(state, verdict="block", risk_level="high")
        return {"verdict": "block", "blocked_rule_ids": ids,
                "review_feedback": [f"命中硬性阻断规则 {rid}" for rid in ids],
                "audit_log": [{"event": "gate_block", "rule_ids": ids}]}

    def after_hard_rules(state: dict):
        """命中即阻断；否则三路并行扇出。
        注意：不能把 list 写进 path_map 的值（LangGraph 校验会报 unhashable list），
        路由函数直接返回节点名列表即可形成并行扇出。"""
        blocked = any(h.get("action") == "block" for h in (state.get("hard_rule_hits") or []))
        if blocked:
            return "block"
        return ["review_medical", "review_ad", "review_privacy"]

    # ══════════════ 5 多专家并行审查 ══════════════
    async def _panel(role: str, system: str, state: dict) -> dict:
        payload = P.render_panel_input(
            (state.get("draft") or {}).get("content", ""),
            render_evidence(state.get("evidence") or []),
            state.get("review_kind") or "content")
        try:
            out: PanelOpinion = await deps.llm.structured(role, PanelOpinion,
                                                          system=system, user=payload)
            data = out.model_dump()
        except Exception as exc:  # noqa: BLE001
            # ★ 审查环节失败【绝不能当成通过】—— 标记 abstain 并升级
            data = {"level": "high", "risk_tags": ["review_unavailable"], "findings": [],
                    "confidence": 0.0, "abstain": True, "error": str(exc)}
        return {"panel_reviews": [{**data, "role": role, "round": state.get("review_round", 0)}]}

    async def review_medical(state: dict) -> dict:
        return await _panel("review_medical", P.REVIEW_MEDICAL_SYSTEM, state)

    async def review_ad(state: dict) -> dict:
        return await _panel("review_ad", P.REVIEW_AD_SYSTEM, state)

    async def review_privacy(state: dict) -> dict:
        return await _panel("review_privacy", P.REVIEW_PRIVACY_SYSTEM, state)

    # ══════════════ 扇入屏障（router 必须挂在真实节点上）══════════════
    async def panel_fanin(state: dict) -> dict:
        return {}

    def should_escalate(state: dict) -> str:
        return "escalate" if _reason_of(state) else "skip"

    def _reason_of(state: dict) -> str | None:
        """当前轮是否需要二次复核，以及原因。判定规则见模块级的 escalation_reason。"""
        reviews = _round_reviews(state)
        tags = T.dedupe([t for r in reviews for t in (r.get("risk_tags") or [])])
        return escalation_reason(reviews=reviews,
                                 high_risk=deps.rules.has_high_risk_tag(tags))

    async def escalate_review(state: dict) -> dict:
        """
        二次复核。MVP 只有一个模型 → 同族复核，必须如实标注，不假装独立。
        ESCALATION_MODEL 一旦填了别家模型，这里立刻变成真独立复核（图代码不变）。
        """
        independent = deps.llm.family_of("escalation") != deps.llm.family_of("review_medical")
        first = _round_reviews(state)
        payload = P.render_escalation_input(
            (state.get("draft") or {}).get("content", ""),
            render_evidence(state.get("evidence") or []), first)
        try:
            out: EscalationOpinion = await deps.llm.structured(
                "escalation", EscalationOpinion, system=P.ESCALATION_SYSTEM, user=payload)
            data = out.model_dump()
        except Exception as exc:  # noqa: BLE001
            data = {"level": "high", "risk_tags": ["review_unavailable"], "findings": [],
                    "confidence": 0.0, "abstain": True, "conflicts": [], "error": str(exc)}
        if not independent:
            await deps.pg.note_same_family_review(state.get("thread_id") or "")
        return {"panel_reviews": [{**data, "role": "escalation", "independent": independent,
                                   "round": state.get("review_round", 0)}],
                "escalation_used": True, "escalation_independent": independent,
                "audit_log": [{"event": "escalate_review", "independent": independent,
                               "reason": _reason_of(state)}]}

    # ══════════════ 6 敏感操作业务校验 ══════════════
    async def check_op(state: dict) -> dict:
        return {}

    def is_operation(state: dict) -> str:
        return "yes" if state.get("review_kind") == "operation" and state.get("operation") else "no"

    async def biz_check(state: dict) -> dict:
        op = state.get("operation") or {}
        params = op.get("params") or {}
        problems: list[str] = []
        try:
            slots = await deps.pg.query_slots(store=params.get("store") or "",
                                              project="", around=params.get("datetime"))
            if not slots:
                problems.append("业务系统未查到可执行档期")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"业务校验失败：{exc}")
        return {"review_feedback": (state.get("review_feedback") or []) + problems,
                "audit_log": [{"event": "biz_check", "problems": problems}]}

    # ══════════════ 7 聚合决策（确定性决策表）══════════════
    async def merge_verdict(state: dict) -> dict:
        reviews = [r for r in (state.get("panel_reviews") or [])
                   if r.get("round") == state.get("review_round")]
        hard = state.get("hard_rule_hits") or []
        tags = T.dedupe([t for r in reviews for t in (r.get("risk_tags") or [])])
        problems = state.get("review_feedback") or []

        if any(h.get("action") == "block" for h in hard):
            level, verdict = "high", "block"
        elif state.get("emergency"):
            level, verdict = "high", "pass"        # 急症走快路径：放行已审提示 + 并行转人工
        elif problems and state.get("review_kind") == "operation":
            level, verdict = "medium", "revise"    # 业务前置条件不成立 → 退回修改方案
        elif deps.rules.has_high_risk_tag(tags) or any(r.get("level") == "high" for r in reviews):
            level, verdict = "high", "human"
        elif hard or deps.rules.count_medium_risk_tag(tags) >= 2 \
                or any(r.get("level") == "medium" for r in reviews):
            level, verdict = "medium", "revise"
        else:
            level, verdict = "low", "pass"

        return {"risk_level": level, "verdict": verdict,
                "audit_log": [{"event": "merge_verdict", "level": level, "verdict": verdict,
                               "tags": tags, "hard": [h.get("rule_id") for h in hard],
                               "panels": len(reviews)}]}

    async def issue_token(state: dict) -> dict:
        """放行：签发凭据 + 写审计（每一次结论都要能回溯到规则版本与模型版本）。"""
        risk_level = state.get("risk_level") or "low"
        await _write_audit(state, verdict="pass", risk_level=risk_level)
        return {**_issue(state, verdict="pass"),
                "audit_log": [{"event": "issue_token", "risk_level": risk_level,
                               "escalation_independent": state.get("escalation_independent")}]}

    async def handoff(state: dict) -> dict:
        await _write_audit(state, verdict="human", risk_level=state.get("risk_level") or "high")
        return {"verdict": "human",
                "audit_log": [{"event": "gate_handoff",
                               "level": state.get("risk_level")}]}

    # ══════════════ 组图 ══════════════
    b = StateGraph(ZhimeiState)
    b.add_node("gate_in", gate_in)
    b.add_node("review_input", review_input)
    b.add_node("need_info", need_info)
    b.add_node("emergency_check", emergency_check)
    b.add_node("hard_rules", hard_rules)
    b.add_node("block", block)
    b.add_node("review_medical", review_medical)
    b.add_node("review_ad", review_ad)
    b.add_node("review_privacy", review_privacy)
    b.add_node("panel_fanin", panel_fanin)
    b.add_node("escalate_review", escalate_review)
    b.add_node("check_op", check_op)
    b.add_node("biz_check", biz_check)
    b.add_node("merge_verdict", merge_verdict)
    b.add_node("issue_token", issue_token)
    b.add_node("handoff", handoff)

    b.add_edge(START, "gate_in")
    b.add_edge("gate_in", "review_input")
    b.add_conditional_edges("review_input", lambda s: "ok" if s.get("review_input_ok") else "need_info",
                            {"ok": "emergency_check", "need_info": "need_info"})
    b.add_edge("need_info", END)

    b.add_conditional_edges("emergency_check",
                            lambda s: "emergency" if s.get("emergency") else "normal",
                            {"emergency": "issue_token", "normal": "hard_rules"})

    b.add_conditional_edges("hard_rules", after_hard_rules)
    b.add_edge("block", END)

    for n in ("review_medical", "review_ad", "review_privacy"):
        b.add_edge(n, "panel_fanin")
    b.add_conditional_edges("panel_fanin", should_escalate,
                            {"escalate": "escalate_review", "skip": "check_op"})
    b.add_edge("escalate_review", "check_op")
    b.add_conditional_edges("check_op", is_operation,
                            {"yes": "biz_check", "no": "merge_verdict"})
    b.add_edge("biz_check", "merge_verdict")
    b.add_conditional_edges("merge_verdict",
                            lambda s: s.get("verdict") or "human",
                            {"pass": "issue_token", "revise": END,
                             "block": "block", "human": "handoff"})
    b.add_edge("issue_token", END)
    b.add_edge("handoff", END)
    return b.compile(name="risk_subgraph")


def _has_conflict(reviews: list[dict]) -> bool:
    """专家意见冲突：有人判 high、有人判 low，且都不是 abstain。"""
    levels = {r.get("level") for r in reviews if not r.get("abstain")}
    return "high" in levels and "low" in levels


def _round_reviews(state: dict) -> list[dict]:
    """取**当前审查轮次**的意见。

    panel_reviews 会跨轮累积（修订后重新送审是新一轮），所以任何只看"这一轮结论"
    的判断都必须先按 round 过滤 —— 漏了就会把上一轮的结论混进来，
    表现为"明明改好了却还按老问题处理"。
    """
    rnd = state.get("review_round")
    return [r for r in (state.get("panel_reviews") or []) if r.get("round") == rnd]


# ══════════════════════════════════════════════════════════════
#  二次复核的触发判定（纯函数，便于单测）
# ══════════════════════════════════════════════════════════════

#: 低置信度阈值。低于它"可能"需要复核 —— 但**单凭它不足以触发**，理由见下。
LOW_CONFIDENCE = 0.6

REASON_NO_REVIEWS = "no_reviews"        # 三份意见一份都没拿到
REASON_HIGH_TAG = "high_risk_tag"       # 命中高风险标签
REASON_CONFLICT = "conflict"            # 三份意见互相矛盾
REASON_ABSTAIN = "abstain"              # 有面板明确表示"无法判断"
REASON_LOW_CONF = "low_confidence"      # 置信度偏低，且确实有值得复核的判断


def _all_clean_low(reviews: list[dict]) -> bool:
    """三份意见都判 low 且都没有 findings —— 即"检查过了，没问题"。"""
    return all(r.get("level") == "low" and not r.get("findings") for r in reviews)


def escalation_reason(*, reviews: list[dict], high_risk: bool,
                      min_confidence: float = LOW_CONFIDENCE) -> str | None:
    """返回"为什么需要二次复核"；None 表示不需要。

    ★ 关于"低置信度"的一条反直觉规则（实测数据推出来的，别轻易改回去）

      原来的写法是：`min(confidence) < 0.6` 就直接升级。跑下来升级率 46%，
      而把审计里的 JSONB 摊开一看：

        45 次升级中，**42 次三个面板全都给了 level=low 且没有任何 findings**。

      也就是说：三个面板都明确表态"低风险、没发现问题"，闸门却因为一个数字
      又烧了一次复核调用（四个审查角色 = 成本 +33%），而且这次复核与首审**同族**
      （单模型下 escalation 与 review_medical 是同一个模型），既没有独立性
      也没有新信息。

      更根本的问题是：**LLM 自报的 confidence 不是校准过的概率。**
      实测分布是量子化的 —— 218 次 0.9、37 次 0.4、12 次 0.6，其余零散。
      模型是在几个档位里挑一个，不是在做概率估计。拿 0.6 去卡这种数字，
      等于让闸门取决于模型这次挑中了哪一档。

      所以：**只有在确实存在"值得复核的判断"时，低置信度才构成升级理由。**
      三个面板全 low 且无 findings 时，低置信度只是数值噪声，不是风险信号。

    ★ 但 `abstain` 仍然一律升级，没有加同样的豁免。

      理由是两者性质不同：abstain 是面板**明确表态"我判断不了"**，
      这在合规闸门上必须保守对待。它现在之所以贡献了 40% 的升级，
      根源是 Prompt 没定义清楚 abstain 的含义（把"我没发现问题"也当成了
      "我不确定"）—— 那要修 Prompt，而不是在这里给它开豁免。
      修完 Prompt 后如果 abstain 率仍然很高，说明面板 Prompt 还需要改，
      而不是说明这道闸门错了。
    """
    if not reviews:
        return REASON_NO_REVIEWS
    if high_risk:
        return REASON_HIGH_TAG
    if _has_conflict(reviews):
        return REASON_CONFLICT
    if any(r.get("abstain") for r in reviews):
        return REASON_ABSTAIN
    if min((r.get("confidence") or 0.0) for r in reviews) < min_confidence:
        return None if _all_clean_low(reviews) else REASON_LOW_CONF
    return None

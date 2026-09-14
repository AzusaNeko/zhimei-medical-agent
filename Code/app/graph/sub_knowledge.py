"""
知识科普子图（主图里的 k_agent）。

边界契约：
  入口 1  主图 dispatch 按「科普咨询」意图分派进入 kb_intake
  入口 2  主图风险审查判「需修改」时，revise 定向回到 kb_verify（不是回到重新生成）
  出口    kb_return / kb_clarify / kb_handoff 三个出口都回到主图 aggregate 统一送审

铁律：本子图只产出「带证据的待审草稿」，从不直接对用户说话。

两个环要分清（混用计数会让修订预算被内部循环吃光）：
  · 子图内部自环   kb_verified → kb_draft   计数 kb_verify_round  上限 KB_MAX_VERIFY_LOOP
  · 父图修订再入   risk_gate → revise → kb_verify   计数 revision_count  上限 MAX_REVISION
"""

from __future__ import annotations

import asyncio
from typing import Callable

from langgraph.graph import END, START, StateGraph

from ..graph.schemas import KbDecomposeOut, KbDraftOut, KbVerifyOut, EvidenceOpinion
from ..prompts import kb as P
from ..prompts.system import render_evidence, render_feedback, render_slots
from ..services import text as T
from ..services.deps import Deps
from . import progress
from .state import ZhimeiState

IN_SCOPE_KEYWORDS = ["区别", "原理", "是什么", "多久", "维持", "流程", "恢复", "注意", "多少钱", "价格", "适合"]
OUT_SCOPE_KEYWORDS = ["预约", "改约", "取消", "退款", "退费", "投诉"]


def build_knowledge_subgraph(deps: Deps):
    MAX_LOOP = deps.settings.kb_max_verify_loop

    # ══════════════ 入口分流 ══════════════
    def entry_router(state: dict) -> str:
        """
        父图 revise 召回时 revision_count > 0 → 走修订再入路径。

        ★ 这里曾经踩过一个坑（跑起来才发现的）：一开始把修订直接接到 kb_verify，
          结果是"只核对不重生成" —— 内容没变，审查必然再次失败，最后白白耗尽修订预算。
          正确做法是：修订再入 → kb_draft（带审查意见重新生成）→ kb_verify → kb_return。
          这样既复用已通过的检索与证据（不重跑 kb_retrieve），又真的改了内容。
        """
        return "revision" if int(state.get("revision_count", 0)) > 0 else "fresh"

    async def kb_revise_in(state: dict) -> dict:
        """修订再入：给这一轮重开一次"生成↔核对"的内部预算。"""
        return {"kb_verify_round": 0,
                "audit_log": [{"event": "kb_revise_in",
                               "revision": state.get("revision_count", 0),
                               "feedback": state.get("review_feedback") or []}]}

    # ══════════════ 1 任务接收与边界判断 ══════════════
    async def kb_intake(state: dict) -> dict:
        raw = state.get("user_input", "")
        intents = set(state.get("intents") or [])
        out_hit = any(k in raw for k in OUT_SCOPE_KEYWORDS)
        in_hit = any(k in raw for k in IN_SCOPE_KEYWORDS)
        # 边界判断是规则优先：明确的操作/争议类直接转交；其余一律放行（宁可多答不可不答）
        in_scope = "knowledge_edu" in intents or (in_hit and not out_hit)
        return {
            "kb_scope_ok": in_scope,
            "kb_verify_round": 0,
            "audit_log": [{"event": "kb_intake", "in_scope": in_scope}],
        }

    def after_scope(state: dict) -> str:
        return "in_scope" if state.get("kb_scope_ok", True) else "out"

    # ══════════════ 2 读取经授权的上下文 ══════════════
    async def kb_context(state: dict) -> dict:
        scopes = (state.get("auth") or {}).get("scopes", [])
        profile = await deps.pg.load_profile(state.get("user_id")) if "profile" in scopes else {}
        return {
            "recent_turns": await deps.pg.recent_turns(state.get("session_id", ""), limit=3),
            "audit_log": [{"event": "kb_context", "profile_authorized": bool(profile)}],
        }

    # ══════════════ 3 解析问题并拆分检索子任务 ══════════════
    async def kb_decompose(state: dict) -> dict:
        try:
            out: KbDecomposeOut = await deps.llm.structured(
                "kb_decompose", KbDecomposeOut, system=P.KB_DECOMPOSE_SYSTEM,
                user=P.KB_DECOMPOSE_USER.format(
                    user_input=state.get("user_input", ""),
                    slots=render_slots(state.get("slots")),
                    review_feedback=render_feedback(state.get("review_feedback") or [])))
            queries = [q.model_dump() for q in out.queries] or _fallback_queries(state)
            missing = list(out.missing)
        except Exception as exc:  # noqa: BLE001
            queries, missing = _fallback_queries(state), []
            return {"kb_queries": queries, "kb_sufficient": True,
                    "audit_log": [{"event": "kb_decompose_degraded", "error": str(exc)}]}

        # 只有缺失信息会实质影响回答时才追问
        sufficient = not missing or not _materially_missing(missing, state)
        return {"kb_queries": queries, "kb_sufficient": sufficient,
                "audit_log": [{"event": "kb_decompose", "queries": len(queries),
                               "missing": missing, "sufficient": sufficient}]}

    def after_sufficiency(state: dict) -> str:
        return "ok" if state.get("kb_sufficient", True) else "need_info"

    # ══════════════ 4 生成澄清问题（出口之一）══════════════
    async def kb_clarify(state: dict) -> dict:
        missing = _missing_categories(state)
        content = deps.rules.clarify_text(missing) or \
            "想先确认您说的是哪个项目？具体仍以医生面诊评估为准。"
        return {
            "kb_exit": "clarify",
            "drafts": [{"agent": "clarify", "turn_id": state.get("turn_id"), "revision": 0,
                        "content": content, "citations": [], "gaps": missing,
                        "route_hint": None, "risk_tags": [], "operation": None}],
            "audit_log": [{"event": "kb_clarify", "missing": missing}],
        }

    # ══════════════ 5 混合检索 ══════════════
    async def kb_retrieve(state: dict) -> dict:
        progress.emit("retrieve")
        queries = state.get("kb_queries") or _fallback_queries(state)
        texts = [q["query_text"] for q in queries]
        vecs = await deps.encoder.encode(texts)          # 进程内模型，走线程池
        projects = _project_filter(state)

        async def one(q: dict, dense: list[float], sparse: dict) -> list[dict]:
            return await deps.vector_store.search(
                query_text=q["query_text"], query_dense=dense, query_sparse=sparse,
                projects=projects, top_k=deps.settings.recall_k)

        groups = await asyncio.gather(*(
            one(q, d, s) for q, d, s in zip(queries, vecs["dense"], vecs["sparse"])))
        candidates = [h for g in groups for h in g]
        return {"kb_candidates": candidates,
                "audit_log": [{"event": "kb_retrieve", "queries": len(queries),
                               "candidates": len(candidates), "projects": projects}]}

    # ══════════════ 6 证据筛选与充分性检查 ══════════════
    async def kb_evidence(state: dict) -> dict:
        candidates = state.get("kb_candidates") or []
        if not candidates:
            return {"kb_evidence": [], "kb_enough": False,
                    "audit_log": [{"event": "kb_evidence", "reason": "no_candidates"}]}

        # 精排：用【原始问题】而不是改写后的 query，避免改写引入偏差
        docs = [c.get("text", "") for c in candidates]
        scores = await deps.reranker.score(state.get("user_input", ""), docs)
        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        kept = [(c, s) for c, s in ranked[: deps.settings.max_evidence]
                if s >= deps.settings.min_rerank]

        evidence = [{
            "evidence_id": f"E{i + 1}", "doc_id": c.get("doc_id"), "title": c.get("title"),
            "version": c.get("version"), "doc_type": c.get("doc_type"),
            "text": c.get("text"), "score": round(float(s), 4),
        } for i, (c, s) in enumerate(kept)]

        enough = bool(kept)
        # 高影响 / 冲突：交给模型做一次"证据是否充分"的独立判断（不是生成回答）
        if evidence and _high_impact(state):
            try:
                verdict: EvidenceOpinion = await deps.llm.structured(
                    "evidence_second_opinion", EvidenceOpinion,
                    system=P.EVIDENCE_SECOND_SYSTEM,
                    user=P.EVIDENCE_SECOND_USER.format(
                        user_input=state.get("user_input", ""),
                        evidence=render_evidence(evidence)))
                if verdict.verdict == "insufficient":
                    enough = False
            except Exception:  # noqa: BLE001
                pass

        return {"kb_evidence": evidence, "kb_enough": enough,
                "audit_log": [{"event": "kb_evidence", "kept": len(evidence),
                               "enough": enough,
                               "top_score": evidence[0]["score"] if evidence else None}]}

    def after_evidence(state: dict) -> str:
        return "enough" if state.get("kb_enough") else "thin"

    # ══════════════ 7 证据不足：缩小回答范围 ══════════════
    async def kb_limit(state: dict) -> dict:
        payload = P.KB_LIMIT_USER.format(
            user_input=state.get("user_input", ""),
            evidence=render_evidence(state.get("kb_evidence") or []),
            missing="、".join(_missing_categories(state)) or "未知")
        try:
            out: KbDraftOut = await deps.llm.structured(
                "kb_limit", KbDraftOut, system=P.KB_LIMIT_SYSTEM, user=payload)
            content, cites, gaps = out.content, [c.model_dump() for c in out.citations], list(out.gaps)
        except Exception as exc:  # noqa: BLE001
            content = ("目前没有查到这个问题的可靠资料，为避免给您不准确的信息，建议到院面诊评估。")
            cites, gaps = [], [f"资料缺失：{exc}"]

        content = content or "目前没有查到可靠资料，建议到院面诊评估。"
        content = _ensure_disclaimer(content)
        return {"kb_draft_content": content, "kb_citations": cites,
                "kb_gaps": gaps or ["知识库未覆盖该问题"]}

    # ══════════════ 8 生成科普草稿 ══════════════
    async def kb_draft(state: dict) -> dict:
        evidence = state.get("kb_evidence") or []
        payload = P.KB_DRAFT_USER.format(
            user_input=state.get("user_input", ""),
            slots=render_slots(state.get("slots")),
            review_feedback=render_feedback(state.get("review_feedback") or []),
            evidence=render_evidence(evidence))
        try:
            out: KbDraftOut = await deps.llm.structured(
                "kb_draft", KbDraftOut, system=P.KB_DRAFT_SYSTEM, user=payload)
            content = out.content or _fallback_content(evidence)
            cites = [c.model_dump() for c in out.citations]
            gaps = list(out.gaps)
        except Exception as exc:  # noqa: BLE001
            content, cites, gaps = _fallback_content(evidence), [], [f"生成降级：{exc}"]

        return {"kb_draft_content": _ensure_disclaimer(content),
                "kb_citations": cites, "kb_gaps": gaps,
                "audit_log": [{"event": "kb_draft", "revision": state.get("revision_count", 0)}]}

    # ══════════════ 9 事实与引用逐项核对 ══════════════
    async def kb_verify(state: dict) -> dict:
        progress.emit("verify")
        payload = P.KB_VERIFY_USER.format(
            draft=state.get("kb_draft_content", ""),
            evidence=render_evidence(state.get("kb_evidence") or []))
        try:
            out: KbVerifyOut = await deps.llm.structured(
                "kb_verify", KbVerifyOut, system=P.KB_VERIFY_SYSTEM, user=payload)
            checks = [c.model_dump() for c in out.claims]
            unsupported = [c for c in checks if c.get("status") != "supported"]
            hard = [c for c in checks if not _is_soft(c)]
        except Exception as exc:  # noqa: BLE001
            # 核对失败不能当成"通过" —— 安全侧默认：转成降级回答
            return {"kb_claim_checks": [], "kb_verify_round": int(state.get("kb_verify_round", 0)) + 1,
                    "kb_enough": False,
                    "audit_log": [{"event": "kb_verify_degraded", "error": str(exc)}]}

        return {
            "kb_claim_checks": checks,
            "kb_verify_round": int(state.get("kb_verify_round", 0)) + 1,
            "kb_enough": bool(checks) and not unsupported,
            "audit_log": [{"event": "kb_verify", "claims": len(checks),
                           "unsupported": len(unsupported),
                           # hard/soft 分开记账：只看到"unsupported=1"根本判断不出
                           # 是"编造了事实"还是"措辞太满"，而这两者的处置完全相反
                           "hard": len(hard), "soft": len(unsupported) - len(hard),
                           "round": int(state.get("kb_verify_round", 0)) + 1}],
        }

    def after_verify(state: dict) -> str:
        """子图内部自环的收口：把状态翻译成一条出口，判断规则见 decide_verify_exit。"""
        return decide_verify_exit(
            claims=state.get("kb_claim_checks") or [],
            round_no=int(state.get("kb_verify_round", 0)),
            max_loop=MAX_LOOP)

    # ══════════════ 10 返回结构化待审草稿（出口之二）══════════════
    async def kb_return(state: dict) -> dict:
        exit_kind = state.get("kb_exit")
        if exit_kind in ("clarify", "handoff"):
            # kb_clarify / 越界转交已经把草稿写好了，这里只负责收口
            return {"audit_log": [{"event": "kb_exit", "kind": exit_kind}]}

        content = state.get("kb_draft_content") or ""
        if not content:
            # 越界但没有草稿：给一句转交建议（同样要过审）
            content = "这个问题建议由顾问或医生为您解答，我这边先不做判断。具体以医生面诊评估为准。"
        return {
            "drafts": [{"agent": "k_agent", "turn_id": state.get("turn_id"),
                        "revision": int(state.get("revision_count", 0)),
                        "content": _ensure_disclaimer(content),
                        "citations": state.get("kb_citations") or [],
                        "gaps": state.get("kb_gaps") or [],
                        "route_hint": None,
                        "risk_tags": deps.rules.tags_from_text(content),
                        "operation": None}],
            "kb_exit": "draft",
            "audit_log": [{"event": "kb_return", "has_evidence": bool(state.get("kb_evidence"))}],
        }

    # ══════════════ 组图 ══════════════
    b = StateGraph(ZhimeiState)
    b.add_node("kb_revise_in", kb_revise_in)
    b.add_node("kb_intake", kb_intake)
    b.add_node("kb_context", kb_context)
    b.add_node("kb_decompose", kb_decompose)
    b.add_node("kb_clarify", kb_clarify)
    b.add_node("kb_retrieve", kb_retrieve)
    b.add_node("kb_evidence", kb_evidence)
    b.add_node("kb_limit", kb_limit)
    b.add_node("kb_draft", kb_draft)
    b.add_node("kb_verify", kb_verify)
    b.add_node("kb_return", kb_return)

    b.add_conditional_edges(START, entry_router,
                            {"fresh": "kb_intake", "revision": "kb_revise_in"})
    b.add_edge("kb_revise_in", "kb_draft")     # 修订必须重新生成，不能只重新核对
    b.add_conditional_edges("kb_intake", after_scope,
                            {"in_scope": "kb_context", "out": "kb_return"})
    b.add_edge("kb_context", "kb_decompose")
    b.add_conditional_edges("kb_decompose", after_sufficiency,
                            {"ok": "kb_retrieve", "need_info": "kb_clarify"})
    b.add_edge("kb_clarify", END)
    b.add_edge("kb_retrieve", "kb_evidence")
    b.add_conditional_edges("kb_evidence", after_evidence,
                            {"enough": "kb_draft", "thin": "kb_limit"})
    # ★ kb_limit 必须直接回 kb_return，不能回 kb_verify：
    #   否则会形成 kb_limit → kb_verify →（已超上限 loop_out）→ kb_limit 的**死循环**，
    #   而且每一圈都调一次 LLM，表现为"卡住十分钟然后超时"。
    #   降级回答本身是保守的（只说明资料不足），并且仍要过父图的风险审查这道关。
    b.add_edge("kb_limit", "kb_return")
    b.add_edge("kb_draft", "kb_verify")
    b.add_conditional_edges("kb_verify", after_verify,
                            {"grounded": "kb_return", "ungrounded": "kb_draft",
                             "loop_out": "kb_limit"})
    b.add_edge("kb_return", END)
    return b.compile(name="knowledge_subgraph")


# ══════════════ 辅助 ══════════════
DISCLAIMER = "具体以医生面诊评估为准。"

#: 软问题的判定集合：**有证据支撑，只是表述过头/绝对化**。
#: 只放这一个 —— 不认识的 status（模型自由发挥出来的词）一律按硬问题处理，
#: 因为"看不懂"不能当成"没问题"。
SOFT_STATUS = {"overstated"}

#: 软问题最多回炉的轮数。修一次措辞就够了，再修只会越删越空。
SOFT_REPAIR_ROUNDS = 1


def _is_soft(claim: dict) -> bool:
    return str(claim.get("status") or "").strip().lower() in SOFT_STATUS


def decide_verify_exit(*, claims: list[dict], round_no: int,
                       max_loop: int, soft_rounds: int = SOFT_REPAIR_ROUNDS) -> str:
    """事实核对后的出口判定（纯函数，便于单测 —— 这是刻意的）。

    ★ 这里有一条**反直觉但很重要**的规则：预算耗尽时不要一律降级。

    第一版写成"只要还有任何非 supported 的判定就回炉，回炉到上限就 loop_out 去
    kb_limit"，真实模型跑出来是这样：
      第 1 轮判定 1 句"表述过头" → 重新生成
      第 2 轮模型为了不被挑错，删掉了有争议的内容 → 又被挑出别的问题
      第 3 轮继续删 → 预算耗尽 → loop_out → 草稿【被整个丢弃】，
      换成 kb_limit 那句"现有资料中没有可引用的说明"。
    净效果是**把一份基本合格的答案越改越空、最后扔掉**，比第一版还差 ——
    预算本意是保护质量，结果成了质量杀手。

    所以按"问题性质"分档，而不是"有没有问题"：
      · 只有软问题（overstated：有证据、只是说得太满）→ 最多修 soft_rounds 轮，
        之后即使仍不完美也【放行草稿】。依据是绝对化用语在父图 risk_gate 和规则层
        还有一道硬检查兜底，"交付偏满但有依据的答案"优于"交付什么都没说的答案"。
      · 有硬问题（unsupported：找不到依据 / 引错证据）→ 才值得回炉；预算耗尽仍不
        达标则 loop_out 降级 —— 编造内容绝不能出门。

    为什么抽成模块级纯函数：它原本是 build_knowledge_subgraph 里的闭包，
    闭包没法被单测，而"没人测得到的反直觉规则"正是最容易被后人改回去的东西。

    ★ 判定只依据 claims，不另收一个 enough 参数：
      曾经用 `kb_enough = not unsupported` 作为依据，结果是——校验模型返回
      **空 claims**（既没报错、也没给判定）时 `not []` 为真，被当成"核对通过"，
      于是【什么都没核对】的草稿直接放行。空 claims 必须按"核对没做成"处理。
    """
    if not claims:
        # 核对没产出任何判定（调用失败 / 模型没给）→ 绝不当作通过
        return "loop_out" if round_no >= max_loop else "ungrounded"

    bad = [c for c in claims if str(c.get("status") or "").strip().lower() != "supported"]
    if not bad:
        return "grounded"

    # 未知 status 一律按硬问题处理：看不懂不能当成没问题
    if all(_is_soft(c) for c in bad):
        return "ungrounded" if round_no <= soft_rounds else "grounded"

    return "loop_out" if round_no >= max_loop else "ungrounded"


def _ensure_disclaimer(content: str) -> str:
    if not content:
        return content
    return content if DISCLAIMER in content else content.rstrip() + f"\n（{DISCLAIMER}）"


def _fallback_queries(state: dict) -> list[dict]:
    raw = state.get("user_input", "")
    projects = (state.get("slots") or {}).get("project") or []
    if isinstance(projects, str):
        projects = [projects]
    base = f"{' '.join(projects)} {raw[:20]}".strip()
    return [{"sub_task": "原理与适用", "query_text": f"{base} 原理 适用"},
            {"sub_task": "恢复期与注意事项", "query_text": f"{base} 恢复期 注意事项"}]


def _fallback_content(evidence: list[dict]) -> str:
    if not evidence:
        return "目前没有查到可靠资料，建议到院面诊评估。"
    lines = ["根据已审核资料，先说明如下："]
    for e in evidence[:3]:
        lines.append(f"- {e.get('text', '')} [{e.get('evidence_id')}]")
    return "\n".join(lines)


def _project_filter(state: dict) -> list[str] | None:
    """project 槽位是数组（一句话可能提到多个项目），检索按多值过滤。"""
    projects = (state.get("slots") or {}).get("project") or []
    if isinstance(projects, str):
        projects = [projects]
    return [p for p in projects if p] or None


def _high_impact(state: dict) -> bool:
    """高影响场景（术后 / 风险 / 疗效类）才做证据二次判断，避免每次都多花一次调用。"""
    raw = state.get("user_input", "")
    return any(k in raw for k in ("术后", "风险", "副作用", "并发症", "维持", "效果"))


def _materially_missing(missing: list[str], state: dict) -> bool:
    """缺项目名会实质影响检索 → 追问；其余缺口不追问，直接给一般性说明。"""
    return "project" in missing


def _missing_categories(state: dict) -> list[str]:
    slots = state.get("slots") or {}
    out = []
    if not slots.get("project"):
        out.append("project")
    return out or ["project"]

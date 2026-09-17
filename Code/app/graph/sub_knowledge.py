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

from ..graph.schemas import (EvidenceOpinion, KbCtxOut, KbDecomposeOut, KbDraftOut,
                             KbVerifyOut)
from ..prompts import kb as P
from ..prompts.system import (HISTORY_RULES, render_dialog_history, render_evidence,
                              render_feedback, render_slots)
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
        # ★ 这里**故意不再重新查 recent_turns**。
        #   原来它会 `recent_turns(session_id, limit=3)` 覆盖 state —— 但两个问题：
        #     1) 本节点执行时，本轮用户消息已经由 normalize 落库了，
        #        所以查出来的"最近对话"里**包含用户刚说的这句话**；
        #        喂给模型等于把同一句话重复一遍，还可能让指代解析绕回它自己。
        #     2) 子图内部没有任何地方读这个字段（读它的只有 classify 与 clarify，
        #        都在主图），所以这次查询纯属白跑一次数据库。
        #   recent_turns 的唯一来源是 normalize，且它是在保存本轮消息**之前**取的。
        return {
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
                    history_rules=HISTORY_RULES,
                    history=render_dialog_history(state.get("recent_turns") or []),
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

    # ══════════════ 3b 越界收口前：先用会话上下文答一次 ══════════════
    async def kb_ctx_reply(state: dict) -> dict:
        """越界（不是知识库该答的问题）时，先看看**手上已有的上下文**能不能答。

        ★ 这条路径原来是直接跳到收口节点，吐一句固定话术
          "这个问题建议由顾问或医生为您解答，我这边先不做判断。"
          实测：顾客先说过"我做的是超声炮"（槽位里就写着），再问"我做的什么项目？"，
          答案明明在手边，却回了"我这边先不做判断"。
          用户的原话是"没有获取到历史记忆" —— 信息其实取到了，是这条出口拒绝用它。

        ★ 安全默认：模型输出空 content 就什么都不做，后面的收口节点照旧转交。
          所以**答不了的情况行为完全不变**，只是多了一次尝试。
          这一点很重要：宁可输出空（转交），也不要硬答（给错信息）。
        """
        try:
            out: KbCtxOut = await deps.llm.structured(
                "kb_ctx_reply", KbCtxOut, system=P.KB_CTX_SYSTEM,
                user=P.KB_CTX_USER.format(
                    user_input=state.get("user_input", ""),
                    slots=render_slots(state.get("slots")),
                    history=render_dialog_history(state.get("recent_turns") or [])))
        except Exception as exc:  # noqa: BLE001
            # 出错了就当"答不了"，继续走原来的转交路径 —— 不让新增的这一步
            # 把一条本来能正常收口的路径变成失败。
            return {"audit_log": [{"event": "kb_ctx_reply_degraded",
                                   "error": str(exc)[:120]}]}

        content = (out.content or "").strip()
        return ctx_reply_patch(content, out.reason)

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

        # ★ 单个子任务检索失败**不能拖垮整轮**：
        #   把它降级成"这一路没召回"，让下游的 kb_evidence 按"证据不足"处理
        #   （最终产出的是诚实的"资料不足 + 面诊建议"，仍然要过审查）。
        #   原来的写法是直接 await gather，任何一路抛错都会让整轮流失败 ——
        #   实测 Milvus 一次内部重试耗尽能挂 30 分钟，那时用户等到的不是降级回答，
        #   而是一个永远不会来的响应。
        results = await asyncio.gather(
            *(one(q, d, s) for q, d, s in zip(queries, vecs["dense"], vecs["sparse"])),
            return_exceptions=True)

        groups: list[list[dict]] = []
        failures: list[str] = []
        for q, r in zip(queries, results):
            if isinstance(r, BaseException):
                failures.append(f"{q.get('sub_task') or q.get('query_text')}: "
                                f"{type(r).__name__}: {r}")
                groups.append([])
            else:
                groups.append(r)

        candidates = [h for g in groups for h in g]
        return {"kb_candidates": candidates,
                "audit_log": [{"event": "kb_retrieve", "queries": len(queries),
                               "candidates": len(candidates), "projects": projects,
                               "failed_sub_tasks": failures}]}

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
        kept = select_evidence(ranked, floor=deps.settings.rerank_floor,
                               rel_ratio=deps.settings.rerank_rel_ratio,
                               max_n=deps.settings.max_evidence)
        best = ranked[0][1] if ranked else 0.0
        # 「弱证据」= 最高分没到 MIN_RERANK。它**不再是准入门槛**（理由见 select_evidence），
        # 而是一个"要多查一道"的信号 —— 放在下面触发独立复核。
        weak = bool(kept) and best < deps.settings.min_rerank

        evidence = [{
            "evidence_id": f"E{i + 1}", "doc_id": c.get("doc_id"), "title": c.get("title"),
            "version": c.get("version"), "doc_type": c.get("doc_type"),
            "text": c.get("text"), "score": round(float(s), 4),
        } for i, (c, s) in enumerate(kept)]

        enough = bool(kept)
        # 高影响 / 弱证据：交给模型做一次"证据是否充分"的独立判断（不是生成回答）
        # ★ 为什么弱证据也要走：我们刚刚把"相关但分数低"的证据从弃用改成使用，
        #   那就必须让另一道判断来兜底 —— 放松闸门而不加复核，等于两处都变松。
        if evidence and (_high_impact(state) or weak):
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
                               "enough": enough, "weak": weak,
                               "top_score": evidence[0]["score"] if evidence else None,
                               "best": round(float(best), 4),
                               "floor": deps.settings.rerank_floor,
                               "rel_ratio": deps.settings.rerank_rel_ratio}]}

    def after_evidence(state: dict) -> str:
        return "enough" if state.get("kb_enough") else "thin"

    # ══════════════ 7 证据不足：缩小回答范围 ══════════════
    async def kb_limit(state: dict) -> dict:
        # ★ 这里**必须**和 kb_draft 一样拿到 slots 与历史。
        #   原来它只有 user_input + evidence，于是这条"证据不足"的路径
        #   （恰恰是真实数据下**最常走**的一条）写出来的回答对用户的情况一无所知：
        #   实测"我对利多卡因过敏"之后问"做热玛吉要注意什么"，它的回答里
        #   一个字都没提到过敏 —— 因为那句话根本没进这个 Prompt。
        #   同一个子图里两个节点拿到的东西不一样，是纯粹的疏漏。
        payload = P.KB_LIMIT_USER.format(
            user_input=state.get("user_input", ""),
            slots=render_slots(state.get("slots")),
            history_rules=HISTORY_RULES,
            history=render_dialog_history(state.get("recent_turns") or []),
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
            history_rules=HISTORY_RULES,
            history=render_dialog_history(state.get("recent_turns") or []),
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
    b.add_node("kb_ctx_reply", kb_ctx_reply)
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
                            {"in_scope": "kb_context", "out": "kb_ctx_reply"})
    # 越界不等于没得答：先用手上的上下文试一次，答不了再照旧收口转交。
    b.add_edge("kb_ctx_reply", "kb_return")
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


def ctx_reply_patch(content: str | None, reason: str = "") -> dict:
    """把"越界时先试答一次"的模型输出，规范化成状态补丁（纯函数，便于单测）。

    ★ 这里唯一要守住的事：**空内容 = 什么都不做**，让后续的收口节点照旧转交人工。
      也就是说这条新增的路径**答不了时行为完全不变** —— 只是多试一次。
      宁可输出空（转交），也不要硬答（给用户错误信息）。
      `reason` 只进审计日志，不参与出站内容。
    """
    text = (content or "").strip()
    if not text:
        return {"audit_log": [{"event": "kb_ctx_reply_declined",
                               "reason": (reason or "")[:120]}]}
    return {"kb_draft_content": text, "kb_citations": [], "kb_gaps": [],
            "audit_log": [{"event": "kb_ctx_reply", "answered": True}]}


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


def select_evidence(ranked: list[tuple[dict, float]], *, floor: float,
                    rel_ratio: float, max_n: int) -> list[tuple[dict, float]]:
    """从精排结果里挑出真正拿去作答的证据。纯函数，便于确定性单测。

    ★ 为什么不是"分数 >= 固定阈值"就完事（这条踩过坑，别再改回去）：

      bge-reranker-v2-m3 的分数是 sigmoid 概率，**绝对值高度取决于问法与文风**，
      而不只是"相不相关"。实测同一批已审核资料：

          完全不相关的问题（天气 / 写诗 / 电影 / 菜谱）   最高分 0.0000 – 0.0002
          "怎么确认这家店有没有资质"                     0.0548
          "热玛吉有没有风险"                            0.1490
          "皮秒做完会不会反黑"（资料里字面就有"反黑"）      0.1831

      相关证据低到 0.05、不相关低到 0.000，**差三个数量级**。
      用 0.30 这样一条绝对线去卡，卡掉的**全是相关的**：
      15 条检索验收里有 5 条"命中了正确文档、分数却不到 0.30"，
      于是 kb_evidence 一条证据都不留，回答降级成"资料不足 + 建议面诊" ——
      检索明明找对了，是闸门把它扔了。

    ★ 所以改成两条一起用：
        · 绝对下限 `floor`：只负责回答"是不是**全都不相关**"。
          取 0.02 —— 比最不相关的 0.0002 高两个数量级，又远低于最低的相关证据 0.0548，
          中间是一段很宽的安全区。
        · 相对系数 `rel_ratio`：在过了下限的候选里，只留"最高分附近"的那一簇，
          丢掉明显更差的长尾。这替代了原来那条绝对线**想做的事**，
          但它是相对于**本轮最好的证据**来判断的，因此不受问法与文风影响。

    ★ 保证：只要最高分过了下限，它自己一定被保留 —— `rel_ratio <= 1` 时
      `best >= best * rel_ratio` 恒成立。否则会出现"分数最高的那条被相对规则筛掉"
      这种荒谬结果，而且因为候选少的时候最明显，会显得像"检索坏了"。
    """
    if not ranked:
        return []
    # 自己再排一次：依赖调用方先排好，是一个不会立刻报错、只会偶尔少给证据的隐患
    ordered = sorted(ranked, key=lambda x: -x[1])
    best = ordered[0][1]
    if best < floor:
        # 全都不过下限 → 一条都不要。这是"拒绝凭空作答"的那道保护，必须留着：
        # 放宽的是"相关但分数低"，不是"什么都不相关也照答"。
        return []
    cutoff = max(floor, best * rel_ratio)
    return [(c, s) for c, s in ordered[:max_n] if s >= cutoff]


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

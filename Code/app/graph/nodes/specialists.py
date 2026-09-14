"""
专业 Agent 层节点（草稿产出层）。

产出契约统一：每个节点只往 state["drafts"] 里追加一条 Draft，
不直接对用户说话、不自己判断能不能发。所有出站都要过 aggregate → risk_gate。
"""

from __future__ import annotations

from typing import Callable

from ...prompts import emergency as EM
from ...prompts import understand as PU
from ...prompts.system import render_slots
from ...services import text as T
from ...services.deps import Deps
from ..schemas import ClarifyOut, SpecialistDraftOut

# 更接近"机构服务口吻"的专业草稿模板：字段由具体 Agent 填充
SPECIALIST_SYSTEM = """
你是医美机构的专业顾问模块。只输出 JSON，不要寒暄。

【硬性要求】
- 只基于我提供的业务查询结果与证据作答；查不到就如实说查不到，不要猜。
- 不承诺疗效、不使用绝对化用语、不报价（价格只能引用业务系统返回值）。
- 涉及个人适用性的内容必须写"需面诊评估"。
- 需要执行操作时，把操作写进 operation 字段，不要在 content 里假装已经执行完成。
""".strip()

SPECIALIST_USER = """用户问题：{user_input}
已知槽位：{slots}
业务查询结果：
{biz}

上一轮审查意见（如有，必须逐条落实）：{review_feedback}

输出 JSON：
{{"content": "面向用户的内容草稿",
  "citations": [],
  "gaps": [],
  "operation": null}}"""


def _draft(state: dict, agent: str, content: str, *, citations: list | None = None,
           gaps: list | None = None, route_hint: str | None = None,
           risk_tags: list | None = None, operation: dict | None = None) -> dict:
    """统一的草稿落库格式 —— 所有产出者都必须走这里，避免字段漂移。"""
    return {
        "drafts": [{
            "agent": agent,
            "turn_id": state.get("turn_id"),
            "revision": int(state.get("revision_count", 0)),
            "content": content,
            "citations": citations or [],
            "gaps": gaps or [],
            "route_hint": route_hint,
            "risk_tags": risk_tags or [],
            "operation": operation,
        }]
    }


def make_specialist_nodes(deps: Deps) -> dict[str, Callable]:

    async def _generic(state: dict, agent: str, biz: dict) -> dict:
        payload = SPECIALIST_USER.format(
            user_input=state.get("user_input", ""),
            slots=render_slots(state.get("slots")),
            biz=T.clean(str(biz))[:1500],
            review_feedback="；".join(state.get("review_feedback") or []) or "（无）",
        )
        out: SpecialistDraftOut = await deps.llm.structured(
            "specialist_draft", SpecialistDraftOut, system=SPECIALIST_SYSTEM, user=payload)
        return _draft(state, agent, out.content,
                      citations=[c.model_dump() for c in out.citations],
                      gaps=list(out.gaps), operation=out.operation)

    # ══════════════ 项目推荐 ══════════════
    async def r_agent(state: dict) -> dict:
        slots = state.get("slots") or {}
        # 画像授权校验：只读经授权信息
        profile = await deps.pg.load_profile(state.get("user_id")) \
            if "profile" in (state.get("auth") or {}).get("scopes", []) else {}
        biz = {"profile_authorized": bool(profile),
               "project_hint": slots.get("project"), "budget": slots.get("budget")}
        return await _generic(state, "r_agent", biz)

    # ══════════════ 资质与门店查询 ══════════════
    async def c_agent(state: dict) -> dict:
        slots = state.get("slots") or {}
        biz = await deps.pg.query_clinic_info(store=slots.get("store"), doctor=slots.get("doctor"))
        # 数据驱动的风险标签：无资质 / 资质过期 → 高风险
        risk_tags = []
        if biz.get("credential") and "有效" not in str(biz.get("credential")):
            risk_tags.append("unauthorized_provider")
        out = await _generic(state, "c_agent", biz)
        out["drafts"][0]["risk_tags"] = risk_tags
        return out

    # ══════════════ 预约管理 ══════════════
    async def b_agent(state: dict) -> dict:
        slots = state.get("slots") or {}
        projects = _project_list(slots)
        slots_list = await deps.pg.query_slots(
            store=slots.get("store") or "浦东店", project=_project_text(slots),
            around=slots.get("datetime"))
        # ★ 传列表而不是拼接后的字符串："热玛吉、超声炮" 拿去和库里的 project_id/name
        #   精确比对必然匹配不到，而且不报错、只会静默返回"查不到"。
        current = await deps.pg.find_upcoming_appointment(
            state.get("user_id") or "", projects=projects or None)
        biz = {"available_slots": slots_list, "current_appointment": current,
               "note": "改约可能影响套餐使用期限，手续费以业务系统为准"}
        out = await _generic(state, "b_agent", biz)
        draft = out["drafts"][0]
        op = draft.get("operation")

        # ★ 标识符只能由**系统**提供，绝不能让模型编：
        #   模型知道"改到浦东店周五"，但它不可能知道"改的是哪一条预约记录、这条记录的
        #   版本号是多少"。所以模型只决定"做什么动作 + 目标时段"，
        #   appointment_id / expected_version 一律由这里从库里查到后注入。
        #   顺带一个好处：这两个值进了 params，就会一起进 plan_hash，
        #   于是"确认后偷偷换一条预约去改"会让凭据直接失效。
        if op and op.get("action") == "change_appointment" and current:
            op.setdefault("params", {})
            op["params"]["appointment_id"] = current["appointment_id"]
            op["params"]["expected_version"] = current["version"]
        elif not op and slots_list and current:
            draft["operation"] = {
                "action": "change_appointment",
                "params": {"appointment_id": current["appointment_id"],
                           "expected_version": current["version"],
                           "store": slots.get("store") or "浦东店",
                           "datetime": slots_list[0].get("start_at")},
            }
        elif op and not current:
            # ★ 查不到可改约的预约 → 不能给一个"看起来能执行"的方案。
            #   否则用户确认后必然在执行阶段失败，而失败信息还是并发冲突，
            #   用户和坐席都无从下手。这里直接诚实降级成"请提供预约信息"。
            draft["operation"] = None
            draft["content"] = (
                "我没有查到您当前可改约的预约记录，所以还不能为您提交改约。\n"
                "请确认您要调整的是哪一次预约（或提供预约编号 / 原预约时间），我再为您查询。")
            draft["gaps"] = ["未查到可改约的预约记录"]
        return out

    # ══════════════ 术后护理与随访 ══════════════
    async def p_agent(state: dict) -> dict:
        slots = state.get("slots") or {}
        attachments = state.get("attachments") or []
        notes = []
        route_hint = None
        if attachments:
            # ★ MVP 未接多模态：图片只入库不解析，转人工，且不得假装看图
            notes.append("用户上传了图片，已转交值班医师查看")
            route_hint = "human_vision"
        biz = {"postop_days": slots.get("postop_days"), "symptom": slots.get("symptom"),
               "attachments": len(attachments), "notes": notes}
        out = await _generic(state, "p_agent", biz)
        if route_hint:
            out["drafts"][0]["route_hint"] = route_hint
            out["drafts"][0]["content"] = "已收到您的图片，已转交值班医师查看，请稍候。"
        return out

    # ══════════════ 澄清 ══════════════
    async def clarify(state: dict) -> dict:
        """
        生成澄清问题。★ 计数只在代码里累加，且必须写回 state 才能跨轮次生效。
        """
        templates = deps.rules.clarify.get("templates", {})
        missing = _missing_slots(state.get("slots") or {}, state.get("intents") or [])
        fallback = deps.rules.clarify_text(missing)

        try:
            out: ClarifyOut = await deps.llm.structured(
                "clarify", ClarifyOut, system=PU.CLARIFY_SYSTEM,
                user=PU.CLARIFY_USER.format(
                    user_input=state.get("user_input", ""),
                    slots=render_slots(state.get("slots")),
                    missing="、".join(missing) or "未知",
                    templates="\n".join(f"- {k}: {v}" for k, v in templates.items())))
            content, gaps = out.content or fallback, list(out.gaps)
        except Exception:  # noqa: BLE001
            content, gaps = fallback, ["未能确定意图"]

        n = int(state.get("clarify_count", 0)) + 1
        out_state = _draft(state, "clarify", content or "麻烦您再说得具体一点，具体以医生面诊评估为准。",
                           gaps=gaps)
        out_state["clarify_count"] = n
        out_state["audit_log"] = [{"event": "clarify_asked", "count": n, "missing": missing}]
        return out_state

    # ══════════════ 紧急提示（固定模板，不走 LLM）═════════════
    async def emergency_draft(state: dict) -> dict:
        hits = state.get("emergency_hits") or []
        tier = hits[0].get("tier") if hits else "P1"
        return _draft(state, "emergency_draft", EM.notice_for(tier),
                      route_hint="human_emergency")

    return {"r_agent": r_agent, "c_agent": c_agent, "b_agent": b_agent,
            "p_agent": p_agent, "clarify": clarify, "emergency_draft": emergency_draft}


def _project_list(slots: dict) -> list[str]:
    """project 槽位是数组，规范成字符串列表。"""
    projects = slots.get("project") or []
    if isinstance(projects, str):
        projects = [projects]
    return [str(p) for p in projects if p]


def _project_text(slots: dict) -> str:
    """project 槽位是数组，拼成给人/给业务系统看的字符串。"""
    return "、".join(_project_list(slots))


def _missing_slots(slots: dict, intents: list[str]) -> list[str]:
    """按意图判断缺哪些关键槽位 —— 只有会实质影响回答的才追问。"""
    missing: list[str] = []
    has_project = bool(slots.get("project"))
    if not has_project:
        missing.append("project")
    if "recommend" in intents:
        if not slots.get("postop_days"):
            missing.append("recovery")
        missing.append("history")
    if "booking" in intents:
        if not slots.get("store"):
            missing.append("store")
    if "postcare" in intents and not slots.get("postop_days"):
        missing.append("postop_day")
    return T.dedupe(missing)[:3]

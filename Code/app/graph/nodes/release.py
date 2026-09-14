"""
输出与执行层：output_type / await_confirm / execute_op / receipt / set_result_kind / final_check

两条不变量：
  1. execute_op 只认「已审 + 已确认」，且靠 idempotency_key 做幂等
  2. interrupt 之前的代码在恢复时会被【重放】—— await_confirm 里绝不能有副作用
"""

from __future__ import annotations
from typing import Callable

from langgraph.types import interrupt

from ...prompts import release as P
from ...services.deps import Deps
from .. import progress
from ..schemas import ReceiptOut
from ..state import begin_review_round


def make_release_nodes(deps: Deps) -> dict[str, Callable]:

    # ══════════════ 输出类型判定（纯规则）══════════════
    async def output_type(state: dict) -> dict:
        kind = "operation" if state.get("operation") else "reply"
        return {"output_kind": kind,
                "audit_log": [{"event": "output_type", "kind": kind}]}

    # ══════════════ 等待用户确认（interrupt 挂起点）══════════════
    async def await_confirm(state: dict) -> dict:
        """
        ★ 这里的代码在 resume 时会被重放 —— 不能发消息、不能写库、不能扣费。
        出边分派放在父图的 add_conditional_edges 里，保持"菱形 = 路由表"的统一风格。
        """
        answer = interrupt({
            "type": "confirm_operation",
            "plan": (state.get("draft") or {}).get("content", ""),
            "plan_hash": state.get("plan_hash"),
            "expires_at": deps.now_plus(seconds=deps.settings.plan_expire_seconds),
        }) or {}

        if answer.get("plan_hash") != state.get("plan_hash"):
            return {"confirmed": False, "confirm_result": "mismatch"}
        if not answer.get("confirmed"):
            return {"confirmed": False, "confirm_result": "denied"}
        return {"confirmed": True, "confirm_result": "confirmed"}

    # ══════════════ 幂等执行 ══════════════
    async def execute_op(state: dict) -> dict:
        operation = state.get("operation") or {}
        plan_hash = state.get("plan_hash")
        content = (state.get("draft") or {}).get("content", "")

        # 前置校验：凭据必须与「内容 + 方案 + 审查类别」三者都匹配
        deps.security.verify_token(state.get("release_token"), content=content,
                                   plan_hash=plan_hash, review_kind="operation")
        if not state.get("confirmed"):
            raise PermissionError("执行被拒绝：缺少用户明确确认")

        key = f'{state.get("thread_id")}:{plan_hash}'
        if await deps.pg.op_already_done(key):
            result = await deps.pg.load_op_result(key)
            return {"execution_result": result, "idempotency_key": key,
                    "audit_log": [{"event": "execute_idempotent_hit", "key": key}]}

        progress.emit("execute")
        action = operation.get("action")
        params = operation.get("params") or {}
        if action == "change_appointment":
            result = await deps.pg.change_appointment(
                store=params.get("store", ""), datetime_=params.get("datetime", ""),
                request_id=key,
                # ★ 这两个必须原样带下来：appointment_id 决定"改哪一条"，
                #   expected_version 是乐观锁基准（比对的是用户确认时看到的那一版）。
                #   漏掉它们，SQL 会退化成 `WHERE appointment_id = NULL` 永不匹配，
                #   再被误报成"状态已变化" —— 排查方向会被完全带偏。
                appointment_id=params.get("appointment_id"),
                expected_version=params.get("expected_version"))
        else:
            # 其它动作在 MVP 里未实现 —— 明确报错，不要静默成功
            raise NotImplementedError(f"未实现的操作类型：{action}")

        await deps.pg.save_op_result(key, session_id=state.get("session_id", ""),
                                     action=action, params=params, result=result)
        return {"execution_result": result, "idempotency_key": key,
                "audit_log": [{"event": "executed", "action": action, "key": key}]}

    # ══════════════ 生成执行结果回复（第二类审查对象）══════════════
    async def receipt(state: dict) -> dict:
        result = state.get("execution_result") or {}
        action = (state.get("operation") or {}).get("action", "")
        try:
            out: ReceiptOut = await deps.llm.structured(
                "receipt", ReceiptOut, system=P.RECEIPT_SYSTEM,
                user=P.RECEIPT_USER.format(
                    user_input=state.get("user_input", ""), action=action,
                    result=_json(result)))
            content, gaps = out.content, list(out.gaps)
        except Exception as exc:  # noqa: BLE001
            # 生成失败不能让用户看不到结果 —— 退回结构化字段拼接（同样要过审）
            content = _fallback_receipt(result)
            gaps = [f"结果文案降级生成：{exc}"]
        return {"drafts": [{"agent": "receipt", "turn_id": state.get("turn_id"),
                            "revision": 0, "content": content, "citations": [],
                            "gaps": gaps, "route_hint": None, "risk_tags": [],
                            "operation": None}]}

    # ══════════════ 切换审查类别（同一个审查子图第二次调用）══════════════
    async def set_result_kind(state: dict) -> dict:
        drafts = [d for d in (state.get("drafts") or []) if d.get("agent") == "receipt"]
        content = drafts[-1]["content"] if drafts else (state.get("draft") or {}).get("content", "")
        return {
            "draft": {"content": content, "citations": [], "gaps": []},
            **begin_review_round(state, "result_reply"),
        }

    # ══════════════ 结果回复是否通过 ══════════════
    async def final_check(state: dict) -> dict:
        ok = state.get("verdict") == "pass"
        return {"final_ok": ok,
                "audit_log": [{"event": "final_check", "ok": ok,
                               "verdict": state.get("verdict")}]}

    return {"output_type": output_type, "await_confirm": await_confirm,
            "execute_op": execute_op, "receipt": receipt,
            "set_result_kind": set_result_kind, "final_check": final_check}


def _json(obj: dict) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _fallback_receipt(result: dict) -> str:
    """业务结果 → 结构化兜底文案（字段照抄，不改写）"""
    if not result:
        return "操作已提交，但未取得业务系统返回结果，请联系客服核实。"
    parts = ["操作结果如下："]
    mapping = [("appointment_id", "预约编号"), ("store", "门店"), ("datetime", "时间"),
               ("status", "状态"), ("fee_cents", "费用（分）")]
    for key, label in mapping:
        if result.get(key) not in (None, ""):
            parts.append(f"{label}：{result[key]}")
    parts.append("如需调整请告诉我。")
    return "\n".join(parts)

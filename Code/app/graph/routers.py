"""主图的路由函数（图上每个菱形对应这里的一个函数）。"""

from __future__ import annotations

from typing import Callable

from ..services.deps import Deps


def make_routers(deps: Deps) -> dict[str, Callable]:

    def after_emergency_screen(state: dict) -> str:
        return "hit" if state.get("emergency") else "miss"

    def after_risk_gate(state: dict):
        """
        五档出口 + 一个特例：
          紧急 / 上传图片（force_human）→ 放行已审提示 与 立即转人工 两条并行出口。
          这对应设计图里 emergency_check 那两条都标"是"的边。

        ★ 直接返回节点名（或节点名列表），不要配 path_map ——
          path_map 的值不能是列表，否则 LangGraph 校验报 unhashable type。
        """
        verdict = state.get("verdict") or "human"
        if verdict == "pass":
            if state.get("force_human"):
                return ["human_handoff", "send"]
            return "output_type"
        return {
            "revise": "feedback",
            "need_info": "send",
            "block": "audit_block",
            "human": "human_handoff",
        }.get(verdict, "human_handoff")

    def after_output_type(state: dict) -> str:
        return state.get("output_kind") or "reply"

    def after_confirm(state: dict) -> str:
        """confirmed / denied / mismatch 三分支，后两者都作废方案。"""
        return state.get("confirm_result") or "denied"

    def after_final_check(state: dict) -> str:
        return "ok" if state.get("final_ok") else "fail"

    def after_retry_check(state: dict) -> str:
        return "retry" if state.get("retry_ok") else "exhausted"

    return {
        "after_emergency_screen": after_emergency_screen,
        "after_risk_gate": after_risk_gate,
        "after_output_type": after_output_type,
        "after_confirm": after_confirm,
        "after_final_check": after_final_check,
        "after_retry_check": after_retry_check,
    }

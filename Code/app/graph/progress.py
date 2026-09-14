"""
进度事件：向 SSE 推送"安全的过程状态"。

三条纪律：
  1. **只发固定文案**，绝不把模型生成的内容流式发出去 ——
     设计上要求"通过才发送"，正文必须整段审后发送。
     所以这里发的是"正在查阅审核资料…"这类无风险的过程提示。
  2. 在非流式调用（CLI 直接 ainvoke、单元测试）里必须是**无害 no-op**，
     不能因为拿不到 writer 就报错。
  3. 每个 stage 的文案在一张表里，便于统一改口径与做多语言。

⚠️ **作用域限制（实测）**：子图内部节点调用 emit() **不会**冒泡到父图的事件流
   —— custom writer 是按图作用域绑定的。所以父图在"进入子图前"自己发一条进度
   （例如 aggregate 发 review、dispatch 发 retrieve），保证前端始终能看到反馈。
   子图内部的 emit 保留，是为了单独调试子图时也有进度。
"""

from __future__ import annotations

from typing import Any

#: stage → 固定文案（前端只展示，不参与任何判定）
STAGE_TEXT: dict[str, str] = {
    "classify": "正在理解您的问题…",
    "emergency": "正在优先处理您的情况…",
    "retrieve": "正在查阅审核资料…",
    "draft": "正在整理说明…",
    "verify": "正在核对事实与依据…",
    "review": "正在进行合规审查…",
    "revision": "正在按审查意见修改…",
    "execute": "正在提交您的操作…",
    "receipt": "正在核对执行结果…",
    "handoff": "正在为您转接人工…",
}


def emit(stage: str) -> None:
    """发一条进度事件。拿不到 writer 就静默跳过。"""
    text = STAGE_TEXT.get(stage)
    if not text:
        return
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
        if writer is None:
            return
        writer({"stage": stage, "text": text})
    except Exception:  # noqa: BLE001
        # 非流式上下文（CLI ainvoke / 单测）里拿不到 writer 是正常的
        return


def collect(payload: Any) -> dict | None:
    """给事件映射层用：把 custom chunk 规范化成事件体。"""
    if isinstance(payload, dict) and "stage" in payload:
        return {"stage": payload["stage"], "text": payload.get("text") or STAGE_TEXT.get(payload["stage"], "")}
    return None

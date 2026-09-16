"""
HTTP 接口层。

端点：
  GET  /api/health                     健康检查
  POST /api/sessions                   新建会话（返回 session_id / thread_id）
  GET  /api/sessions/{sid}             会话信息 + 最近消息（出站内容已脱敏）
  POST /api/chat/{sid}/stream          ★ SSE：一次对话
  POST /api/chat/{sid}/confirm         ★ SSE：用户确认后恢复挂起的图

三个必须守住的设计点：
  1. **正文不流式** —— 过程用 status 事件流式，正文整段审后通过 final 事件发出
  2. **挂起不是结束** —— interrupt 后本次流以 awaiting_confirmation 收尾，
     用户确认走另一个 HTTP 请求（Command(resume=...)），这样线程不必长期占用
  3. **人工接管优先** —— 会话被人工接管后（ai_enabled=false）直接 409，不让 AI 抢话
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from ..graph import progress
from ..runtime import Runtime
from ..services import text as T
from ..services import trace
from .events import (EVENT_BLOCKED, EVENT_DONE, EVENT_ERROR, EVENT_FINAL, EVENT_HANDOFF,
                     EVENT_NODE, EVENT_STATUS, SSE_HEADERS, error_event, map_patch,
                     node_detail, ping, sse)
from .schemas import ChatIn, ConfirmIn, CreateSessionIn, HealthOut, MessageOut, SessionOut

router = APIRouter(prefix="/api")

#: SSE 心跳间隔：超过这个时间没有事件就发一行注释，避免代理断连
HEARTBEAT_SECONDS = 15.0


def _rt(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - 只会在 lifespan 未执行时出现
        raise HTTPException(status_code=503, detail={"code": "not_ready"})
    return runtime


# ════════════════════════════════════════════════════════════════
#  健康检查与会话
# ════════════════════════════════════════════════════════════════
@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    rt = _rt(request)
    return HealthOut(status="ok", profile=rt.settings.profile,
                     checkpointer=rt.checkpointer_kind,
                     graph=getattr(rt.graph, "name", "zhimei_main"))


@router.post("/sessions", response_model=SessionOut)
async def create_session(body: CreateSessionIn, request: Request) -> SessionOut:
    rt = _rt(request)
    session_id = str(uuid.uuid4())
    ensure = getattr(rt.deps.pg, "ensure_session", None)
    if ensure is not None:
        try:
            await ensure(session_id, channel=body.channel, user_id=body.user_id)
        except Exception:  # noqa: BLE001
            # 业务库不可用不应阻断建会话（演示与本地开发常见）
            pass
    session = await rt.deps.pg.get_session(session_id)
    return SessionOut(session_id=session_id, thread_id=session.get("thread_id") or session_id,
                      channel=body.channel, ai_enabled=bool(session.get("ai_enabled", True)),
                      status=session.get("status", "active"))


@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(session_id: str, request: Request, limit: int = 10) -> dict:
    rt = _rt(request)
    session = await rt.deps.pg.get_session(session_id)
    rows = await rt.deps.pg.recent_turns(session_id, limit=limit)
    return {
        "session": SessionOut(
            session_id=session_id, thread_id=session.get("thread_id") or session_id,
            channel=session.get("channel", "web"),
            ai_enabled=bool(session.get("ai_enabled", True)),
            status=session.get("status", "active")).model_dump(),
        # 出站内容返回前再脱敏一次（双保险）
        "messages": [MessageOut(role=m.get("role", ""), content=T.mask(m.get("content", "")),
                                review_kind=m.get("review_kind"),
                                risk_level=m.get("risk_level"),
                                created_at=m.get("created_at")).model_dump()
                     for m in rows],
    }


# ════════════════════════════════════════════════════════════════
#  对话（SSE）
# ════════════════════════════════════════════════════════════════
@router.post("/chat/{session_id}/stream")
async def chat_stream(session_id: str, body: ChatIn, request: Request,
                      trace: bool = Query(default=False,
                                          description="是否发送节点执行轨迹（node 事件）。"
                                                      "演示与排障用；生产环境保持 false")) -> StreamingResponse:
    rt = _rt(request)
    await _guard(rt, session_id)
    if not await rt.try_begin(session_id):
        raise HTTPException(status_code=409, detail={"code": "busy",
                                                     "message": "该会话正在处理上一条消息"})
    payload = {"session_id": session_id, "user_input": body.text, "channel": "api",
               "attachments": body.attachments}
    return _sse_response(_event_source(rt, session_id, payload, trace_enabled=trace))


@router.post("/chat/{session_id}/confirm")
async def chat_confirm(session_id: str, body: ConfirmIn, request: Request,
                       trace: bool = Query(default=False)) -> StreamingResponse:
    rt = _rt(request)
    await _guard(rt, session_id)
    if not await rt.try_begin(session_id):
        raise HTTPException(status_code=409, detail={"code": "busy",
                                                     "message": "该会话正在处理上一条消息"})
    resume = {"confirmed": body.confirmed, "plan_hash": body.plan_hash}
    return _sse_response(_event_source(rt, session_id, Command(resume=resume),
                                       trace_enabled=trace))


async def _guard(rt: Runtime, session_id: str) -> None:
    """人工接管守卫：会话被坐席接管后，AI 不允许再自动回复。"""
    session = await rt.deps.pg.get_session(session_id)
    if session.get("ai_enabled") is False:
        raise HTTPException(status_code=409, detail={
            "code": "human_takeover",
            "message": "当前会话已由人工客服接管，请由坐席处理",
        })


def _sse_response(source: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(source, headers=SSE_HEADERS, media_type="text/event-stream")


# ════════════════════════════════════════════════════════════════
#  事件源
# ════════════════════════════════════════════════════════════════
async def _event_source(rt: Runtime, session_id: str,
                        graph_input: Any, *, trace_enabled: bool = False) -> AsyncIterator[str]:
    """把图的执行过程翻译成 SSE 流。无论成功失败都必须释放在途标记。

    trace_enabled=True 时额外发送 node 事件（节点执行轨迹），并开启 subgraphs=True
    以便看到子图内部节点。**默认关闭**，原因见 events.EVENT_NODE 的注释。
    """
    turn_id: str | None = None
    seq = 0
    last_at = time.perf_counter()
    # 把本轮 thread_id 放进上下文：模型网关据此把每次调用记到 app.llm_call_log。
    # ★ 这里用不带还原的 set_turn 而非 turn_scope：ASGI 每个请求本来就是独立任务、
    #   持有上下文的副本，请求结束整个上下文一起丢弃，不存在"漏给下一个请求"；
    #   而在异步生成器里跨 yield 做 reset 反而可能踩到 token 不匹配。
    trace.set_turn(session_id)
    try:
        async for item in _with_heartbeat(
                lambda: rt.graph.astream(graph_input, rt.config(session_id),
                                         stream_mode=["custom", "updates"],
                                         subgraphs=trace_enabled),
                HEARTBEAT_SECONDS):
            if item is None:                       # 心跳
                yield ping()
                continue
            # ★ subgraphs=True 会把产出从 (mode, chunk) 变成 (namespace, mode, chunk)。
            #   这里统一成 (namespace, mode, chunk)，让两条路径共用下面的处理逻辑 ——
            #   否则就得维护两份几乎一样的解析代码，迟早改漏一边。
            if trace_enabled:
                namespace, mode, chunk = item
            else:
                namespace, (mode, chunk) = (), item

            if mode == "custom":
                ev = progress.collect(chunk)
                if ev:
                    yield sse(EVENT_STATUS, ev)
                continue

            now = time.perf_counter()
            elapsed_ms = int((now - last_at) * 1000)
            last_at = now
            for node, patch in (chunk or {}).items():
                # ★ 挂起事件在 updates 流里是【顶层键】"__interrupt__"，不是某个节点的 patch。
                #   早先只按"节点 → patch"处理，结果挂起被静默丢掉，前端永远等不到确认请求。
                if node == "__interrupt__":
                    for name, data in map_patch("__interrupt__", {"__interrupt__": patch}):
                        yield sse(name, data)
                    continue
                if trace_enabled:
                    # ★ 三个"结构"字段 + 一个"内容"字段（detail）。
                    #   detail 会带出该节点写回的状态摘要 —— 包括**未审草稿、
                    #   审查意见、检索到的证据原文**。这些在正常流程里绝不外露，
                    #   所以它只在这里发（trace 模式），生产环境必须关掉。
                    #   详见 events.node_detail 的注释。
                    seq += 1
                    yield sse(EVENT_NODE, {
                        "node": node, "seq": seq, "ms": elapsed_ms,
                        "ns": list(namespace),
                        "detail": node_detail(patch),
                    })
                if node == "normalize" and isinstance(patch, dict) and patch.get("turn_id"):
                    turn_id = patch["turn_id"]
                for name, data in map_patch(node, patch):
                    yield sse(name, data)
        yield sse(EVENT_DONE, {"session_id": session_id, "turn_id": turn_id})
    except GraphRecursionError:
        # 步数上限不是 500 —— 它是"图没能在预算内收敛"，必须转人工
        ticket = await _force_handoff(rt, session_id, reason="recursion_limit", priority="P1")
        yield sse(EVENT_HANDOFF, ticket)
        yield sse(*error_event("recursion_limit", "处理步数超出上限，已转人工"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        # ★ 兜底转人工，而不是只发一个 error 就结束。
        #
        #   原来的写法是"发个 error 事件，流结束"，后果是：用户问了一句话，
        #   收到的只有静默失败 —— 没有回答、没有工单、也没有人说会跟进。
        #   对客系统里这是最糟的一种失败：用户以为系统坏了，而运营侧根本不知道
        #   有人被晾在那里。
        #
        #   这与项目里其它地方的取向是一致的（"审查失败绝不能当成通过 → 转人工"）：
        #   宁可多转一次人工，也不要让用户对着空气说话。
        #   error 事件照旧发出，异常类型与信息不丢，运营侧仍然看得见问题。
        ticket = await _force_handoff(rt, session_id, reason="internal_error", priority="P1")
        yield sse(EVENT_HANDOFF, ticket)
        yield sse(*error_event("internal", f"{type(exc).__name__}: {exc}"))
    finally:
        await rt.end(session_id)


async def _with_heartbeat(source_factory, interval: float) -> AsyncIterator[Any]:
    """
    给图的 astream 套一层心跳：长时间没有事件时产出 None（由调用方发注释行）。

    同时解决一个容易忽略的问题：客户端断开时 StreamingResponse 会取消这个生成器，
    finally 里取消底层任务，避免图在后台继续跑（白烧 token）。
    """
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def pump() -> None:
        try:
            async for item in source_factory():
                await queue.put(("item", item))
        except Exception as exc:  # noqa: BLE001
            await queue.put(("error", exc))
        finally:
            await queue.put(("done", sentinel))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield None
                continue
            if kind == "item":
                yield value
            elif kind == "done":
                return
            else:
                raise value
    finally:
        if not task.done():
            task.cancel()


async def _force_handoff(rt: Runtime, session_id: str, *, reason: str,
                         priority: str) -> dict:
    try:
        ticket = await rt.deps.pg.create_handoff_ticket(
            session_id=session_id, thread_id=session_id, user_id=None,
            reason=reason, priority=priority,
            profile_summary="（系统兜底转人工，无画像摘要）", last_turns=[],
            risk_report={"reason": reason})
        return {"ticket_id": ticket.get("ticket_id"), "priority": priority,
                "reason": reason, "text": "已为您转接人工客服，请稍候。"}
    except Exception:  # noqa: BLE001
        return {"ticket_id": None, "priority": priority, "reason": reason,
                "text": "已为您转接人工客服，请稍候。"}


# 供调试：把事件体转成可读的一行（不参与生产路径）
def dump_event(name: str, data: dict) -> str:  # pragma: no cover
    return f"{name} {json.dumps(data, ensure_ascii=False)}"

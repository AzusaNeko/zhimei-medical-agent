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
  3. **人工接管优先** —— 会话被人工接管后（ai_enabled=false）AI 不再自动回复。
     但**用户仍然可以说话**：消息照常落库并转给坐席，只是 AI 不答
     （见 `_takeover_source`）。早期版本在这里直接返回 409 把用户挡在门外，
     结果是顾客刚被告知"已为您转接人工客服"，下一句就发不出去了 ——
     而他想补充的"我疼得更厉害了"，谁也收不到。
     唯一仍然拒绝的是 `confirm`：那一步是**不可逆**的（真的去改约），
     人工已在场时不该由 AI 单方面执行。**消息照收，操作不执行。**
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from ..graph import progress
from ..prompts import emergency as EM
from ..runtime import Runtime
from ..services import text as T
from ..services import trace
from ..services.auth import Principal
from .security import current_user
from .graph_topology import build_topology as _topology
from .events import (EVENT_BLOCKED, EVENT_DONE, EVENT_ERROR, EVENT_FINAL, EVENT_HANDOFF,
                     EVENT_NODE, EVENT_STATUS, SSE_HEADERS, error_event, map_patch,
                     node_detail, ping, sse, takeover_event)

logger = logging.getLogger("zhimei.api")
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
async def create_session(body: CreateSessionIn, request: Request,
                         user: Principal = Depends(current_user)) -> SessionOut:
    """建会话。**归属由令牌决定，不接受客户端传入的 user_id。**

    ★ 原来 user_id 是从请求体里读的（`body.user_id`）—— 那等于谁都能把会话
      挂到别人名下，进而用别人的身份去改预约。现在一律取令牌里的 user_id，
      `CreateSessionIn.user_id` 仅作为兼容字段保留，**被忽略**。
    """
    rt = _rt(request)
    session_id = str(uuid.uuid4())
    ensure = getattr(rt.deps.pg, "ensure_session", None)
    if ensure is not None:
        try:
            await ensure(session_id, channel=body.channel, user_id=user.user_id)
        except Exception:  # noqa: BLE001
            # 业务库不可用不应阻断建会话（演示与本地开发常见）
            pass
    session = await rt.deps.pg.get_session(session_id)
    return SessionOut(session_id=session_id, thread_id=session.get("thread_id") or session_id,
                      channel=body.channel, ai_enabled=bool(session.get("ai_enabled", True)),
                      status=session.get("status", "active"))


@router.get("/sessions")
async def list_sessions(request: Request,
                        limit: int = 30,
                        channel: str | None = None,
                        user: Principal = Depends(current_user)) -> dict:
    """当前用户的会话列表（"新对话 / 切换对话"用）。

    标题是**第一条用户消息**，不需要用户手动命名 —— 对话类产品里手动命名
    几乎没人用，而第一句话天然就是这一轮的意图。还没说话的会话 title 为空，
    前端显示成"（新对话）"。

    ★ 必须按登录用户过滤：这是上一版留下的缺口 —— 当时没有鉴权层，
      接口列出的是**全部**会话，等于把所有人的对话列表摊开。
    """
    rt = _rt(request)
    rows = await rt.deps.pg.list_sessions(limit=max(1, min(limit, 100)), channel=channel,
                                          user_id=user.user_id)
    return {"sessions": rows, "count": len(rows)}


@router.get("/graph", response_model=dict)
async def graph_topology(request: Request,
                         user: Principal = Depends(current_user)) -> dict:
    """工作流拓扑（给前端画执行图用）。

    ★ 是从**编译好的图**上读出来的，不是手写的常量 —— 手写的那份迟早会漂移，
      而一张少了边的流程图比没有图更糟（它会让人对执行过程产生错误判断）。
      详见 `graph_topology.py` 的说明。

    需要登录：拓扑本身不敏感，但这个接口没有任何理由对未登录用户开放。
    """
    return _topology(_rt(request))


@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(session_id: str, request: Request, limit: int = 10,
                      user: Principal = Depends(current_user)) -> dict:
    rt = _rt(request)
    await _own_session(rt, session_id, user.user_id)
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
                                                      "演示与排障用；生产环境保持 false"),
                      user: Principal = Depends(current_user)) -> StreamingResponse:
    rt = _rt(request)
    await _own_session(rt, session_id, user.user_id)

    # ★ 人工接管期间：**照样收下这句话**，记下来、转给坐席，但不跑图。
    #   放在 try_begin 之前 —— 这条路根本不占图的执行名额，没必要抢锁，
    #   也就不会因为"上一轮还在跑"而把用户的话拒掉（那种场景恰恰最需要收下它）。
    takeover = await _takeover_info(rt, session_id)

    # ★ 用户主动要求转人工：先安抚、够次数才真转，**不跑图**。
    #   紧急信号优先：一句话里带"人工"不意味着可以放过急诊 ——
    #   所以先判紧急，是紧急就照旧走图（急诊 + 转人工并行），不走这条。
    if rt.deps.rules.is_human_request(body.text):
        try:
            is_emergency = bool(rt.deps.rules.match_emergency(T.clean(body.text or "")))
        except Exception:  # noqa: BLE001
            is_emergency = True   # 判不出来就当紧急处理（走图，更安全）
        if not is_emergency:
            return _sse_response(_human_request_source(
                rt, session_id, body.text, taken_over=takeover is not None))

    if takeover is not None:
        return _sse_response(_takeover_source(rt, session_id, body.text, takeover))

    if not await rt.try_begin(session_id):
        raise HTTPException(status_code=409, detail={"code": "busy",
                                                     "message": "该会话正在处理上一条消息"})
    payload = {"session_id": session_id, "user_input": body.text, "channel": "api",
               "attachments": body.attachments}
    return _sse_response(_event_source(rt, session_id, payload, trace_enabled=trace))


@router.post("/chat/{session_id}/confirm")
async def chat_confirm(session_id: str, body: ConfirmIn, request: Request,
                       trace: bool = Query(default=False),
                       user: Principal = Depends(current_user)) -> StreamingResponse:
    rt = _rt(request)
    await _own_session(rt, session_id, user.user_id)

    # ★ 接管期间"确认执行"走**记录 + 明确未执行**，而不是 409。
    #   确认动作本身要让坐席看得见（有人正想执行一个方案），
    #   但操作绝不能执行；事件里 executed=false 显式写死，客户端不会误判成功。
    takeover = await _takeover_info(rt, session_id)
    if takeover is not None:
        # ★ 接管期间"确认执行"走**记录 + 明确未执行**，而不是拒绝。
        #   确认动作本身要让坐席看得见（有人正想执行一个方案），
        #   但操作绝不能执行；事件里 executed=false 显式写死，客户端不会误判成功。
        #
        # ★ 为什么消息照收、操作却不执行：
        #     · 说话是**可逆**的（记下来、转给坐席），把用户挡在门外只会让他在
        #       被告知"马上有人来"之后再发不出一句话；
        #     · 确认执行是**不可逆**的（真的去改约），而此刻人工已经介入，
        #       AI 单方面执行一个坐席看不见上下文的操作，风险明显更大。
        #   所以两者在这里分道扬镳：**消息照收，操作不执行。**
        what = "确认执行" if body.confirmed else "取消"
        return _sse_response(_takeover_source(
            rt, session_id,
            f"[系统记录] 用户点了「{what}」，但会话已被人工客服接管，该操作未执行",
            takeover,
            note=("您的操作**没有执行**：当前会话已由人工客服接管，"
                  "为避免重复操作，改约/取消这类操作请交给客服确认。")))

    if not await rt.try_begin(session_id):
        raise HTTPException(status_code=409, detail={"code": "busy",
                                                     "message": "该会话正在处理上一条消息"})
    resume = {"confirmed": body.confirmed, "plan_hash": body.plan_hash}
    return _sse_response(_event_source(rt, session_id, Command(resume=resume),
                                       trace_enabled=trace))


async def _takeover_info(rt: Runtime, session_id: str) -> dict | None:
    """会话处于人工接管就返回工单信息，否则 None。"""
    session = await rt.deps.pg.get_session(session_id)
    if session.get("ai_enabled") is not False:
        return None
    try:
        ticket = await rt.deps.pg.open_ticket_for_session(session_id)
    except Exception:  # noqa: BLE001
        logger.exception("查未结束工单失败（session=%s）", session_id)
        ticket = None
    return {"ticket": ticket or {}, "session": session}


def _takeover_notice(info: dict) -> tuple[str, bool, str | None]:
    """接管期间给用户的回执文案。返回 (文案, 是否已接单, 坐席名)。

    ★ 文案**必须**区分"已接单"和"还在排队"。项目里有一条硬约束：
      没接单就不能告诉用户"人工已接入" —— 说了等于替一个还没接手的人承诺。
      这里同样：排队时只能说"已转达/排队中"。

    ★ 也**不把坐席的 id 写进给顾客看的话里**。第一版写的是
      "已转达正在为您服务的客服（agent-service）" —— `agent-service`
      是内部的工号，对顾客毫无意义，还会让人以为在跟一个机器人对话。
      真要显示就显示姓名，拿不到姓名就不显示。
    """
    ticket = info.get("ticket") or {}
    accepted = bool(ticket.get("accepted_at"))
    if accepted:
        return ("已收到，并已转达正在为您服务的客服。AI 已暂停自动回复，"
                "客服会在这里继续回复您。"), True, None
    return ("已收到，并已排队转给人工客服。AI 已暂停自动回复；"
            "客服接单后会看到您刚才补充的内容。"), False, None


async def _human_request_source(rt: Runtime, session_id: str, text: str, *,
                                taken_over: bool) -> AsyncIterator[str]:
    """用户**主动要求转人工**时的处理：先安抚、够次数才真转（不跑图）。

    ★ 为什么不交给图去判断：这件事的结果是"把会话转给真人"，代价高、且必须
      可预测可复现。走规则 + 固定话术，既不烧 token，也不会出现
      "模型今天心情好就把人转走了"。

    ★ 为什么要连续 N 次：中文里"人工"两个字太随意（"人工客服几点下班"、
      "人工费怎么算"），一次就转会让转人工率虚高、坐席被无谓占用 ——
      与项目里"二次复核白升"那个问题是同一类。

    ★ 但门槛**必须如实告诉用户**（见 HUMAN_REQUEST_REASSURE）：
      含糊地说"稍后为您转接"再拖三次，那是在骗人。

    ★ 紧急情况不走这里：调用方已经先判过紧急信号（见 chat_stream），
      宁可走图的急诊路径，也不能因为一句话里带"人工"就把急诊降级成排队。
    """
    saved = T.clean(text or "")
    try:
        await rt.deps.pg.save_message(session_id=session_id, turn_id=str(uuid.uuid4()),
                                      role="user", content=saved,
                                      meta={"channel": "api", "human_request": True})
    except Exception:  # noqa: BLE001
        logger.exception("转人工请求落库失败（session=%s）", session_id)

    n = await rt.deps.pg.bump_human_request(session_id)
    total = rt.deps.rules.human_request_threshold

    if taken_over:
        # AI 已经停了，人已经在处理/排队 —— 没什么可"转"的了，
        # 能给的就是安抚 + 让他知道这条也被记下了。
        notice = EM.HUMAN_REQUEST_ALREADY.format(n=n)
        await _save_notice(rt, session_id, notice)
        yield sse(*takeover_event(notice, accepted=True))
        yield sse(EVENT_DONE, {"session_id": session_id, "turn_id": None})
        return

    if n < total:
        notice = EM.HUMAN_REQUEST_REASSURE.format(n=n, total=total)
        await _save_notice(rt, session_id, notice)
        # ★ 用 final 而不是自定义事件：这是一次**正常的 AI 回复**
        #   （只不过措辞是固定模板），前端不需要为它多写一套渲染。
        yield sse(EVENT_FINAL, {"text": notice, "token": "", "kind": "template",
                                "citations": [], "handoff": False})
        yield sse(EVENT_DONE, {"session_id": session_id, "turn_id": None})
        return

    # 达到阈值：真的转交。★ 用户主动要求的转交**当场停掉 AI**，
    # 不等坐席接单 —— 与"系统推断出来的转交"刻意不同：
    # 后者（急诊、风险高）AI 可能还是最快的帮手，所以让它答到人工接手为止；
    # 而用户明确说了"我要人"，再让 AI 抢话就是不尊重他的选择。
    ticket = await _force_handoff(rt, session_id, reason="user_requested", priority="P1")
    await rt.deps.pg.set_ai_enabled(session_id, False)
    await _save_notice(rt, session_id, EM.HUMAN_REQUEST_GRANTED)
    yield sse(EVENT_HANDOFF, {**ticket, "text": EM.HUMAN_REQUEST_GRANTED,
                              "reason": "user_requested", "priority": "P1"})
    yield sse(EVENT_DONE, {"session_id": session_id, "turn_id": None})


async def _save_notice(rt: Runtime, session_id: str, notice: str) -> None:
    """把固定话术也落库（否则对话记忆里只有用户单方面的发言）。"""
    try:
        await rt.deps.pg.save_message(session_id=session_id, turn_id=str(uuid.uuid4()),
                                      role="assistant", content=notice,
                                      review_kind="template")
    except Exception:  # noqa: BLE001
        logger.exception("固定话术落库失败（session=%s）", session_id)


async def _takeover_source(rt: Runtime, session_id: str, text: str, info: dict,
                           *, note: str | None = None) -> AsyncIterator[str]:
    """接管期间的处理：**记下来、转过去、不抢话**（不跑图、不产生 AI 正文）。

    ★ 为什么不复用 `_event_source`：那条路会去跑图。接管期间跑图就等于
      AI 又在自动回复了，正好是"AI 不与坐席抢话"要禁止的事。
      这里只做两件事：落库 + 回一个明确的事件。
    """
    notice, accepted, agent_name = _takeover_notice(info)
    ticket_id = (info.get("ticket") or {}).get("ticket_id")
    if note:
        notice = note

    # ★ 回执只在**接管开始后的第一条**弹给用户看。
    #   接管期间顾客常常连着补充好几句（"还有点发烧"→"现在又吐了"），
    #   每句都插一个"已收到，并已转达…"的气泡会变成刷屏 ——
    #   用户会以为系统在报错，真正重要的那句话反而被淹掉。
    #   之后只用状态栏提示一行（quiet=True），消息照常落库转给坐席。
    try:
        already = await rt.deps.pg.count_takeover_messages(session_id)
    except Exception:  # noqa: BLE001
        logger.exception("查接管回执次数失败（session=%s）", session_id)
        already = 0            # 查不出来就当作第一次（宁可多说一句，也不要漏说）
    quiet = already > 0

    turn_id = str(uuid.uuid4())
    try:
        await rt.deps.pg.save_message(
            session_id=session_id, turn_id=turn_id, role="user",
            content=T.clean(text or ""),
            meta={"channel": "api", "human_takeover": True})
    except Exception:  # noqa: BLE001
        # 落库失败也必须让用户收到回执 —— 但这是"坐席看不到这句话"的严重问题，
        # 所以一定要留日志，不能像什么都没发生。
        logger.exception("接管期间用户消息落库失败（session=%s）", session_id)
        notice = ("已收到您的消息，但系统暂时没能把它转给客服，"
                  "麻烦您稍后再发一次，或直接等待客服回复。")
        quiet = False          # 这种情况必须弹出来，不能只留在状态栏

    yield sse(*takeover_event(notice, ticket_id=ticket_id,
                              accepted=accepted, agent_name=agent_name,
                              quiet=quiet))
    yield sse(EVENT_DONE, {"session_id": session_id, "turn_id": turn_id})


async def _own_session(rt: Runtime, session_id: str, user_id: str) -> None:
    """确认会话属于当前用户。**不归你就当作不存在。**

    ★ 这里用 404 而不是 403 是刻意的：403 等于告诉对方"这个会话存在，
      但不归你" —— 那就成了一个探测别人会话 id 的接口。404 什么都不泄漏。
    ★ 归属为空的会话（历史遗留、或未绑定用户的匿名会话）同样按 404 处理，
      而不是"谁先来就归谁" —— 后者等于给了一条抢注别人会话的路径。
    """
    session = await rt.deps.pg.get_session(session_id)
    owner = session.get("user_id")
    if not owner or str(owner) != str(user_id):
        raise HTTPException(status_code=404, detail={
            "code": "session_not_found", "message": "会话不存在"})


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
        logger.warning("图步数超上限（session=%s），已兜底转人工", session_id)
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
        #
        # ★ 但"运营侧看得见"这件事**不能只靠 SSE 流**：那条流是给用户看的，
        #   而异常发生在服务端。这里曾经只发 error 事件、**不打日志**，
        #   后果是一次真实的服务端异常在服务端日志里查无此事 ——
        #   只有恰好盯着浏览器事件流的人才知道出过错。
        #   所以下面这行 logger.exception 是必需的，不是可选的：
        #   它保证异常类型、消息和**完整调用栈**都落在服务端日志里。
        logger.exception("图执行异常，已兜底转人工（session=%s）", session_id)
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

"""
运营后台的 HTTP 接口（坐席工作台）。

  GET  /ops/tickets                        队列（默认只看待处理）
  GET  /ops/tickets/{id}                   详情：画像 / 最近对话 / 风险报告
  POST /ops/tickets/{id}/accept            接单（→ 关掉 AI）
  POST /ops/tickets/{id}/reply             回复（规则层轻校验，不调 LLM）
  POST /ops/tickets/{id}/escalate          升级给值班医师
  POST /ops/tickets/{id}/close             关闭（→ 恢复 AI）
  POST /ops/tickets/{id}/reopen            重新打开
  POST /ops/tickets/{id}/misreport         标记误报（词表调优的唯一数据来源）
  GET  /ops/metrics                        指标看板
  GET  /ops/stream                         SSE：队列快照 + SLA 告警
  GET  /ops/panel                          监控面板（单文件 HTML，无构建步骤）

说明：MVP 的队列实时性用**服务端轮询推送**实现（每 3 秒推一次快照）。
生产环境应换成 Redis pub/sub 或 Postgres LISTEN/NOTIFY —— 换的只是这个流，
前端契约（snapshot / alert 两种事件）不用动。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..graph.progress import STAGE_TEXT  # noqa: F401  (保持与聊天侧同一个文案源)
from ..runtime import Runtime
from . import service
from .deps import Agent, current_agent, require

router = APIRouter(prefix="/ops")

PANEL_FILE = Path(__file__).parent / "panel.html"
QUEUE_POLL_SECONDS = 3.0
#: 单条 SSE 连接的最长寿命（防止客户端悄悄断开后留下僵尸连接占着内存与连接池）
MAX_TICKS = int(30 * 60 / QUEUE_POLL_SECONDS)


def _rt(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail={"code": "not_ready"})
    return runtime


def _fail(exc: service.OpsError) -> HTTPException:
    return HTTPException(status_code=exc.status,
                         detail={"code": exc.code, "message": exc.message})


# ════════════════════════════════════════════════════════════════
#  请求体
# ════════════════════════════════════════════════════════════════
class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    acknowledge_warnings: bool = Field(
        default=False, description="命中 revise 级规则时，前端提示后需带 true 再发一次")


class CloseIn(BaseModel):
    # 不在这里限制 min_length：让"缺原因"由领域层给出 reason_required，
    # 而不是被 Pydantic 拦成通用的 422 —— 前端需要能区分"参数格式错"和"业务上必须填原因"
    reason: str = Field(default="", max_length=200)


class EscalateIn(BaseModel):
    reason: str = ""


class MisreportIn(BaseModel):
    source: str = Field(default="emergency", description="emergency | hard_rule | risk_tag")
    ref_id: str = ""
    raw_message: str = ""
    verdict: str = Field(default="false_positive", description="true_positive | false_positive")
    note: str = ""


# ════════════════════════════════════════════════════════════════
#  队列与详情
# ════════════════════════════════════════════════════════════════
@router.get("/tickets")
async def list_tickets(request: Request,
                       status: list[str] | None = Query(default=None),
                       priority: list[str] | None = Query(default=None),
                       limit: int = 50,
                       agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:read")
    rt = _rt(request)
    rows = await service.list_queue(rt, agent, statuses=status, priorities=priority, limit=limit)
    return {"agent": {"agent_id": agent.agent_id, "name": agent.name, "role": agent.role},
            "tickets": rows}


@router.get("/tickets/{ticket_id}")
async def ticket_detail(ticket_id: str, request: Request,
                        agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:read")
    rt = _rt(request)
    try:
        return await service.get_detail(rt, agent, ticket_id)
    except service.OpsError as exc:
        raise _fail(exc) from exc


# ════════════════════════════════════════════════════════════════
#  处置动作
# ════════════════════════════════════════════════════════════════
@router.post("/tickets/{ticket_id}/accept")
async def accept(ticket_id: str, request: Request,
                 agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:accept")
    rt = _rt(request)
    try:
        return await service.accept(rt, agent, ticket_id)
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.post("/tickets/{ticket_id}/reply")
async def reply(ticket_id: str, body: ReplyIn, request: Request,
                agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:reply")
    rt = _rt(request)
    try:
        # 命中 revise 级规则时先回 409 让前端提示，坐席确认后再带 acknowledge_warnings 重发
        result = await service.reply(rt, agent, ticket_id, body.text)
        if result.get("warnings") and not body.acknowledge_warnings:
            raise HTTPException(status_code=409, detail={
                "code": "soft_warning",
                "message": "内容命中以下规则，确认仍要发送请勾选后重试",
                "warnings": result["warnings"],
            })
        return result
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.post("/tickets/{ticket_id}/escalate")
async def escalate(ticket_id: str, body: EscalateIn, request: Request,
                   agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:escalate")
    rt = _rt(request)
    try:
        return await service.escalate(rt, agent, ticket_id, body.reason)
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.post("/tickets/{ticket_id}/close")
async def close(ticket_id: str, body: CloseIn, request: Request,
                agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:close")
    rt = _rt(request)
    try:
        return await service.close(rt, agent, ticket_id, body.reason)
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.post("/tickets/{ticket_id}/reopen")
async def reopen(ticket_id: str, request: Request,
                 agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:close")
    rt = _rt(request)
    try:
        return await service.reopen(rt, agent, ticket_id)
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.post("/tickets/{ticket_id}/misreport")
async def misreport(ticket_id: str, body: MisreportIn, request: Request,
                    agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "misreport:write")
    rt = _rt(request)
    try:
        return await service.misreport(rt, agent, ticket_id, **body.model_dump())
    except service.OpsError as exc:
        raise _fail(exc) from exc


@router.get("/metrics")
async def metrics(request: Request, agent: Agent = Depends(current_agent)) -> dict:
    require(agent, "ticket:read")
    return await service.metrics(_rt(request), agent)


# ════════════════════════════════════════════════════════════════
#  实时队列（SSE）
# ════════════════════════════════════════════════════════════════
@router.get("/stream")
async def stream(request: Request,
                 once: bool = Query(default=False,
                                    description="只推一帧就结束（轮询型客户端与测试用）"),
                 agent: Agent = Depends(current_agent)) -> StreamingResponse:
    require(agent, "ticket:read")
    rt = _rt(request)

    async def gen():
        last_alert: set[str] = set()
        ticks = 1 if once else MAX_TICKS
        try:
            for _ in range(ticks):
                if await request.is_disconnected():
                    break
                tickets = await service.list_queue(rt, agent, limit=50)
                yield _sse("snapshot", {
                    "tickets": [_queue_row(t) for t in tickets],
                    "metrics": await service.metrics(rt, agent),
                })
                # SLA 告警：只推跨过阈值的那一次，避免每 3 秒重复刷屏
                for t in tickets:
                    key = t["ticket_id"]
                    if t["sla_breached"] and key not in last_alert:
                        last_alert.add(key)
                        yield _sse("alert", {
                            "ticket_id": key,
                            "priority": t["priority"],
                            "wait_seconds": t["wait_seconds"],
                            "message": f'{t["priority"]} 工单已等待 {t["wait_seconds"]} 秒未接单',
                        })
                if once:
                    break
                await asyncio.sleep(QUEUE_POLL_SECONDS)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _queue_row(t: dict) -> dict:
    return {k: t.get(k) for k in ("ticket_id", "session_id", "reason", "priority", "status",
                                  "wait_seconds", "sla_seconds", "sla_breached",
                                  "accepted_at", "assigned_to", "context")}


# ════════════════════════════════════════════════════════════════
#  监控面板（单文件，无构建步骤）
# ════════════════════════════════════════════════════════════════
@router.get("/panel", response_class=HTMLResponse)
async def panel() -> HTMLResponse:
    if not PANEL_FILE.exists():
        return HTMLResponse("<h1>panel.html 缺失</h1>", status_code=500)
    return HTMLResponse(PANEL_FILE.read_text(encoding="utf-8"))

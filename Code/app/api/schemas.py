"""接口层请求 / 响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateSessionIn(BaseModel):
    channel: str = Field(default="web", description="wechat | app | web | cli")
    user_id: str | None = Field(default=None, description="已登录用户 id，可空表示匿名会话")


class SessionOut(BaseModel):
    session_id: str
    thread_id: str
    channel: str
    ai_enabled: bool
    status: str


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    confirmed: bool
    plan_hash: str | None = Field(
        default=None,
        description="必须是挂起时返回的方案哈希；不匹配则作废方案，绝不执行",
    )


class MessageOut(BaseModel):
    role: str
    content: str
    review_kind: str | None = None
    risk_level: str | None = None
    created_at: Any = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    profile: str
    checkpointer: str
    graph: str

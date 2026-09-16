"""
运营后台：坐席身份与权限。

═══ 这里补上的是一个真实的安全洞 ═══

在此之前，坐席身份是**客户端在请求头里自己声明的**：

    X-Agent-Role: compliance      ← 请求方自己说自己是合规岗
    X-Agent-Id:   whatever

后果很直接：任何能访问该接口的人，改一个请求头就能拿到**未脱敏的手机号**
（`RAW_FIELD_ROLES` 里 compliance / doctor 可以看到原文）。
对一个卖点是"合规"的系统来说，这是最不该有的洞 —— 脱敏做得再仔细，
只要角色能自己声明，就等于没做。

现在改成：**身份来自 JWT（签名过的），角色再回数据库核对当前值**。
请求头完全不再参与身份判定。详见 `app/api/security.py` 里
「token 说你是谁、数据库说你能干什么」那段。

接缝仍然保留：将来要接机构的 SSO / 客服系统登录态，**只改 current_agent 一处**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from ..services import text as T

Role = Literal["service", "doctor", "compliance", "admin"]

#: 角色能做什么（写在这里，路由层只查表，不散落 if）
PERMISSIONS: dict[str, set[str]] = {
    "service":    {"ticket:read", "ticket:accept", "ticket:reply", "ticket:close",
                   "ticket:escalate", "misreport:write"},
    "doctor":     {"ticket:read", "ticket:accept", "ticket:reply", "ticket:close",
                   "ticket:escalate", "misreport:write", "medical:view_raw"},
    "compliance": {"ticket:read", "audit:read", "rule:write", "misreport:write",
                   "medical:view_raw"},
    "admin":      {"*"},
}

#: 角色能看到哪些敏感字段（未列出 = 脱敏后返回）
RAW_FIELD_ROLES = {"doctor", "compliance", "admin"}


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    role: str

    def can(self, permission: str) -> bool:
        perms = PERMISSIONS.get(self.role, set())
        return "*" in perms or permission in perms

    @property
    def sees_raw_pii(self) -> bool:
        return self.role in RAW_FIELD_ROLES


class AgentLoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=190)
    password: str = Field(min_length=1, max_length=128)


async def current_agent(request: Request) -> Agent:
    """坐席身份依赖：令牌定身份 → 数据库读当前角色。

    实现放在 `app.api.security`（C 端与坐席的验签逻辑是同一套）。
    这里保留一个薄转发，让路由层继续写 `Depends(current_agent)`，
    将来换 SSO 时只改这一个函数。
    """
    from ..api.security import current_staff_agent

    return await current_staff_agent(request)


def require(agent: Agent, permission: str) -> None:
    if not agent.can(permission):
        raise HTTPException(status_code=403, detail={
            "code": "forbidden", "message": f"角色 {agent.role} 无权执行 {permission}"})


def mask_for(agent: Agent, text: str) -> str:
    """服务端脱敏：service 角色看不到完整手机号 / 身份证 / 银行卡。"""
    return text if agent.sees_raw_pii else T.mask(text)


def mask_rows(agent: Agent, rows: list[dict], fields: tuple[str, ...] = ("content",)) -> list[dict]:
    if agent.sees_raw_pii:
        return rows
    out = []
    for row in rows:
        item = dict(row)
        for f in fields:
            if isinstance(item.get(f), str):
                item[f] = T.mask(item[f])
        out.append(item)
    return out

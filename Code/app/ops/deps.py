"""
运营后台：坐席身份与权限（MVP 版）。

**这不是生产级的鉴权**，而是一个可替换的接缝：
  · MVP：从请求头读 X-Agent-Id / X-Agent-Role；缺省时给一个演示坐席，
    让监控面板开箱可用（否则演示时要先造一个登录系统）
  · 生产：换成你们的 SSO / 客服系统登录态，**只改 current_agent 这一个函数**

两条硬规则写在这里而不是文档里：
  1. service 角色拿到的手机号 / 身份证必须**服务端脱敏**，不能靠前端隐藏
  2. 放行权限与回复权限分离：被 block 的内容任何人都不能直接发给用户
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Header, HTTPException

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

DEMO_AGENT = {"agent_id": "demo-agent-0001", "name": "demo-agent-0001", "role": "service"}


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


async def current_agent(
    x_agent_id: str | None = Header(default=None),
    x_agent_role: str | None = Header(default=None),
) -> Agent:
    """
    解析坐席身份。

    ⚠️ 只从请求头读 **ASCII 的 agent_id 与 role**，不读显示名 ——
       HTTP 头按规范只能是 latin-1，中文名字塞进去 httpx/浏览器都会直接报错。
       显示名应当由 agent_id 去 ops.agent_user 查（生产做法），
       或由前端本地维护（MVP 做法）。

    MVP 允许缺省（返回演示坐席）是为了让监控面板开箱可用；
    生产环境请把 default 改成 None 并在这里抛 401。
    """
    role = (x_agent_role or DEMO_AGENT["role"]).strip()
    if role not in PERMISSIONS:
        raise HTTPException(status_code=403, detail={"code": "unknown_role",
                                                     "message": f"未知角色：{role}"})
    agent_id = (x_agent_id or DEMO_AGENT["agent_id"]).strip()
    # TODO(生产): name = await ops_lookup(agent_id) —— 从 ops.agent_user 取显示名
    return Agent(agent_id=agent_id, name=agent_id, role=role)


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

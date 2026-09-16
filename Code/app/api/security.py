"""
接口层的认证依赖。

═══ 一条贯穿全项目的规则：token 说"你是谁"，数据库说"你能干什么" ═══

JWT 里带了 `role`，但**不能用它来判权限**，原因有两个：

  1. 令牌一旦签发就无法撤回。坐席被降级、停用、离职之后，
     他手里那张还没过期的令牌仍然声称自己是 `compliance` —— 照发不误。
  2. 权限表（谁能做什么）会变。如果判权限时只看令牌里的字符串，
     改权限就得等所有旧令牌过期。

所以这里的做法是：**用令牌确定身份（user_id / agent_id），
再去数据库读当前的账号状态与角色**。多一次查询，换来的是
"停用即失效"和"改权限立即生效"。

（如果将来为了性能要去掉这次查询，正确做法是引入短过期时间 +
refresh token，或者一个可撤销的会话表 —— 而不是直接信任令牌里的 role。）
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..runtime import Runtime
from ..services.auth import AuthError, Principal, bearer_token, decode_token


def _rt(request: Request) -> Runtime:
    return request.app.state.runtime


def _fail(exc: AuthError) -> HTTPException:
    """认证失败统一转成 401，并带上可区分的 code 供前端处理。"""
    return HTTPException(status_code=401, detail={"code": exc.code, "message": exc.message})


def principal_of(request: Request) -> Principal:
    """从请求里解出主体（**不查库**）。仅用于"有没有登录"这类轻判断。"""
    rt = _rt(request)
    token = bearer_token(request.headers.get("authorization"))
    try:
        return decode_token(rt.settings, token)
    except AuthError as exc:
        raise _fail(exc) from exc


async def current_user(request: Request) -> Principal:
    """C 端当前用户。**必须已登录且邮箱已验证。**

    ★ 邮箱验证与身份核验是两回事，别混：
      · `email_verified` —— 邮箱是不是你的（本函数校验）→ 决定能不能登录
      · `user_identity.verified`（实名/手机号核验）→ 决定能不能办操作类业务
      前者挡的是"拿别人邮箱注册"，后者挡的是"冒名改别人的预约"。两者都要，不能互替。
    """
    p = principal_of(request)
    if p.is_staff:
        raise HTTPException(status_code=403, detail={
            "code": "staff_not_customer",
            "message": "坐席账号不能访问顾客端接口，请使用坐席工作台"})

    user = await _rt(request).deps.pg.get_user(p.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail={
            "code": "user_not_found", "message": "账号不存在或已被删除"})
    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail={
            "code": "user_disabled", "message": "账号已被停用"})
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail={
            "code": "email_not_verified", "message": "邮箱尚未验证，请先完成验证"})
    return Principal(user_id=str(user["user_id"]), role="customer",
                     name=user.get("display_name") or "")


async def optional_user(request: Request) -> Principal | None:
    """可选登录：没带令牌返回 None，带了但无效才报错。

    用在"登录与否都能看，但登录后能多看一些"的接口上（例如会话列表）。
    """
    if not bearer_token(request.headers.get("authorization")):
        return None
    return await current_user(request)


async def current_staff_agent(request: Request):
    """坐席当前身份：令牌确定 agent_id → 数据库读**当前**角色。

    返回 ops.deps.Agent（延迟导入避免循环依赖：ops 依赖 api 的 Runtime，
    api 又需要 ops 的 Agent 类型）。
    """
    from ..ops.deps import Agent, PERMISSIONS, RAW_FIELD_ROLES  # noqa: F401

    p = principal_of(request)
    if not p.is_staff:
        raise HTTPException(status_code=403, detail={
            "code": "not_staff", "message": "该接口仅限坐席访问"})

    row = await _rt(request).deps.pg.get_agent(p.user_id)
    if row is None:
        raise HTTPException(status_code=401, detail={
            "code": "agent_not_found", "message": "坐席账号不存在"})
    if row.get("status") != "active":
        raise HTTPException(status_code=403, detail={
            "code": "agent_disabled", "message": "坐席账号已被停用"})
    role = str(row.get("role") or "")
    if role not in PERMISSIONS:
        raise HTTPException(status_code=403, detail={
            "code": "unknown_role", "message": f"未知角色：{role}"})
    return Agent(agent_id=str(row["agent_id"]), name=str(row.get("name") or ""), role=role)

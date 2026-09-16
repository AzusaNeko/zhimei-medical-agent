"""
C 端认证：注册 / 邮箱验证 / 登录 / 当前用户。

设计上把两件事分清楚（它们经常被混为一谈）：

  · **邮箱验证** `email_verified` —— 这个邮箱是不是你的。决定**能不能登录**。
    挡的是"拿别人邮箱注册"。本项目没有邮件服务，所以是个占位流程
    （见 `EXPOSE_VERIFY_TOKEN`）。

  · **身份核验** `user_identity.verified` —— 你是不是你本人（实名/手机号）。
    决定**能不能办操作类业务**（改约、取消）。挡的是"冒名改别人的预约"。
    这条链路本来就在（`load_auth` → `auth.verified` → 操作类请求判 need_info）。

两者都要有，不能互相替代：邮箱验证过了不代表能改别人的预约，
实名过了也不代表这个人真的持有那个邮箱。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..runtime import Runtime
from ..services import auth as A
from .security import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: 邮箱长度上限（RFC 5321 的 254 是理论值，这里收紧到 190 更实际）
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
_MIN_PASSWORD = 10


def _rt(request: Request) -> Runtime:
    return request.app.state.runtime


class RegisterIn(BaseModel):
    email: str = Field(min_length=5, max_length=190)
    password: str = Field(min_length=_MIN_PASSWORD, max_length=128)
    display_name: str = Field(default="", max_length=40)


class VerifyIn(BaseModel):
    token: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=190)
    password: str = Field(min_length=1, max_length=128)


def _check_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(status_code=422, detail={
            "code": "invalid_email", "message": "邮箱格式不正确"})
    return e


def _check_password(pw: str) -> None:
    """密码强度：只要长度够就行。

    ★ 不强制"必须含大写+数字+符号"是**有意**的。那类规则的实际效果是把用户
      逼向 `Password1!` 这种可预测模式，安全收益很低。长度才是主要因素
      （配合本地 scrypt 慢哈希，离线爆破成本已经很高）。
      真正该做的是查弱密码表 —— 那属于后续工作。
    """
    if len(pw) < _MIN_PASSWORD:
        raise HTTPException(status_code=422, detail={
            "code": "weak_password",
            "message": f"密码至少 {_MIN_PASSWORD} 位（长度比复杂度更重要）"})


def _public_user(u: dict[str, Any]) -> dict[str, Any]:
    """对外输出的用户信息。**绝不含 password_hash**。"""
    return {
        "user_id": str(u.get("user_id") or ""),
        "email": u.get("email"),
        "display_name": u.get("display_name"),
        "email_verified": bool(u.get("email_verified")),
        "created_at": u.get("created_at"),
    }


@router.post("/register")
async def register(body: RegisterIn, request: Request) -> dict:
    rt = _rt(request)
    email = _check_email(body.email)
    _check_password(body.password)

    token = A.new_verify_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=rt.settings.email_verify_ttl_hours)
    try:
        user = await rt.deps.pg.create_user(
            email=email, password_hash=A.hash_password(body.password),
            display_name=body.display_name.strip(), verify_token=token,
            verify_expires=expires.isoformat())
    except ValueError as exc:
        # 邮箱已注册。★ 这里**如实告诉用户"已注册"**，而不是含糊其辞。
        #   含糊的做法（"如果该邮箱存在我们会发邮件"）能防邮箱枚举，
        #   但会让正常用户卡在"我到底注册过没有"。医疗场景里用户找回账号
        #   的体验更重要，所以选择直说 —— 这个取舍是刻意的，不是疏忽。
        raise HTTPException(status_code=409, detail={
            "code": "email_taken", "message": str(exc)}) from exc

    out = {"user": _public_user(user), "email_verified": False,
           "message": "注册成功，请完成邮箱验证后再登录"}
    if rt.settings.expose_verify_token:
        # 没有邮件服务期间的临时妥协，详见 settings.expose_verify_token 的注释
        out["verify_token"] = token
        out["note"] = ("演示环境：验证令牌直接返回。生产环境必须置 "
                       "EXPOSE_VERIFY_TOKEN=false，令牌只应出现在邮件里。")
    return out


@router.post("/verify-email")
async def verify_email(body: VerifyIn, request: Request) -> dict:
    rt = _rt(request)
    user = await rt.deps.pg.verify_user_email(body.token.strip())
    if user is None:
        raise HTTPException(status_code=400, detail={
            "code": "invalid_verify_token", "message": "验证令牌无效或已过期"})
    return {"user": _public_user(user), "email_verified": True,
            "message": "邮箱验证完成，现在可以登录了"}


@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict:
    rt = _rt(request)
    email = (body.email or "").strip().lower()
    user = await rt.deps.pg.find_user_by_email(email)

    # ★ 用户不存在与密码错误**返回同一个错误**：区分开会帮攻击者枚举出
    #   "哪些邮箱注册过"（这比注册接口的枚举更隐蔽，因为它不留下注册记录）。
    # ★ 用户不存在时也跑一次哈希校验：否则"不存在的邮箱"会立刻返回，
    #   而"存在但密码错"要等 50ms，响应时间差同样能枚举出账号。
    stored = (user or {}).get("password_hash")
    ok = A.verify_password(body.password, stored)
    if not user or not ok:
        raise HTTPException(status_code=401, detail={
            "code": "bad_credentials", "message": "邮箱或密码不正确"})

    if user.get("status") != "active":
        raise HTTPException(status_code=403, detail={
            "code": "user_disabled", "message": "账号已被停用"})
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail={
            "code": "email_not_verified", "message": "邮箱尚未验证，请先完成验证"})

    await rt.deps.pg.touch_user_login(str(user["user_id"]))
    token, ttl = A.issue_token(rt.settings, A.Principal(
        user_id=str(user["user_id"]), role="customer",
        name=user.get("display_name") or ""))
    return {"access_token": token, "token_type": "Bearer", "expires_in": ttl,
            "user": _public_user(user)}


@router.get("/me")
async def me(request: Request, principal: A.Principal = Depends(current_user)) -> dict:
    user = await _rt(request).deps.pg.get_user(principal.user_id)
    return {"user": _public_user(user or {}), "role": principal.role}

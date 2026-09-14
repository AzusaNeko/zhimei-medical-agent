"""
安全服务：方案哈希与放行凭据。

两条不变量：
  1. plan_hash 绑定「被审过的那一版方案」——用户确认的必须是这一版
  2. release_token 绑定「内容 + 方案 + 审查类别 + 有效期」——出站前强制校验
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


class TokenError(Exception):
    """凭据无效 / 过期 / 与内容不匹配。"""


def canonical(obj: Any) -> str:
    """稳定序列化：同样的内容必须得到同样的串（键排序、无空格）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_plan(operation: dict) -> str:
    """
    方案哈希：把「动作 + 参数」一起哈希。
    参数里必须包含会影响用户利益的部分（时间、门店、项目、费用），
    否则改了参数却复用旧凭据 —— 那是最危险的一类事故。
    """
    payload = {
        "action": operation.get("action"),
        "params": operation.get("params", {}),
    }
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReleaseClaims:
    content_hash: str
    plan_hash: str | None
    review_kind: str
    issued_at: int
    expires_at: int
    nonce: str


class SecurityService:
    """HMAC-SHA256 签名的自包含凭据（MVP 无需查库即可校验，同时写审计表备查）。"""

    def __init__(self, secret: str, token_ttl_seconds: int = 1800) -> None:
        if not secret:
            raise ValueError("RELEASE_SECRET 不能为空")
        self._secret = secret.encode("utf-8")
        self._ttl = token_ttl_seconds

    # ── 哈希（对外暴露成方法，节点里统一用 deps.security.xxx）──
    def hash_text(self, text: str) -> str:
        return hash_text(text)

    def hash_plan(self, operation: dict) -> str:
        return hash_plan(operation)

    # ── 签发 ──
    def sign_release(self, *, content: str, plan_hash: str | None, review_kind: str) -> str:
        now = int(time.time())
        claims = {
            "content_hash": hash_text(content),
            "plan_hash": plan_hash,
            "review_kind": review_kind,
            "issued_at": now,
            "expires_at": now + self._ttl,
            "nonce": base64.urlsafe_b64encode(hashlib.sha256(f"{now}{content}".encode()).digest()[:8]).decode(),
        }
        body = base64.urlsafe_b64encode(canonical(claims).encode("utf-8")).decode()
        sig = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    # ── 校验 ──
    def verify_token(self, token: str | None, *, content: str | None = None,
                     plan_hash: str | None = None, review_kind: str | None = None) -> ReleaseClaims:
        if not token or "." not in token:
            raise TokenError("缺少放行凭据")
        body, _, sig = token.rpartition(".")
        expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise TokenError("凭据签名不匹配")
        try:
            claims = json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise TokenError(f"凭据内容不可解析：{exc}") from exc

        if int(claims.get("expires_at", 0)) < int(time.time()):
            raise TokenError("凭据已过期")
        if content is not None and claims.get("content_hash") != hash_text(content):
            raise TokenError("内容与凭据不匹配（内容已被改动）")
        if plan_hash is not None and claims.get("plan_hash") != plan_hash:
            raise TokenError("方案与凭据不匹配（方案已被改动）")
        if review_kind is not None and claims.get("review_kind") != review_kind:
            raise TokenError("审查类别与凭据不匹配")

        return ReleaseClaims(**claims)

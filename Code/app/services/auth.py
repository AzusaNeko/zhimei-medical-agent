"""
认证：密码哈希与 JWT 签发/校验。

═══ 为什么密码用 hashlib.scrypt 而不是 bcrypt ═══

bcrypt / argon2 都要装带 C 扩展的第三方包。而 `hashlib.scrypt` 是**标准库**里的，
由 OpenSSL 实现，本身就是为抗暴力破解设计的（内存硬 + CPU 硬），
参数可控且默认强度足够。少一个 C 依赖，部署时少一类"在别人机器上装不上"的问题。

参数取 RFC 7914 推荐的档位（n=2^14, r=8, p=1），实测单次约 50–100ms ——
这个量级是**故意的**：登录变慢一点无所谓，暴力破解慢一万倍才关键。
将来要提速就调这三个参数，**哈希串里已经带了参数**，老密码仍然能验，
不需要强制所有人改密码（升级路径见 verify_password）。

═══ JWT 的几条硬规则 ═══

  1. `algorithms=["HS256"]` **必须显式传**。不传的话，攻击者可以把头部改成
     `alg: none` 或换成非对称算法来绕过签名 —— 这是 JWT 最经典的漏洞。
  2. `exp` 一定要有，且由服务端签发时写入，不信任客户端。
  3. 密钥必须 ≥32 字节（HS256 的摘要长度）。短密钥会被 PyJWT 警告，
     也确实更容易被离线爆破。
  4. **不要往 payload 里塞敏感信息**。JWT 只是签名、不是加密 ——
     任何人 base64 解开就能读。这里只放 user_id / role / 姓名。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from ..settings import Settings

# ── 密码哈希参数（scrypt）──
#: n=16384, r=8, p=1 —— RFC 7914 建议的交互式登录档位
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

#: 哈希串前缀，便于将来换算法时按前缀分派
_ALGO = "scrypt"


class AuthError(Exception):
    """认证失败。带一个机器可读的 code，路由层直接映射成 HTTP 错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Principal:
    """已认证的主体（C 端用户或坐席）。"""

    user_id: str
    role: str            # customer | service | doctor | compliance | admin
    name: str = ""

    @property
    def is_staff(self) -> bool:
        return self.role != "customer"


# ══════════════════════════════════════════════════════════════
#  密码
# ══════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """返回 `scrypt$n$r$p$salt$hash`（全部 base64url，无填充）。

    ★ 参数写进哈希串里，不是硬编码在验证函数里 ——
      否则将来调参数时，所有老密码会**全部验不过**。
    """
    if not password:
        raise AuthError("empty_password", "密码不能为空")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    b = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")  # noqa: E731
    return f"{_ALGO}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b(salt)}${b(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验密码。**任何异常都返回 False** —— 认证失败不该泄漏失败细节。"""
    if not password or not stored:
        return False
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        unpad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        salt, expected = unpad(salt_b64), unpad(hash_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n),
                            r=int(r), p=int(p), dklen=len(expected))
        # ★ 必须用 compare_digest：普通的 == 会在第一个不同的字节处提前返回，
        #   攻击者能靠响应时间差逐字节猜出哈希（时序攻击）。
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


def new_verify_token() -> str:
    """邮箱验证令牌（占位实现用）。用 secrets 而不是 random —— random 可预测。"""
    return secrets.token_urlsafe(32)


# ══════════════════════════════════════════════════════════════
#  JWT
# ══════════════════════════════════════════════════════════════

_ALG = "HS256"


def issue_token(settings: Settings, principal: Principal) -> tuple[str, int]:
    """签发访问令牌。返回 (token, 有效期秒数)。

    payload 里只放**非敏感**的标识：JWT 是签名不是加密，谁都能解开看。
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=max(1, settings.jwt_ttl_hours))
    payload: dict[str, Any] = {
        "sub": principal.user_id,
        "role": principal.role,
        "name": principal.name,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "zhimei",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_ALG)
    return token, int((exp - now).total_seconds())


def decode_token(settings: Settings, token: str | None) -> Principal:
    """校验并解出主体。任何问题都抛 AuthError（带可区分的 code）。"""
    if not token:
        raise AuthError("missing_token", "缺少访问令牌")
    try:
        payload = jwt.decode(
            token, settings.jwt_secret,
            # ★ 显式白名单是必须的：不写的话攻击者可以伪造 alg 绕过签名
            algorithms=[_ALG],
            issuer="zhimei",
            options={"require": ["exp", "sub"]},   # 缺 exp / sub 直接拒绝
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token_expired", "登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        # 签名不对 / 结构不对 / iss 不对 / 缺必需字段，全部归到这里。
        # 对外不区分具体原因 —— 那只会帮攻击者定位问题。
        raise AuthError("invalid_token", "访问令牌无效") from exc
    return Principal(user_id=str(payload.get("sub") or ""),
                     role=str(payload.get("role") or "customer"),
                     name=str(payload.get("name") or ""))


def bearer_token(authorization: str | None) -> str | None:
    """从 `Authorization: Bearer xxx` 里取出令牌。"""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None

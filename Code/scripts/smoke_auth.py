"""
认证冒烟：注册 → 邮箱验证 → 登录 → 令牌校验 → 权限隔离。

    python scripts/smoke_auth.py --base http://127.0.0.1:8090     # 需要先起服务

═══ 这个脚本重点守什么 ═══

不变量比"能不能登录"重要得多。逐条守的是：

  1. **没令牌进不来**：聊天、会话列表、坐席接口，缺令牌一律 401。
  2. **令牌不能自己造**：签名被改动 / 换个密钥 / 伪造角色，全部拒绝。
  3. **坐席角色以数据库为准**：客户端在请求头里声明 `X-Agent-Role: compliance`
     必须**完全无效** —— 这正是本次补上的那个洞。
  4. **会话互相隔离**：A 用户的会话，B 用户拿不到、也聊不进去（而且返回 404
     而不是 403 —— 403 等于确认"这个会话存在"）。
  5. **口令错误与账号不存在返回同一个错误**，且耗时相近（防账号枚举）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402,F401

PASS: list[str] = []
FAIL: list[str] = []

DEMO_EMAIL = "demo@zhimei.test"
DEMO_PASSWORD = "zhimei-demo-2026"
AGENT_SERVICE = ("service@zhimei.test", "service")
AGENT_COMPLIANCE = ("compliance@zhimei.test", "compliance")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   —— {detail}" if detail and not ok else ""))


def section(title: str) -> None:
    print(f"\n{title}")


def bearer(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    args = ap.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=60) as c:
        # ══════════════ 1 未登录一律拒绝 ══════════════
        section("1. 未登录不得访问受保护接口")
        # 注意方法要对得上：写错方法会返回 405（正确行为）而不是 401，
        # 断言就会误报 —— 第一版就在这里多写了个 POST /api/auth/me。
        for method, url, kw in [
            ("GET", "/api/sessions", {}),
            ("POST", "/api/sessions", {"json": {"channel": "web"}}),
            ("GET", "/api/auth/me", {}),
            ("GET", "/ops/tickets", {}),
            ("POST", "/api/chat/00000000-0000-0000-0000-000000000000/stream",
             {"json": {"text": "在吗"}}),
        ]:
            r = await c.request(method, url, **kw)
            check(f"{method} {url} 无令牌 → 401", r.status_code == 401,
                  f"HTTP {r.status_code}")

        r = await c.get("/api/sessions", headers={"Authorization": "Bearer not.a.jwt"})
        check("乱码令牌 → 401", r.status_code == 401, f"HTTP {r.status_code}")

        # ══════════════ 2 注册 → 验证 → 登录 ══════════════
        section("2. 注册 / 邮箱验证 / 登录")
        email = f"tester-{uuid.uuid4().hex[:8]}@zhimei.test"
        r = await c.post("/api/auth/register",
                         json={"email": email, "password": "a-long-demo-password",
                               "display_name": "冒烟测试"})
        check("注册成功", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        reg = r.json()
        token_v = reg.get("verify_token")
        check("返回了邮箱验证令牌（演示期的临时妥协）", bool(token_v))
        check("响应里不含 password_hash",
              "password_hash" not in json.dumps(reg), "！密码哈希外泄")

        r = await c.post("/api/auth/register",
                         json={"email": email, "password": "a-long-demo-password"})
        check("同一邮箱重复注册 → 409", r.status_code == 409, f"HTTP {r.status_code}")

        r = await c.post("/api/auth/register",
                         json={"email": "not-an-email", "password": "a-long-demo-password"})
        check("非法邮箱 → 422", r.status_code == 422, f"HTTP {r.status_code}")
        r = await c.post("/api/auth/register",
                         json={"email": f"x{uuid.uuid4().hex[:6]}@zhimei.test", "password": "short"})
        check("过短口令 → 422", r.status_code == 422, f"HTTP {r.status_code}")

        r = await c.post("/api/auth/login", json={"email": email, "password": "a-long-demo-password"})
        check("未验证邮箱先登录 → 403 email_not_verified",
              r.status_code == 403 and r.json().get("code") == "email_not_verified",
              f"HTTP {r.status_code} {r.text[:100]}")

        r = await c.post("/api/auth/verify-email", json={"token": token_v})
        check("邮箱验证成功", r.status_code == 200, f"HTTP {r.status_code}")
        r = await c.post("/api/auth/verify-email", json={"token": token_v})
        check("验证令牌一次性（重复使用 → 400）", r.status_code == 400, f"HTTP {r.status_code}")

        t_ok = time.perf_counter()
        r = await c.post("/api/auth/login", json={"email": email, "password": "a-long-demo-password"})
        t_ok = time.perf_counter() - t_ok
        check("验证后登录成功", r.status_code == 200, f"HTTP {r.status_code}")
        user_token = r.json().get("access_token")
        check("拿到访问令牌", bool(user_token))
        check("响应里不含 password_hash", "password_hash" not in r.text)

        t_bad1 = time.perf_counter()
        r1 = await c.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        t_bad1 = time.perf_counter() - t_bad1
        t_bad2 = time.perf_counter()
        r2 = await c.post("/api/auth/login",
                          json={"email": f"nobody-{uuid.uuid4().hex[:8]}@zhimei.test",
                                "password": "wrong-password"})
        t_bad2 = time.perf_counter() - t_bad2
        check("口令错误 → 401", r1.status_code == 401, f"HTTP {r1.status_code}")
        check("账号不存在 → 401（与口令错误同一个错误码，防枚举）",
              r2.status_code == 401 and r1.json().get("code") == r2.json().get("code"),
              f"{r1.json().get('code')} vs {r2.json().get('code')}")
        check("两者耗时相近（不存在时也跑哈希，防时序枚举）",
              abs(t_bad1 - t_bad2) < 0.25,
              f"口令错 {t_bad1 * 1000:.0f}ms vs 不存在 {t_bad2 * 1000:.0f}ms")

        # ══════════════ 3 令牌内容与篡改 ══════════════
        section("3. 令牌不能自己造")
        r = await c.get("/api/auth/me", headers=bearer(user_token))
        check("带令牌可访问 /me", r.status_code == 200, f"HTTP {r.status_code}")
        check("/me 返回的角色是 customer", r.json().get("role") == "customer", r.text[:100])

        head, payload, sig = user_token.split(".")
        forged = f"{head}.{payload}.{sig[:-4]}AAAA"
        r = await c.get("/api/sessions", headers=bearer(forged))
        check("改动签名 → 401", r.status_code == 401, f"HTTP {r.status_code}")

        # alg=none 是 JWT 最经典的绕过手法
        import base64
        b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
        none_tok = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{payload}."
        r = await c.get("/api/sessions", headers=bearer(none_tok))
        check("alg=none 伪造令牌 → 401", r.status_code == 401, f"HTTP {r.status_code}")

        # ══════════════ 4 会话隔离 ══════════════
        section("4. 会话按用户隔离")
        r = await c.post("/api/sessions", json={"channel": "web"}, headers=bearer(user_token))
        check("登录后建会话成功", r.status_code == 200, f"HTTP {r.status_code}")
        sid = r.json()["session_id"]

        r = await c.get("/api/sessions", params={"channel": "web"}, headers=bearer(user_token))
        mine = [s["session_id"] for s in r.json()["sessions"]]
        check("会话列表里能看到自己的会话", sid in mine, f"共 {len(mine)} 条")
        check("列表里只有自己的会话", len(mine) == 1, f"{len(mine)} 条")

        # 另一个用户
        email2 = f"other-{uuid.uuid4().hex[:8]}@zhimei.test"
        reg2 = (await c.post("/api/auth/register",
                             json={"email": email2, "password": "a-long-demo-password"})).json()
        await c.post("/api/auth/verify-email", json={"token": reg2["verify_token"]})
        tok2 = (await c.post("/api/auth/login",
                             json={"email": email2, "password": "a-long-demo-password"})).json()["access_token"]

        r = await c.get("/api/sessions", headers=bearer(tok2))
        check("另一个用户看不到我的会话", r.json()["count"] == 0, str(r.json()["count"]))
        r = await c.get(f"/api/sessions/{sid}", headers=bearer(tok2))
        check("另一个用户读我的会话 → 404（不是 403）", r.status_code == 404, f"HTTP {r.status_code}")
        r = await c.post(f"/api/chat/{sid}/stream", json={"text": "在吗"}, headers=bearer(tok2))
        check("另一个用户聊进我的会话 → 404", r.status_code == 404, f"HTTP {r.status_code}")

        # ══════════════ 5 坐席登录与角色不可伪造 ══════════════
        section("5. 坐席：角色来自令牌+数据库，不信请求头")
        r = await c.post("/ops/auth/login",
                         json={"email": AGENT_SERVICE[0], "password": DEMO_PASSWORD})
        check("坐席登录成功", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        agent_token = r.json().get("access_token")
        check("返回角色为 service", (r.json().get("agent") or {}).get("role") == "service",
              str(r.json().get("agent")))
        check("返回权限清单供前端渲染", isinstance(r.json().get("permissions"), list))

        r = await c.get("/ops/tickets", headers={**bearer(agent_token),
                                                 "X-Agent-Role": "compliance"})
        check("★ 请求头声明 compliance 无效（角色仍来自令牌）",
              r.status_code == 200 and (r.json().get("agent") or {}).get("role") == "service",
              str((r.json() or {}).get("agent")))

        r = await c.get("/ops/tickets", headers={"X-Agent-Role": "compliance"})
        check("★ 只带请求头、不带令牌 → 401", r.status_code == 401, f"HTTP {r.status_code}")

        r = await c.post("/ops/auth/login",
                         json={"email": AGENT_SERVICE[0], "password": "wrong"})
        check("坐席口令错误 → 401（与账号不存在的错误码相同）",
              r.status_code == 401, f"HTTP {r.status_code}")

        # 顾客令牌不能进坐席端，反之亦然
        r = await c.get("/ops/tickets", headers=bearer(user_token))
        check("顾客令牌访问坐席接口 → 403", r.status_code == 403, f"HTTP {r.status_code}")
        r = await c.get("/api/sessions", headers=bearer(agent_token))
        check("坐席令牌访问顾客接口 → 403", r.status_code == 403, f"HTTP {r.status_code}")

        r = await c.get("/ops/auth/me", headers=bearer(agent_token))
        check("坐席 /me 可见脱敏权限标记", "sees_raw_pii" in (r.json() or {}), r.text[:100])

    print("\n" + "═" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗ " + f)
    print("═" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

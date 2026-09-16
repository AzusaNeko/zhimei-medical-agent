"""
接口层冒烟验证（进程内跑 ASGI，不需要起服务、不需要 pytest）。

用法：python scripts/smoke_api.py

它验证的是**接口契约与守卫**，不是"模型答得好不好"：
  1. 健康检查与会话创建
  2. 科普链路：SSE 事件序列正确，正文只从 final 事件出去
  3. 预约链路：挂起 awaiting_confirmation → confirm 恢复 → final；哈希不符则不执行
  4. 紧急链路：final（已审模板）+ handoff（P0 工单）两个事件
  5. 守卫：同会话并发 409、人工接管 409、参数非法 422
  6. 会话查询：历史消息出站前已脱敏
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   —— {detail}" if detail and not ok else ""))


def section(title: str) -> None:
    print(f"\n{title}")


async def collect(client: httpx.AsyncClient, method: str, url: str,
                  **kw: Any) -> tuple[int, list[tuple[str, Any]], Any]:
    """收集一次 SSE 请求的所有事件；非 200 时返回错误体。"""
    events: list[tuple[str, Any]] = []
    async with client.stream(method, url, **kw) as resp:
        if resp.status_code != 200:
            raw = await resp.aread()
            try:
                return resp.status_code, [], json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status_code, [], raw.decode("utf-8", "ignore")
        name: str | None = None
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                events.append((name or "?", json.loads(line[len("data: "):])))
            elif line.startswith(":"):
                events.append(("ping", None))
    return 200, events, None


def names(events: list[tuple[str, Any]]) -> list[str]:
    return [n for n, _ in events]


def first(events: list[tuple[str, Any]], name: str) -> Any:
    for n, d in events:
        if n == name:
            return d
    return None


async def main() -> int:
    settings = Settings()
    object.__setattr__(settings, "profile", "fake")     # 不连任何外部依赖
    app = create_app(settings)

    # 手动跑 lifespan：ASGITransport 不会自动触发 startup/shutdown
    async with app.router.lifespan_context(app):
        rt = app.state.runtime
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     timeout=30) as client:

            # ══════════════ 0 先登录（接口现在要求认证）══════════════
            section("0. 认证前置：走一遍注册 → 验证 → 登录")
            # ★ 这里**刻意走完整的真实流程**，而不是直接往 fake 存储里塞一个用户：
            #   直接塞的话，注册/验证/登录这三条链路在本套件里就完全没有覆盖，
            #   而它们恰恰是最该被回归盯住的部分。
            email = f"smoke-api-{uuid.uuid4().hex[:8]}@zhimei.test"
            pw = "smoke-api-password"
            r = await client.post("/api/auth/register",
                                  json={"email": email, "password": pw, "display_name": "接口冒烟"})
            check("注册 → 200 且返回验证令牌",
                  r.status_code == 200 and r.json().get("verify_token"),
                  f"{r.status_code} {r.text[:120]}")
            r = await client.post("/api/auth/verify-email",
                                  json={"token": r.json()["verify_token"]})
            check("邮箱验证 → 200", r.status_code == 200, f"{r.status_code}")
            r = await client.post("/api/auth/login", json={"email": email, "password": pw})
            check("登录 → 200 且拿到令牌", r.status_code == 200 and r.json().get("access_token"),
                  f"{r.status_code} {r.text[:120]}")
            # ★ 把令牌设成客户端的默认头，后面所有请求自动带上 ——
            #   比逐条改调用点可靠得多（漏掉一处就是一堆莫名其妙的 401）。
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

            # ══════════════ 1 健康检查与会话 ══════════════
            section("1. 健康检查与会话")
            r = await client.get("/api/health")
            check("GET /api/health → 200", r.status_code == 200, r.text[:120])
            check("档位与 checkpointer 正确",
                  r.json().get("profile") == "fake" and r.json().get("checkpointer") == "memory",
                  json.dumps(r.json(), ensure_ascii=False))

            r = await client.post("/api/sessions", json={"channel": "web"})
            check("POST /api/sessions → 200", r.status_code == 200, r.text[:120])
            sid = r.json().get("session_id")
            check("返回 session_id 与 thread_id",
                  bool(sid) and r.json().get("thread_id") == sid)

            # ══════════════ 2 科普链路 ══════════════
            section("2. 科普链路：过程流式 + 正文整段发送")
            code, events, _ = await collect(client, "POST", f"/api/chat/{sid}/stream",
                                            json={"text": "热玛吉和超声炮有什么区别"})
            got = names(events)
            check("SSE 返回 200", code == 200)
            check("收到过程事件 status", "status" in got, str(got))
            check("收到已审正文事件 final", got.count("final") == 1, str(got))
            check("final 携带放行凭据", bool(first(events, "final").get("token")))
            check("final 是最后一个业务事件（done 之前）",
                  got.index("final") < got.index("done"), str(got))
            check("没有 awaiting_confirmation（纯回复类不该挂起）",
                  "awaiting_confirmation" not in got, str(got))
            stage_texts = [d.get("text") for n, d in events if n == "status"]
            check("过程事件只发固定文案（不含模型输出）",
                  all(t and len(t) < 30 for t in stage_texts), str(stage_texts[:4]))

            # ══════════════ 3 预约链路（挂起 → 恢复）══════════════
            section("3. 预约链路：挂起 → 确认 → 执行 → 结果复审")
            sid2 = (await client.post("/api/sessions", json={})).json()["session_id"]
            code, events, _ = await collect(client, "POST", f"/api/chat/{sid2}/stream",
                                            json={"text": "帮我把热玛吉的预约改到浦东店周五下午"})
            got = names(events)
            check("操作类请求挂起", "awaiting_confirmation" in got, str(got))
            check("挂起时不发 final（未确认不能执行）", "final" not in got, str(got))
            payload = first(events, "awaiting_confirmation") or {}
            check("挂起事件带回方案哈希与过期时间",
                  bool(payload.get("plan_hash")) and bool(payload.get("expires_at")))

            code, events, _ = await collect(client, "POST", f"/api/chat/{sid2}/confirm",
                                            json={"confirmed": True,
                                                  "plan_hash": payload.get("plan_hash")})
            got = names(events)
            check("确认后执行并返回 final", "final" in got, str(got))
            check("执行结果文案含已审凭据", bool((first(events, "final") or {}).get("token")))

            # 方案哈希不符 → 作废，不执行
            sid3 = (await client.post("/api/sessions", json={})).json()["session_id"]
            _, events, _ = await collect(client, "POST", f"/api/chat/{sid3}/stream",
                                         json={"text": "帮我把热玛吉的预约改到浦东店周五下午"})
            _, events, _ = await collect(client, "POST", f"/api/chat/{sid3}/confirm",
                                         json={"confirmed": True, "plan_hash": "tampered"})
            text = (first(events, "final") or {}).get("text", "")
            check("方案哈希不符时作废方案、不执行",
                  "未做任何改动" in text, text[:120])

            # ══════════════ 4 紧急链路 ══════════════
            section("4. 紧急链路：已审模板 + 并行转人工")
            sid4 = (await client.post("/api/sessions", json={})).json()["session_id"]
            _, events, _ = await collect(
                client, "POST", f"/api/chat/{sid4}/stream",
                json={"text": "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清"})
            got = names(events)
            check("同时收到 final 与 handoff 两个事件",
                  "final" in got and "handoff" in got, str(got))
            check("紧急提示含急诊指引", "急诊" in (first(events, "final") or {}).get("text", ""))
            check("工单优先级为 P0",
                  (first(events, "handoff") or {}).get("priority") == "P0",
                  str(first(events, "handoff")))

            # ══════════════ 5 守卫 ══════════════
            section("5. 守卫：并发 / 人工接管 / 参数校验")
            sid5 = (await client.post("/api/sessions", json={})).json()["session_id"]
            await rt.try_begin(sid5)          # 模拟"该会话正在跑一个 run"
            r = await client.post(f"/api/chat/{sid5}/stream", json={"text": "在吗"})
            check("同会话并发 → 409 busy",
                  r.status_code == 409 and r.json().get("code") == "busy",
                  f"{r.status_code} {r.text[:80]}")
            await rt.end(sid5)

            sid6 = (await client.post("/api/sessions", json={})).json()["session_id"]
            session = await rt.deps.pg.get_session(sid6)
            session["ai_enabled"] = False     # 模拟坐席已接管
            r = await client.post(f"/api/chat/{sid6}/stream", json={"text": "补充一句"})
            # ★ 接管期间**收下**用户的话（200），但不产生 AI 正文。
            #   断言的是"没有 final"，而不是状态码 —— 见 _takeover_source 的说明。
            check("人工接管后用户仍可发言 → 200",
                  r.status_code == 200, f"{r.status_code} {r.text[:80]}")
            check("人工接管后回 human_takeover 事件且没有 final",
                  "human_takeover" in r.text and "event: final" not in r.text,
                  r.text[:160])
            check("接管期间用户的话仍然落库（转给坐席）",
                  any("补充一句" in (m.get("content") or "")
                      for m in await rt.deps.pg.recent_turns(sid6, limit=10)),
                  "消息没落库 —— 坐席将看不到顾客补充的内容")

            r = await client.post(f"/api/chat/{sid}/stream", json={"text": ""})
            check("空文本 → 422 invalid_request",
                  r.status_code == 422 and r.json().get("code") == "invalid_request",
                  f"{r.status_code} {r.text[:80]}")

            # ══════════════ 6 会话查询与脱敏 ══════════════
            section("6. 会话查询：历史消息已脱敏")
            r = await client.get(f"/api/sessions/{sid}")
            check("GET /api/sessions/{sid} → 200", r.status_code == 200)
            msgs = r.json().get("messages") or []
            check("能看到用户消息与已审回复",
                  any(m["role"] == "user" for m in msgs) and any(m["role"] == "assistant" for m in msgs),
                  json.dumps(msgs, ensure_ascii=False)[:160])
            check("回复带 review_kind 便于审计追溯",
                  all(m.get("review_kind") for m in msgs if m["role"] == "assistant"),
                  json.dumps(msgs, ensure_ascii=False)[:160])

            # ══════════════ 7 会话列表（新对话 / 切换对话）══════════════
            section("7. 会话列表：新建与切换的数据基础")
            r = await client.get("/api/sessions", params={"channel": "web", "limit": 50})
            check("GET /api/sessions → 200 且返回 sessions 数组",
                  r.status_code == 200 and isinstance(r.json().get("sessions"), list),
                  f"{r.status_code} {r.text[:100]}")
            lst = r.json()["sessions"]
            mine = next((s for s in lst if s["session_id"] == sid), None)
            check("刚聊过的会话出现在列表里", mine is not None, f"共 {len(lst)} 条")
            check("标题取自第一条用户消息（不用手动命名）",
                  mine is not None and (mine.get("title") or "").strip() != "",
                  str((mine or {}).get("title")))
            check("带消息条数（前端显示用）",
                  mine is not None and mine.get("msg_count", 0) >= 2,
                  str((mine or {}).get("msg_count")))
            check("带 last_active_at（排序依据）",
                  mine is not None and bool(mine.get("last_active_at")),
                  str((mine or {}).get("last_active_at")))
            check("带 ai_enabled（前端据此禁用输入）",
                  mine is not None and "ai_enabled" in mine, str(mine))

            r = await client.get("/api/sessions", params={"channel": "nosuch"})
            check("channel 过滤生效（别的渠道不会混进来）",
                  r.json()["count"] == 0, str(r.json()["count"]))

            r = await client.get("/api/sessions", params={"limit": 1})
            check("limit 生效（会话表只增不减，必须有上限）",
                  len(r.json()["sessions"]) <= 1, str(len(r.json()["sessions"])))

    # ══════════════ 汇总 ══════════════
    print("\n" + "═" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  ✗ " + f)
    print("═" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

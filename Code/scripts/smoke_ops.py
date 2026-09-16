"""
运营后台冒烟验证（进程内跑 ASGI，不需要起服务、不需要 pytest）。

用法：python scripts/smoke_ops.py

重点验证的是**不变量与跨模块联动**，不是界面好不好看：
  1. 转人工 → 工单进队列，优先级正确
  2. 未接单时不得告知用户"人工已接入"；未接单不能回复
  3. 接单 → 关掉 AI → 聊天接口立刻 409（跨模块联动）
  4. 坐席回复：禁发词硬拦、代执行操作拦、提示类规则先确认
  5. 关单 → 恢复 AI；状态机非法转移被拒
  6. 误报标记落库；脱敏按角色生效；权限按角色生效
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

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


#: 各角色的坐席令牌（登录后填充）。★ 角色现在是**服务端根据账号给的**，
#: 不再是客户端在请求头里声明的 —— 这个字典就是那个变化的落点。
_AGENT_TOKENS: dict[str, str] = {}
_SMOKE_PW = "smoke-ops-password"

#: 测试脚本统一用这个渠道建会话（见 smoke_api_live.py 里的详细说明）。
#: ★ 本脚本**每跑一次就造一张紧急工单**，所以这里尤其重要：
#:   用 web 渠道建会话的话，测试工单和真实顾客的工单无法区分，
#:   会污染队列与 SLA 指标（坐席打开面板分不清哪张是眼前这位顾客的）。
#:   用 test 渠道后，工单自动带 is_test 标记、默认不进队列。
TEST_CHANNEL = "test"


def H(role: str = "service") -> dict:
    """按角色取坐席令牌。

    ★ 这里原来返回的是 `{"X-Agent-Role": role, "X-Agent-Id": ...}` ——
      也就是**测试脚本自己声明自己是合规岗**。现在不行了：
      角色由登录令牌 + 数据库决定，所以这里改成一堆登录后拿到的令牌。
      请求头里塞中文名的问题也随之消失（令牌是 ASCII 的）。
    """
    tok = _AGENT_TOKENS.get(role)
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def login_agents(client: httpx.AsyncClient, rt) -> None:
    """给四个角色各造一个坐席账号并登录（fake 存储是内存的，得现造）。"""
    from app.services import auth

    for role in ("service", "doctor", "compliance", "admin"):
        email = f"{role}@smoke.test"
        await rt.deps.pg.upsert_agent(agent_id=f"t-{role}", name=f"测试{role}", role=role,
                                      email=email,
                                      password_hash=auth.hash_password(_SMOKE_PW))
        r = await client.post("/ops/auth/login", json={"email": email, "password": _SMOKE_PW})
        if r.status_code == 200:
            _AGENT_TOKENS[role] = r.json()["access_token"]


async def login_customer(client: httpx.AsyncClient) -> None:
    """造一个顾客账号并登录，把令牌设成客户端默认头（后续 /api 调用自动带上）。"""
    email = f"smoke-ops-{uuid.uuid4().hex[:8]}@zhimei.test"
    reg = (await client.post("/api/auth/register",
                             json={"email": email, "password": _SMOKE_PW,
                                   "display_name": "工单顾客"})).json()
    await client.post("/api/auth/verify-email", json={"token": reg["verify_token"]})
    r = await client.post("/api/auth/login", json={"email": email, "password": _SMOKE_PW})
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def parse_sse_text(body: str) -> dict[str, dict]:
    """把一次性 POST 回来的 SSE 正文解析成 {事件名: 数据}。

    同名事件后者覆盖前者 —— 这里只关心"出现了哪些事件、最后那个长什么样"。
    """
    out: dict[str, dict] = {}
    name = "?"
    for line in body.splitlines():
        if line.startswith("event: "):
            name = line[len("event: "):].strip()
        elif line.startswith("data: "):
            try:
                out[name] = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                pass
    return out


async def collect_sse(client: httpx.AsyncClient, url: str, headers: dict,
                      limit: int = 3) -> list[tuple[str, dict]]:
    """读取 SSE 事件。队列流用 ?once=true 只推一帧，避免无限流把测试挂住。"""
    events: list[tuple[str, dict]] = []
    async with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 200:
            return events
        name = "?"
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):])))
                if len(events) >= limit:
                    break
    return events


async def main() -> int:
    settings = Settings()
    object.__setattr__(settings, "profile", "fake")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        rt = app.state.runtime
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     timeout=30) as client:

            # ══════════════ 准备：认证 + 制造一张紧急工单 ══════════════
            section("0. 准备：登录（顾客 + 四个角色坐席），并让 AI 转人工")
            await login_agents(client, rt)
            await login_customer(client)
            check("四个角色坐席都能登录",
                  len(_AGENT_TOKENS) == 4, str(sorted(_AGENT_TOKENS)))
            sid = (await client.post("/api/sessions", json={"channel": TEST_CHANNEL})).json()["session_id"]
            async with client.stream("POST", f"/api/chat/{sid}/stream",
                                     json={"text": "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清"}) as r:
                async for _ in r.aiter_lines():
                    pass

            # ★ 默认队列必须**看不到**这张工单：它是 test 渠道造的，带 is_test 标记。
            #   这条断言和下面那条是一对 —— 只测"勾选后能看到"，过滤失效了也发现不了。
            r = await client.get("/ops/tickets", headers=H())
            check("GET /ops/tickets → 200", r.status_code == 200, r.text[:120])
            default_rows = r.json().get("tickets") or []
            check("测试工单默认不进队列", not any(t["session_id"] == sid for t in default_rows),
                  f"默认队列 {len(default_rows)} 张")

            # 本脚本要看的是自己刚造的那张，所以显式勾选"含测试工单"
            r = await client.get("/ops/tickets", params={"include_test": "true"}, headers=H())
            check("include_test=true → 200", r.status_code == 200, r.text[:120])
            tickets = r.json().get("tickets") or []
            check("工单已进队列", len(tickets) >= 1, json.dumps(tickets, ensure_ascii=False)[:160])
            ticket = next((t for t in tickets if t["session_id"] == sid), None)
            check("勾选后能看到自己那张测试工单", ticket is not None)
            check("队列行带 is_test 标记", bool(ticket and ticket["is_test"]))
            ticket = ticket or tickets[0]
            tid = ticket["ticket_id"]
            check("优先级为 P0", ticket["priority"] == "P0", str(ticket["priority"]))
            check("队列行带等待时长与上下文",
                  "wait_seconds" in ticket and bool(ticket.get("context")))

            # ══════════════ 1 未接单的不变量 ══════════════
            section("1. 未接单：不得告知用户已接入 / 不得回复")
            r = await client.get(f"/ops/tickets/{tid}", headers=H())
            detail = r.json()
            check("详情里 human_joined = false", detail["ticket"]["human_joined"] is False,
                  json.dumps(detail["ticket"], ensure_ascii=False)[:160])
            check("未接单时 accepted_at 为空", not detail["ticket"]["accepted_at"])
            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "您好，我来看一下"})
            check("未接单就回复 → 409 not_accepted",
                  r.status_code == 409 and r.json().get("code") == "not_accepted",
                  f"{r.status_code} {r.text[:100]}")

            # ══════════════ 2 接单 → 关掉 AI ══════════════
            section("2. 接单：写入 accepted_at 并关掉 AI（跨模块联动）")
            r = await client.post(f"/ops/tickets/{tid}/accept", headers=H())
            check("接单 → 200", r.status_code == 200, r.text[:120])
            check("返回 ai_enabled=false", r.json().get("ai_enabled") is False)
            r2 = await client.post(f"/ops/tickets/{tid}/accept", headers=H())
            check("重复接单 → 409 already_accepted",
                  r2.status_code == 409 and r2.json().get("code") == "already_accepted",
                  f"{r2.status_code} {r2.text[:100]}")

            # ★ 接管期间用户**仍然可以说话**：200 + human_takeover 事件，
            #   消息落库转给坐席，但**没有 final**（AI 不抢话）。
            #   早期版本这里断言 409 —— 那是把顾客挡在门外：他刚被告知
            #   "已为您转接人工客服"，下一句就发不出去，而想补充的情况
            #   （"我疼得更厉害了"）谁也收不到。
            r = await client.post(f"/api/chat/{sid}/stream", json={"text": "补充：现在还有点发烧"})
            check("接单后用户仍可发言 → 200（不再 409 挡人）",
                  r.status_code == 200, f"{r.status_code} {r.text[:100]}")
            ev = parse_sse_text(r.text)
            check("回的是 human_takeover 事件（明确告知 AI 已暂停）",
                  "human_takeover" in ev, str(list(ev)))
            check("接管期间**没有** final（AI 不抢话）", "final" not in ev, str(list(ev)))
            check("事件里 accepted=true（坐席确实接了单）",
                  (ev.get("human_takeover") or {}).get("accepted") is True,
                  str(ev.get("human_takeover")))
            # 不给 headers：login_customer 已经把顾客令牌设成默认头
            r = await client.get(f"/api/sessions/{sid}")
            msgs = (r.json().get("messages") or [])
            check("用户补充的话已落库（坐席看得到）",
                  any("发烧" in (m.get("content") or "") for m in msgs),
                  str([m.get("content", "")[:20] for m in msgs]))

            # ══════════════ 3 坐席回复的规则层校验 ══════════════
            section("3. 坐席回复：硬拦 / 代执行 / 软提示")
            # 硬性阻断项（隐私泄露）必须硬拦
            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "用户身份证 110101199003078515，我已核对"})
            check("命中硬性阻断（隐私泄露）→ 403 blocked_content",
                  r.status_code == 403 and r.json().get("code") == "blocked_content",
                  f"{r.status_code} {r.text[:120]}")

            # 疗效承诺按设计是 revise 级（改文案能解决）→ 软提示 + 需确认，不是硬拦
            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "我们保证根治，您放心"})
            check("疗效承诺（revise 级）→ 409 soft_warning",
                  r.status_code == 409 and r.json().get("code") == "soft_warning",
                  f"{r.status_code} {r.text[:120]}")

            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "已为您取消该笔预约，费用稍后到账"})
            check("坐席代执行操作 → 403 no_agent_operation",
                  r.status_code == 403 and r.json().get("code") == "no_agent_operation",
                  f"{r.status_code} {r.text[:120]}")

            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "这个项目是最好的选择"})
            check("提示类规则 → 409 soft_warning（先征求确认）",
                  r.status_code == 409 and r.json().get("code") == "soft_warning",
                  f"{r.status_code} {r.text[:120]}")
            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "这个项目是最好的选择",
                                        "acknowledge_warnings": True})
            check("坐席确认后可发送", r.status_code == 200, f"{r.status_code} {r.text[:100]}")

            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H(),
                                  json={"text": "已通知值班医师，请您保持电话畅通。"})
            check("正常回复 → 200", r.status_code == 200, r.text[:120])
            msgs = await rt.deps.pg.list_messages(sid, limit=50)
            check("坐席回复落库为 role=agent",
                  any(m["role"] == "agent" for m in msgs))

            # ══════════════ 4 脱敏与权限 ══════════════
            section("4. 脱敏按角色生效 / 权限按角色生效")
            await rt.deps.pg.save_agent_message(session_id=sid, agent_id="x",
                                                content="用户手机号 13800138000 已核实，邮箱 demo@zhimei.test")
            r = await client.get(f"/ops/tickets/{tid}", headers=H("service"))
            joined = " ".join(m["content"] for m in r.json()["messages"])
            check("service 角色看到的是脱敏后的手机号",
                  "13800138000" not in joined and "138****8000" in joined, joined[-80:])
            # ★ 邮箱是后补的：原来 mask() 只处理手机号/身份证/银行卡，
            #   而注释里写的是"邮箱与手机号同一条规则" —— 注释与实现不一致，
            #   界面上 service 角色能看到完整邮箱。这类偏差只能靠断言盯住。
            check("service 角色看到的邮箱也已打码（局部名首字符 + 域名保留）",
                  "demo@zhimei.test" not in joined and "d***@zhimei.test" in joined,
                  joined[-120:])
            r = await client.get(f"/ops/tickets/{tid}", headers=H("doctor"))
            joined = " ".join(m["content"] for m in r.json()["messages"])
            check("doctor 角色可看到原文",
                  "13800138000" in joined and "demo@zhimei.test" in joined, joined[-80:])

            r = await client.post(f"/ops/tickets/{tid}/reply", headers=H("compliance"),
                                  json={"text": "合规角色不该能回复"})
            check("compliance 无 ticket:reply 权限 → 403 forbidden",
                  r.status_code == 403 and r.json().get("code") == "forbidden",
                  f"{r.status_code} {r.text[:100]}")

            # ══════════════ 5 状态机与关单恢复 AI ══════════════
            section("5. 状态机：非法转移被拒，关单恢复 AI")
            r = await client.post(f"/ops/tickets/{tid}/close", headers=H(), json={"reason": ""})
            check("关闭无原因 → 400 reason_required",
                  r.status_code == 400 and r.json().get("code") == "reason_required",
                  f"{r.status_code} {r.text[:100]}")

            r = await client.post(f"/ops/tickets/{tid}/misreport", headers=H(),
                                  json={"source": "emergency", "ref_id": "vision:看不清",
                                        "raw_message": "眼睛也有点看不清", "verdict": "false_positive",
                                        "note": "坐席判定为询问而非自述"})
            check("标记误报 → 200", r.status_code == 200, r.text[:120])

            r = await client.post(f"/ops/tickets/{tid}/close", headers=H(),
                                  json={"reason": "已电话沟通处理完成"})
            check("关闭工单 → 200", r.status_code == 200, r.text[:120])
            check("关单后 AI 恢复", r.json().get("ai_enabled") is True)

            # 关单 → AI 立即恢复可用：这一次要有**真正的 AI 出站**，
            # 而不只是"HTTP 200"。接管期间用户还能说话，所以"200"本身
            # 已经不能证明 AI 恢复了 —— 必须看有没有 final。
            r = await client.post(f"/api/chat/{sid}/stream", json={"text": "还有一个问题"})
            check("关单后 AI 恢复：200", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
            ev = parse_sse_text(r.text)
            check("关单后这一轮**真的由 AI 作答**（有 final，不再是接管回执）",
                  "final" in ev and "human_takeover" not in ev, str(list(ev)))

            r = await client.post(f"/ops/tickets/{tid}/accept", headers=H())
            check("已关闭工单再接单 → 409 ticket_closed",
                  r.status_code == 409 and r.json().get("code") == "ticket_closed",
                  f"{r.status_code} {r.text[:100]}")
            r = await client.post(f"/ops/tickets/{tid}/reopen", headers=H())
            check("重新打开 → 200", r.status_code == 200, r.text[:100])
            r = await client.post(f"/ops/tickets/{tid}/accept", headers=H())
            check("重开后若已接单过 → 409 already_accepted",
                  r.status_code == 409 and r.json().get("code") == "already_accepted",
                  f"{r.status_code} {r.text[:100]}")
            # reopen 会把 AI 再关掉：重开的工单同样不能让 AI 抢话。
            # ★ 注意断言的是"没有 final"，不是"409" —— AI 不抢话的含义是
            #   **不出 AI 正文**，用户的话仍然要收下并转给坐席。
            r = await client.post(f"/api/chat/{sid}/stream", json={"text": "还有问题"})
            check("重开工单后 AI 重新停用：返回接管回执",
                  r.status_code == 200 and "human_takeover" in parse_sse_text(r.text),
                  f"{r.status_code} {r.text[:100]}")
            check("重开后 AI 不再作答（没有 final）",
                  "final" not in parse_sse_text(r.text), r.text[:160])

            # ══════════════ 6 指标与实时流 ══════════════
            section("6. 指标看板与队列实时流")
            r = await client.get("/ops/metrics", headers=H())
            m = r.json()
            check("GET /ops/metrics → 200", r.status_code == 200, r.text[:120])
            check("指标含 SLA 基线与规则 Top",
                  "sla_baseline" in m and "hard_rule_top" in m, json.dumps(m, ensure_ascii=False)[:160])
            check("误报计数已记录", (m.get("misreport_pending") or 0) >= 1,
                  json.dumps(m, ensure_ascii=False)[:160])

            events = await collect_sse(client, "/ops/stream?once=true", H(), limit=2)
            check("GET /ops/stream 首帧是 snapshot",
                  bool(events) and events[0][0] == "snapshot", str(events[:1])[:120])
            check("snapshot 带队列与指标",
                  "tickets" in events[0][1] and "metrics" in events[0][1])

            r = await client.get("/ops/panel")
            check("GET /ops/panel 返回监控面板",
                  r.status_code == 200 and "坐席工作台" in r.text, str(r.status_code))

    print("\n" + "═" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  ✗ " + f)
    print("═" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

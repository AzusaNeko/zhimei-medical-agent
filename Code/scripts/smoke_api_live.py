"""
接口层「真实 HTTP」冒烟：对着**已经跑起来的服务**发真实请求。

    # 终端 A
    python -m app.api --host 127.0.0.1 --port 8090
    # 终端 B
    python scripts/smoke_api_live.py --base http://127.0.0.1:8090 --user <user_id>

═══ 为什么还需要这个脚本（smoke_api.py 不是已经验过了吗）═══

`smoke_api.py` 走的是 `httpx.ASGITransport` —— 请求在**同一个进程内**直接交给
ASGI 应用。它验不出任何与"真的监听端口、真的走网络"有关的问题，实测至少漏掉两类：

  1. **服务根本起不来**。Windows 上 uvicorn 把事件循环硬编码成 ProactorEventLoop，
     而 psycopg 的异步连接在 Proactor 上会直接拒绝 —— `python -m app.api` 启动即失败。
     进程内测试完全看不到这一层，因为它压根不经过 uvicorn 的启动流程。
  2. **SSE 在真实连接上的行为**：分帧、心跳、断流、缓冲。
     进程内测试拿到的是一个拼好的响应体，而真实网络下事件是逐帧到达的。

所以两个脚本是互补的，不是重复的：
  · smoke_api.py      验业务逻辑与状态码（快、无需外部依赖、进 CI）
  · smoke_api_live.py 验"真的能对外提供服务"（需要先起服务）

═══ 本脚本重点验的三件事 ═══

  1. SSE 契约 —— 过程可流式，**正文永远整段发送**。
     断言方式是硬的：所有 status 事件的文案必须落在 `STAGE_TEXT` 这个封闭集合里。
     只要将来有人把业务内容漏进 status 通道，这条就会红。
  2. 挂起 → 确认，跨**两个独立 HTTP 请求**完成，且方案哈希被篡改时绝不执行。
  3. 人工接管不变量（跨模块）：坐席接单后，聊天接口必须拒绝继续自动回复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

# 控制台 UTF-8 兜底（见 app/console.py）。输出被重定向时 Windows 会退回 GBK，
# 而本脚本要打印 ✓ ✗，不处理就会 UnicodeEncodeError 直接崩。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402,F401

from app.graph.progress import STAGE_TEXT  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

OPS_HEADERS = {"X-Agent-Id": "agent-001", "X-Agent-Role": "service"}


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   —— {detail}" if detail and not ok else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# ════════════════════════════════════════════════════════════════
#  SSE 读取
# ════════════════════════════════════════════════════════════════
async def sse(client: httpx.AsyncClient, method: str, url: str,
              body: dict) -> tuple[int, str, list[tuple[str, dict]]]:
    """发一个请求并收集 SSE 事件。返回 (状态码, content-type, 事件列表)。

    网络层出错时返回 (-1, "", [])，而不是抛出去把整个脚本打断 ——
    真实 HTTP 下连接可能被重置，一次抖动不应该让前面所有通过项的结果都拿不到。
    """
    events: list[tuple[str, dict]] = []
    try:
        async with client.stream(method, url, json=body) as resp:
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200:
                await resp.aread()
                return resp.status_code, ctype, events
            name = "?"
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: "):]
                elif line.startswith("data: "):
                    try:
                        events.append((name, json.loads(line[len("data: "):])))
                    except json.JSONDecodeError:
                        events.append((name, {"_raw": line}))
        return 200, ctype, events
    except Exception as exc:  # noqa: BLE001
        print(f"    !! SSE 请求失败 {method} {url}：{type(exc).__name__}: {exc}")
        return -1, "", events


async def sse_detail(client: httpx.AsyncClient, method: str, url: str,
                     body: dict) -> tuple[int, dict | None, list[tuple[str, dict]]]:
    """同 sse()，但非 200 时把 JSON 错误体也解析出来（用于断言 detail.code）。

    用流式方式发请求而不是 client.post()：该端点在 200 时返回的是
    text/event-stream，非流式调用会一直等整个响应体结束，行为不一致。
    """
    try:
        async with client.stream(method, url, json=body) as resp:
            if resp.status_code != 200:
                raw = await resp.aread()
                try:
                    payload = json.loads(raw.decode("utf-8", "ignore"))
                except Exception:  # noqa: BLE001
                    payload = {"_raw": raw.decode("utf-8", "ignore")[:200]}
                return resp.status_code, payload.get("detail", payload), []
        return 200, None, []
    except Exception as exc:  # noqa: BLE001
        print(f"    !! 请求失败 {method} {url}：{type(exc).__name__}: {exc}")
        return -1, None, []


async def safe(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response | None:
    """网络层出错时返回 None，而不是把整个脚本打断。"""
    try:
        return await client.request(method.upper(), url, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"    !! 请求失败 {method.upper()} {url}：{type(exc).__name__}: {exc}")
        return None


def names(events: list[tuple[str, dict]]) -> list[str]:
    return [n for n, _ in events]


def first(events: list[tuple[str, dict]], name: str) -> dict | None:
    return next((d for n, d in events if n == name), None)


def report_errors(events: list[tuple[str, dict]]) -> list[dict]:
    """把 error / handoff 事件的载荷打出来。

    ★ 这个不是为了好看：`_event_source` 会捕获异常并转成 error 事件，
      于是**服务端日志里干干净净**，什么堆栈都没有。不把载荷打出来的话，
      一旦偶发失败，你手上只有一行"失败了"，完全没有线索。
      （真发生过：一次 booking 偶发失败，因为没打载荷，只能靠猜。）
      handoff 同理 —— 转人工有好几种原因，不打出来就分不清是
      "风险真的高" 还是 "修订预算被耗尽了"。
    """
    errs = [d for n, d in events if n == "error"]
    for d in errs:
        print(f"    !! error 事件：{json.dumps(d, ensure_ascii=False)}")
    for d in (d for n, d in events if n == "handoff"):
        print(f"    !! handoff 转人工：reason={d.get('reason')} "
              f"priority={d.get('priority')} ticket={str(d.get('ticket_id'))[:8]}…")
    return errs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    ap.add_argument("--user", default=None,
                    help="绑定用户 id（见 seed_kb.py 输出）。不绑定则跳过操作类场景")
    args = ap.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=120) as client:
        # ── 0 能连上吗 ──
        try:
            health = (await client.get("/api/health")).json()
        except Exception as exc:  # noqa: BLE001
            print(f"连不上 {args.base}：{exc}")
            print("先起服务：python -m app.api --host 127.0.0.1 --port 8090")
            return 2

        # ══════════════ 1 健康检查与会话 ══════════════
        section("1. 真实 HTTP：健康检查与会话")
        check("GET /api/health 返回 ok", health.get("status") == "ok", str(health))
        check("档位与检查点后端正确",
              health.get("profile") == "real" and health.get("checkpointer") == "postgres",
              str(health))

        r = await client.post("/api/sessions", json={"channel": "live", "user_id": args.user})
        check("POST /api/sessions 建会话", r.status_code == 200, f"HTTP {r.status_code}")
        sid = (r.json() or {}).get("session_id")
        check("返回了 session_id", bool(sid))

        r = await client.get(f"/api/sessions/{sid}", params={"limit": 5})
        check("GET /api/sessions/{id} 可读", r.status_code == 200, f"HTTP {r.status_code}")

        # ══════════════ 2 SSE 契约：正文绝不流式 ══════════════
        section("2. SSE 契约：过程可流式，正文整段发送")
        code, ctype, ev = await sse(client, "POST", f"/api/chat/{sid}/stream",
                                    {"text": "热玛吉和超声炮有什么区别？"})
        check("流式请求返回 200", code == 200, f"HTTP {code}")
        check("Content-Type 是 text/event-stream", "text/event-stream" in ctype, ctype)
        check("有过程事件（status）", "status" in names(ev), str(names(ev)))
        check("恰好一个 final", names(ev).count("final") == 1, str(names(ev)))
        check("以 done 收尾", names(ev)[-1] == "done" if ev else False, str(names(ev)))

        fin = first(ev, "final") or {}
        check("final 携带放行凭据", bool(fin.get("token")))
        check("final 有已审正文", bool(str(fin.get("text") or "").strip()))

        # ★ 最硬的一条：过程通道只允许出现固定文案。
        #   业务内容一旦漏进 status（比如把草稿分片推给前端），这里立刻失败。
        allowed = set(STAGE_TEXT.values())
        bad = [d.get("text") for n, d in ev if n == "status" and d.get("text") not in allowed]
        check("所有 status 文案都来自固定话术表（正文没有混进过程通道）",
              not bad, str(bad[:3]))
        check("status 事件不含正文片段",
              all(str(fin.get("text", ""))[:30] not in str(d.get("text", ""))
                  for n, d in ev if n == "status"))

        # ★ 万一这一轮内部报错：不能只发 error 就结束，必须同时兜底转人工。
        #   否则用户问了一句话，收到的只有静默失败 —— 没有回答、没有工单、
        #   也没人说会跟进。这是对客系统里最糟的一种失败。
        errs = report_errors(ev)
        if errs:
            check("内部异常时必须同时兜底转人工（不能让用户对着空气说话）",
                  "handoff" in names(ev), str(names(ev)))

        # ══════════════ 3 挂起 → 确认（跨两个 HTTP 请求）══════════════
        section("3. 挂起 → 确认：跨两个独立 HTTP 请求")
        if not args.user:
            print("  （未提供 --user，跳过操作类场景 —— 不绑用户时会话 auth.verified=false，"
                  "操作类请求按设计会被判 need_info）")
        else:
            # 3.1 错误哈希不得执行
            s_t = (await client.post("/api/sessions",
                                     json={"channel": "live", "user_id": args.user})).json()["session_id"]
            _, _, ev1 = await sse(client, "POST", f"/api/chat/{s_t}/stream",
                                  {"text": "帮我把热玛吉的预约改到浦东店周五下午"})
            report_errors(ev1)
            pend = first(ev1, "awaiting_confirmation")
            check("操作类请求挂起等待确认", pend is not None, str(names(ev1)))
            if pend is None:
                # 这一节依赖"库里有一条 status='booked' 的热玛吉预约"。
                # 上一次跑这里真的执行过改约的话，那条记录就变成 'changed' 了，
                # 而改约只能作用于 booked —— 于是本轮会走"查不到可改约的预约"的
                # 诚实降级路径。这是正确行为，不是缺陷，但**必须把话说清楚**，
                # 否则看到的就是几个费解的失败，还以为代码坏了。
                print("    ↑ 提示：预约可能已被上一次运行消耗（改约成功后 status 变 changed）。")
                print("      先跑一次 python scripts/seed_kb.py 复位演示数据，再重跑本脚本。")
            check("挂起时带回方案哈希与过期时间",
                  bool((pend or {}).get("plan_hash")) and bool((pend or {}).get("expires_at")))
            check("挂起时【没有】提前输出业务正文（未确认不得出站）",
                  "final" not in names(ev1), str(names(ev1)))

            code_t, _, ev_t = await sse(client, "POST", f"/api/chat/{s_t}/confirm",
                                        {"confirmed": True, "plan_hash": "tampered"})
            check("篡改方案哈希后仍返回 200（流程走完，但不执行）", code_t == 200, f"HTTP {code_t}")
            executed = any(n == "status" and d.get("stage") == "execute" for n, d in ev_t)
            check("方案哈希不符时【绝不执行】", not executed, str(names(ev_t)))

            # 3.2 正确哈希 → 真的执行
            s_ok = (await client.post("/api/sessions",
                                      json={"channel": "live", "user_id": args.user})).json()["session_id"]
            _, _, ev2 = await sse(client, "POST", f"/api/chat/{s_ok}/stream",
                                  {"text": "帮我把热玛吉的预约改到浦东店周五下午"})
            report_errors(ev2)
            p2 = first(ev2, "awaiting_confirmation")
            if p2 is None:
                check("第二次也为操作类请求挂起", False, str(names(ev2)))
                print("    ↑ 提示：同上 —— 预约已被消耗，先跑 python scripts/seed_kb.py 复位。")
            else:
                _, _, ev3 = await sse(client, "POST", f"/api/chat/{s_ok}/confirm",
                                      {"confirmed": True, "plan_hash": p2.get("plan_hash")})
                report_errors(ev3)
                did = any(n == "status" and d.get("stage") == "execute" for n, d in ev3)
                check("确认后真的执行了操作", did, str(names(ev3)))
                rec = first(ev3, "final") or {}
                check("执行结果经过审查后出站（携带凭据）", bool(rec.get("token")))
                check("不是把执行结果未经审查直接返回", "receipt" in [d.get("stage")
                      for n, d in ev3 if n == "status"] or bool(rec.get("text")))

        # ══════════════ 4 人工接管不变量（跨模块）══════════════
        section("4. 人工接管：坐席接单后 AI 必须停止自动回复")
        s_em = (await client.post("/api/sessions", json={"channel": "live"})).json()["session_id"]
        _, _, ev_em = await sse(client, "POST", f"/api/chat/{s_em}/stream",
                                {"text": "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清"})
        ho = first(ev_em, "handoff")
        check("紧急信号触发转人工事件", ho is not None, str(names(ev_em)))
        check("工单优先级为 P0", (ho or {}).get("priority") == "P0", str(ho))
        check("同一次回复里也有已审模板提示（并行）", "final" in names(ev_em), str(names(ev_em)))

        # /ops/tickets 返回的是 {"agent": {...}, "tickets": [...]}，不是裸数组
        queue = (await client.get("/ops/tickets", headers=OPS_HEADERS)).json()
        tickets = queue.get("tickets") or []
        check("队列返回带坐席身份（便于审计谁在看）", bool((queue.get("agent") or {}).get("agent_id")),
              str(queue.get("agent")))
        mine = next((t for t in tickets if t.get("session_id") == s_em), None)
        check("运营后台能查到这张工单", mine is not None, f"共 {len(tickets)} 张")
        check("队列行能看出大概是什么事（context 非空）", bool((mine or {}).get("context")),
              str((mine or {}).get("context")))
        check("工单未接单时 accepted_at 为空（不得谎称已接入）",
              mine is not None and not mine.get("accepted_at"), str((mine or {}).get("accepted_at")))

        if mine:
            tid = mine["ticket_id"]
            # 这一整段都用 sse()/safe_* 包裹：一旦某一步网络层出错（真实 HTTP 下
            # 连接可能被重置），也只记录一项失败，不能把整个脚本打断 ——
            # 否则前面所有通过项的结果都拿不到。
            r = await safe(client, "post", f"/ops/tickets/{tid}/accept", headers=OPS_HEADERS)
            check("坐席接单成功（真实库下坐席 id 是 TEXT，不是 UUID）",
                  r is not None and r.status_code == 200,
                  f"HTTP {getattr(r, 'status_code', 'network-error')}")

            code_409, detail_409, _ = await sse_detail(
                client, "POST", f"/api/chat/{s_em}/stream", {"text": "在吗"})
            check("接单后聊天接口拒绝 AI 自动回复（409）", code_409 == 409, f"HTTP {code_409}")
            check("拒绝原因标明是人工接管",
                  (detail_409 or {}).get("code") == "human_takeover", str(detail_409))

            r = await safe(client, "post", f"/ops/tickets/{tid}/close", headers=OPS_HEADERS,
                           json={"reason": "已电话联系并给出急诊指引"})
            check("坐席关单成功", r is not None and r.status_code == 200,
                  f"HTTP {getattr(r, 'status_code', 'network-error')}")

            code_after, _, _ = await sse(client, "POST", f"/api/chat/{s_em}/stream",
                                         {"text": "热玛吉痛不痛"})
            check("关单后 AI 恢复自动回复（不再是 409）", code_after == 200, f"HTTP {code_after}")

        # ══════════════ 5 运营面板与推送 ══════════════
        section("5. 运营后台：监控面板与 SSE 推送")
        r = await client.get("/ops/panel")
        check("GET /ops/panel 返回页面", r.status_code == 200, f"HTTP {r.status_code}")
        check("面板内容像监控台", "工单" in r.text, r.text[:60])

        r = await client.get("/ops/stream", headers=OPS_HEADERS, params={"once": "true"})
        check("GET /ops/stream?once=true 可读", r.status_code == 200, f"HTTP {r.status_code}")
        check("推送首帧是 snapshot", "event: snapshot" in r.text, r.text[:80])

        r = await client.get("/ops/metrics", headers=OPS_HEADERS)
        check("GET /ops/metrics 可读", r.status_code == 200, f"HTTP {r.status_code}")

        # ══════════════ 6 角色权限（真实 HTTP 下的 403）══════════════
        section("6. 角色权限")
        r = await client.get("/ops/tickets", headers={"X-Agent-Id": "a", "X-Agent-Role": "nosuchrole"})
        check("未知角色返回 403", r.status_code == 403, f"HTTP {r.status_code}")

    print("\n" + "═" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗ " + f)
    print("═" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

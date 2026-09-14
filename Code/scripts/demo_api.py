"""
接口层演示：把真实的 SSE 事件流打出来（面试演示用）。

用法（先起服务）：
    python -m app.api --profile fake --port 8077        # 另开一个终端
    python scripts/demo_api.py --base http://127.0.0.1:8077

它会跑三个场景并把每个事件逐行打印：
  1. 科普咨询    → 过程事件 + 已审正文（final）
  2. 预约改约    → 挂起等待确认 → 调 /confirm 恢复 → 执行结果再审（final）
  3. 术后紧急    → 已审模板提示（final）+ 创建 P0 工单（handoff）两个事件并行

看这个脚本能直观理解一件事：**过程可以流式，正文永远整段发送**。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx


async def stream(client: httpx.AsyncClient, method: str, url: str, *, body: dict,
                 show_ping: bool = False) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    print(f"\n  → {method} {url}")
    async with client.stream(method, url, json=body) as resp:
        if resp.status_code != 200:
            raw = await resp.aread()
            print(f"  ✗ HTTP {resp.status_code}: {raw.decode('utf-8', 'ignore')[:200]}")
            return events
        name = "?"
        async for line in resp.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
                events.append((name, data))
                _print_event(name, data)
            elif line.startswith(":") and show_ping:
                print("  · (心跳)")
    return events


def _print_event(name: str, data: dict) -> None:
    icon = {"status": "·", "final": "✅", "handoff": "👤",
            "blocked": "⛔", "awaiting_confirmation": "⏸", "done": "■",
            "error": "✗"}.get(name, "?")
    if name == "status":
        print(f"  {icon} status  {data.get('stage')}: {data.get('text')}")
    elif name == "final":
        print(f"  {icon} final   凭据={(data.get('token') or '')[:16]}…")
        for line in str(data.get("text", "")).splitlines():
            print(f"        {line}")
    elif name == "awaiting_confirmation":
        print(f"  {icon} 等待确认  方案哈希={(data.get('plan_hash') or '')[:16]}…"
              f"  过期={data.get('expires_at')}")
        for line in str(data.get("plan", "")).splitlines():
            print(f"        {line}")
    elif name == "handoff":
        print(f"  {icon} 转人工   工单={str(data.get('ticket_id'))[:8]}…"
              f"  优先级={data.get('priority')}  原因={data.get('reason')}")
    elif name == "blocked":
        print(f"  {icon} 阻断     命中规则={data.get('rule_ids')}")
    elif name == "done":
        print(f"  {icon} done    会话={str(data.get('session_id'))[:8]}…")
    else:
        print(f"  {icon} {name}  {json.dumps(data, ensure_ascii=False)[:160]}")


SCENARIOS = [
    ("场景 1 · 科普咨询（过程流式 + 正文整段发送）", "热玛吉和超声炮有什么区别？", False),
    ("场景 2 · 预约改约（挂起 → 确认 → 执行 → 结果再审）",
     "帮我把热玛吉的预约改到浦东店周五下午", True),
    ("场景 3 · 术后紧急（模板提示 + 并行转人工）",
     "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清", False),
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8077")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        try:
            health = (await client.get("/api/health")).json()
        except Exception as exc:  # noqa: BLE001
            print(f"连不上 {args.base}：{exc}\n先执行：python -m app.api --profile fake --port 8077")
            return 2
        print(f"服务正常：{json.dumps(health, ensure_ascii=False)}")

        for title, text, needs_confirm in SCENARIOS:
            print("\n" + "═" * 68)
            print(f"▶ {title}\n  用户：{text}")
            print("═" * 68)
            sid = (await client.post("/api/sessions", json={"channel": "demo"})).json()["session_id"]
            events = await stream(client, "POST", f"/api/chat/{sid}/stream", body={"text": text})

            pending = next((d for n, d in events if n == "awaiting_confirmation"), None)
            if pending and needs_confirm:
                print("\n  （图已挂起，下面用第二个 HTTP 请求恢复它）")
                await stream(client, "POST", f"/api/chat/{sid}/confirm",
                             body={"confirmed": True, "plan_hash": pending.get("plan_hash")})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

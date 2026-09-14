"""
命令行演示入口（落地第 1 步：图 + CLI）。

用法：
  python -m app.cli --demo                     # 跑 4 个典型场景
  python -m app.cli --demo --profile fake      # 没有任何外部依赖，验证图接线
  python -m app.cli -t "热玛吉和超声炮有什么区别"   # 单轮对话
  python -m app.cli --interactive              # 交互式多轮

为什么保留 fake 档位：API key、Postgres、Milvus 任何一样没配好，图就跑不起来 ——
但"图的接线对不对"和"依赖装没装"是两件事，必须能分开验证。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Any

from langgraph.types import Command

from .runtime import Runtime, create_runtime
from .settings import Settings

# 控制台 UTF-8 兜底已在 app/__init__.py 里开启（导入本模块时必然先执行），
# 所以这里不需要再做一次 —— 即便 stdout 被重定向到管道/文件，中文也不会崩。

LINE = "─" * 72

#: 演示场景：覆盖科普（含修订环）、预约（含确认执行）、紧急、澄清预算
DEMOS: list[dict[str, Any]] = [
    {"title": "场景 1 · 售前科普咨询（会触发一次修订再通过）",
     "text": "热玛吉和超声炮有什么区别？",
     "auto_confirm": None},
    {"title": "场景 2 · 预约改约（需要用户确认后才执行，结果再送审）",
     "text": "帮我把热玛吉的预约改到浦东店周五下午",
     "auto_confirm": True},
    {"title": "场景 3 · 术后紧急信号（固定模板提示 + 并行人工作业）",
     "text": "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清",
     "auto_confirm": None},
    {"title": "场景 4 · 意图不明确（澄清追问）",
     "text": "那个怎么样",
     "auto_confirm": None},
]


# ════════════════════════════════════════════════════════════════
#  单轮执行
# ════════════════════════════════════════════════════════════════
async def run_turn(rt: Runtime, session_id: str, text: str, *,
                   user_id: str | None = None,
                   auto_confirm: bool | None = None, verbose: bool = True) -> dict:
    config = rt.config(session_id)
    state = {"session_id": session_id, "user_input": text, "channel": "cli",
             "user_id": user_id, "attachments": []}
    result = await rt.graph.ainvoke(state, config)

    # ── interrupt：等用户确认（接口层里这是另一个 HTTP 请求）──
    pending = result.get("__interrupt__")
    if pending:
        payload = pending[0].value
        if verbose:
            print(f"\n[暂停] 图已挂起，等待用户确认：\n{_indent(payload.get('plan', ''))}")
            print(f"   方案哈希：{(payload.get('plan_hash') or '')[:16]}…")
        confirmed = auto_confirm
        if confirmed is None:
            # ★ 非交互环境（CI、`< /dev/null`、被父进程捕获 stdin）下 input() 会抛
            #   EOFError。默认当成"用户没确认"处理并明确说明，而不是甩一屏 traceback
            #   ——因为"读不到输入"和"代码有 bug"对使用者是完全不同的两件事。
            try:
                answer = input("   是否确认执行？[y/N] ").strip().lower()
                confirmed = answer in ("y", "yes")
            except EOFError:
                confirmed = False
                if verbose:
                    print("   （无交互输入，按【未确认】处理：方案已作废，未执行任何操作）")
        result = await rt.graph.ainvoke(
            Command(resume={"confirmed": bool(confirmed),
                            "plan_hash": payload.get("plan_hash")}),
            config)
        result["__resumed__"] = True

    if verbose:
        _print_result(result)
    return result


def _print_result(state: dict) -> None:
    out = state.get("outbound") or {}
    print(f"\n   意图：{'、'.join(state.get('intents') or []) or '—'}"
          f"   路由：{'、'.join((state.get('plan') or {}).get('targets') or []) or '—'}")
    print(f"   草稿来源：{'、'.join(d.get('agent', '?') for d in (state.get('drafts') or [])) or '—'}")
    print(f"   审查：verdict={state.get('verdict')}  risk_level={state.get('risk_level')}"
          f"  复审轮次={state.get('review_round')}  修订次数={state.get('revision_count')}"
          f"  同族复核={state.get('escalation_independent') is False}"
          f"  澄清计数={state.get('clarify_count', 0)}")

    hits = state.get("hard_rule_hits") or []
    if hits:
        print("   硬规则命中：" + "、".join(f"{h['rule_id']}({h['action']})" for h in hits))
    if state.get("review_feedback"):
        print("   修改意见：" + "；".join(state["review_feedback"]))

    kind = out.get("kind")
    if kind == "reply":
        print(f"\n[OK] 已放行并发送（凭据 {(out.get('token') or '')[:16]}…）：")
        print(_indent(out.get("text", "")))
    elif kind == "blocked":
        print(f"\n[阻断] 硬性阻断，未产生任何业务出站内容。命中规则：{out.get('rule_ids')}")
    else:
        print("\n（本轮无业务出站内容）")

    # 转人工走单独字段（紧急场景会与 send 并行走，不能共用 outbound）
    if state.get("handoff_notice"):
        t = state.get("handoff_ticket") or {}
        print(f"\n[转人工] 已转人工（工单 {str(t.get('ticket_id', ''))[:8]}…  原因={t.get('reason')}"
              f"  优先级={t.get('priority')}）：")
        print(_indent(state["handoff_notice"]))

    events = [e.get("event") for e in (state.get("audit_log") or [])]
    print(f"\n   节点轨迹：{' → '.join(events)}")


def _indent(text: str, prefix: str = "     ") -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


# ════════════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════════════
async def _main(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.profile:
        object.__setattr__(settings, "profile", args.profile)

    print(f"档位：{settings.profile}"
          f"{'（内存 + 脚本化假模型，仅验证图接线）' if settings.is_fake else '（真实 Postgres / Milvus / DeepSeek）'}")
    if not settings.is_fake:
        settings.validate()

    rt = await create_runtime(settings)
    exit_code = 0
    try:
        if args.demo:
            for i, case in enumerate(DEMOS, 1):
                session = str(uuid.uuid4())
                print(f"\n{LINE}\n▶ {case['title']}\n  用户：{case['text']}"
                      f"\n  会话：{session}\n{LINE}")
                try:
                    await run_turn(rt, session, case["text"], user_id=args.user,
                                   auto_confirm=case.get("auto_confirm"))
                except Exception as exc:  # noqa: BLE001
                    exit_code = 1
                    print(f"\n[失败] 场景执行失败：{type(exc).__name__}: {exc}")
                    if args.verbose:
                        import traceback
                        traceback.print_exc()
            return exit_code

        if args.text:
            session = args.session or str(uuid.uuid4())
            # --yes 时不走 input()：脚本/CI 里没有可交互的 stdin，
            # 不加这个开关就只能等到 EOFError 再按"未确认"处理
            await run_turn(rt, session, args.text, user_id=args.user,
                           auto_confirm=True if args.yes else None)
            return 0

        if args.interactive:
            session = args.session or str(uuid.uuid4())
            print(f"会话 {session}（输入 exit 退出）")
            while True:
                try:
                    text = input("\n你：").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not text or text.lower() in ("exit", "quit"):
                    break
                await run_turn(rt, session, text, user_id=args.user)
            return 0

        print("请指定 --demo / -t 文本 / --interactive，或 -h 查看帮助。")
        return 2
    finally:
        await rt.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="zhimei", description="智美医美顾问 MVP · CLI 演示")
    parser.add_argument("--demo", action="store_true", help="跑内置的 4 个典型场景")
    parser.add_argument("-t", "--text", help="单轮文本")
    parser.add_argument("--interactive", action="store_true", help="交互式多轮")
    parser.add_argument("--session", help="指定会话 id（复用同一个 thread）")
    parser.add_argument("--user", help="绑定用户 id（UUID，见 seed_kb.py 输出）。"
                                      "真实档位下不绑定用户，操作类请求会被判 need_info")
    parser.add_argument("--profile", choices=["real", "fake"], help="覆盖 APP_PROFILE")
    parser.add_argument("-v", "--verbose", action="store_true", help="出错时打印堆栈")
    parser.add_argument("--yes", action="store_true",
                        help="需要确认的操作自动确认（脚本/CI 用；等价于演示场景里的自动确认）")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()

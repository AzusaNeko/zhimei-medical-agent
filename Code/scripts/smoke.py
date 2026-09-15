"""
独立冒烟脚本：不依赖 pytest、不依赖任何外部服务（fake 档位）。

用法：python scripts/smoke.py            （在 Code 目录下执行）

它验证的是【图的接线与不变量】，不是"模型答得好不好"：
  1. 科普链路能跑通，越界草稿被打回修订、修订后通过
  2. 操作类必须经用户确认；未确认/方案被改 → 绝不执行
  3. 紧急信号走固定模板 + 并行转人工
  4. 放行凭据绑定内容与方案，被改动即失效
  5. clarify 预算用尽后不再无限追问
  6. 紧急词表的四种抑制规则
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.graph.build import build_graph  # noqa: E402
from app.services import trace  # noqa: E402
from app.services.deps import Deps  # noqa: E402
from app.services.rules import RuleEngine  # noqa: E402
from app.settings import Settings  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}" + (f"   —— {detail}" if detail and not ok else ""))


def section(title: str) -> None:
    print(f"\n{title}")


async def turn(graph, deps, thread: str, text: str) -> dict:
    # 与应用入口（cli.run_turn / api._event_source）保持一致：把 thread_id 放进上下文，
    # 模型网关据此把每次调用归属到具体对话。不设的话 llm_call_log.thread_id 会是 NULL
    # —— 日志照样能记，但归不到哪一轮，成本报表就串不起来。
    with trace.turn_scope(thread):
        return await graph.ainvoke(
            {"session_id": thread, "user_input": text, "channel": "smoke", "attachments": []},
            {"configurable": {"thread_id": thread},
             "recursion_limit": deps.settings.recursion_limit})


async def resume(graph, deps, thread: str, payload: dict, confirmed: bool) -> dict:
    with trace.turn_scope(thread):
        return await graph.ainvoke(
            Command(resume={"confirmed": confirmed, "plan_hash": payload.get("plan_hash")}),
            {"configurable": {"thread_id": thread},
             "recursion_limit": deps.settings.recursion_limit})


async def main() -> int:
    settings = Settings()
    object.__setattr__(settings, "profile", "fake")      # 强制走内存 + 脚本化假模型
    deps = Deps.build(settings)
    await deps.startup()
    graph = build_graph(deps, checkpointer=InMemorySaver())

    # ══════════════ 1 科普链路 + 修订环 ══════════════
    section("1. 售前科普：草稿越界 → 退回修订 → 复审通过")
    s = await turn(graph, deps, "smoke-kb", "热玛吉和超声炮有什么区别")
    out = s.get("outbound") or {}
    check("审查结论为 pass", s.get("verdict") == "pass", str(s.get("verdict")))
    check("发生过至少一次修订", int(s.get("revision_count", 0)) >= 1,
          f"revision_count={s.get('revision_count')}")
    check("修订后重新送审（轮次 ≥ 2）", int(s.get("review_round", 0)) >= 2,
          f"review_round={s.get('review_round')}")
    check("第一轮硬规则命中被记录进 history",
          any(h.get("rule_id") == "AD-002" for h in (s.get("hard_rule_hits_history") or [])),
          str([h.get("rule_id") for h in (s.get("hard_rule_hits_history") or [])]))
    check("最终一轮结论字段已重置（不残留上一轮命中）",
          (s.get("hard_rule_hits") or []) == [],
          str(s.get("hard_rule_hits")))
    check("出站内容不含被拦下的绝对化用语", "全网最低" not in out.get("text", ""),
          out.get("text", "")[:60])
    check("出站携带放行凭据", bool(out.get("token")))
    check("审查结论已写审计", bool(deps.pg.audits))

    # ══════════════ 2 预约链路 ══════════════
    section("2. 预约改约：确认 → 执行 → 结果复审")
    t = "smoke-bk"
    s = await turn(graph, deps, t, "帮我把热玛吉的预约改到浦东店周五下午")
    pending = s.get("__interrupt__")
    check("操作类请求挂起等待确认", bool(pending))
    payload = pending[0].value if pending else {}
    check("挂起时带回方案哈希", bool(payload.get("plan_hash")))

    denied = await resume(graph, deps, t, payload, confirmed=False)
    check("用户否认时不执行", not denied.get("execution_result"))
    check("否认被记录为 denied", denied.get("confirm_result") == "denied",
          str(denied.get("confirm_result")))

    # ── 方案必须绑定"改哪一条预约 + 该记录的版本" ──
    # 这两个字段漏掉时的现象极具误导性：SQL 退化成 `appointment_id = NULL` 永不匹配，
    # 报出来的却是"状态已变化，请刷新后重试"，把排查方向完全带偏到并发冲突上。
    op_params = ((s.get("operation") or {}).get("params")) or {}
    check("方案里带上了要改的预约编号", bool(op_params.get("appointment_id")),
          str(op_params))
    check("方案里带上了乐观锁基准版本（不是写死的 1）",
          op_params.get("expected_version") == 1, str(op_params))

    t2 = "smoke-bk2"
    s2 = await turn(graph, deps, t2, "帮我把热玛吉的预约改到浦东店周五下午")
    p2 = s2["__interrupt__"][0].value
    done = await resume(graph, deps, t2, p2, confirmed=True)
    check("确认后执行成功", bool(done.get("execution_result")))
    check("记录幂等键", bool(done.get("idempotency_key")))
    kinds = [a.get("review_kind") for a in deps.pg.audits if a.get("review_kind")]
    check("执行结果回复再次送审", "result_reply" in kinds, str(sorted(set(map(str, kinds)))))
    check("结果回复携带凭据出站", bool((done.get("outbound") or {}).get("token")))

    t3 = "smoke-bk3"
    s3 = await turn(graph, deps, t3, "帮我把热玛吉的预约改到浦东店周五下午")
    p3 = s3["__interrupt__"][0].value
    # 这一步直接调 graph.ainvoke（因为要伪造 plan_hash，不能用 resume 助手），
    # 所以必须自己带上 turn 上下文 —— 否则这一轮里所有模型调用的 thread_id 都会是 NULL。
    # （第 9 节的断言就是这么发现的：日志能记，但归不到具体对话。）
    with trace.turn_scope(t3):
        tampered = await graph.ainvoke(
            Command(resume={"confirmed": True, "plan_hash": "tampered"}),
            {"configurable": {"thread_id": t3}, "recursion_limit": deps.settings.recursion_limit})
    check("方案哈希不符时绝不执行", not tampered.get("execution_result"))
    check("哈希不符被记录为 mismatch", tampered.get("confirm_result") == "mismatch",
          str(tampered.get("confirm_result")))

    # ── 查不到可改约的预约时，不得编造一个"看起来能执行"的方案 ──
    # 否则用户确认后必然在执行阶段失败，而失败信息还是并发冲突，用户和坐席都无从下手。
    saved_appts = dict(deps.pg.appointments)
    deps.pg.appointments.clear()
    t4 = "smoke-bk4"
    s4 = await turn(graph, deps, t4, "帮我把热玛吉的预约改到浦东店周五下午")
    check("无预约记录时不生成待确认方案（不挂起）", not s4.get("__interrupt__"))
    check("无预约记录时给出诚实的降级说明",
          "没有查到" in ((s4.get("outbound") or {}).get("text") or ""),
          (s4.get("outbound") or {}).get("text", "")[:80])
    deps.pg.appointments.update(saved_appts)

    # ══════════════ 3 紧急信号 ══════════════
    section("3. 术后紧急信号：固定模板提示 + 并行转人工")
    s = await turn(graph, deps, "smoke-em", "我做完水光第三天，现在脸发白还特别疼，眼睛也有点看不清")
    ticket = s.get("handoff_ticket") or {}
    check("识别为紧急", s.get("emergency") is True)
    check("记录命中的词与簇", bool(s.get("emergency_hits")))
    check("生成 P0 工单", ticket.get("priority") == "P0", str(ticket.get("priority")))
    check("发出预审模板提示（含急诊指引）", "急诊" in (s.get("outbound") or {}).get("text", ""))
    check("同时产生转接提示（并行走 handoff_notice）", bool(s.get("handoff_notice")))

    # ══════════════ 4 凭据校验 ══════════════
    section("4. 放行凭据绑定内容与方案")
    token = deps.security.sign_release(content="原始内容", plan_hash="p1", review_kind="content")
    ok = True
    try:
        deps.security.verify_token(token, content="原始内容", plan_hash="p1", review_kind="content")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"    正常校验意外失败：{exc}")
    check("正常凭据校验通过", ok)
    for label, kwargs in [("内容被改动", {"content": "改过的内容", "plan_hash": "p1",
                                        "review_kind": "content"}),
                          ("方案被改动", {"content": "原始内容", "plan_hash": "p2",
                                        "review_kind": "content"}),
                          ("审查类别不符", {"content": "原始内容", "plan_hash": "p1",
                                          "review_kind": "operation"})]:
        try:
            deps.security.verify_token(token, **kwargs)
            check(f"{label}时校验失败", False, "竟然通过了")
        except Exception:  # noqa: BLE001
            check(f"{label}时校验失败", True)

    # ══════════════ 5 clarify 预算 ══════════════
    section("5. 澄清预算：连续追问不收敛会兜底")
    counts = []
    for _ in range(int(deps.settings.max_clarify) + 1):
        st = await turn(graph, deps, "smoke-cl", "那个怎么样")
        counts.append(int(st.get("clarify_count", 0)))
    check("前几轮确实在追问", counts and counts[0] >= 1, str(counts))
    check(f"追问次数不超过上限 {deps.settings.max_clarify}",
          max(counts) <= int(deps.settings.max_clarify) + 1, str(counts))
    final = await turn(graph, deps, "smoke-cl", "就是那个呀")
    check("超预算后有兜底动作（转人工或降级回答）",
          bool(final.get("handoff_ticket") or final.get("outbound")))

    # ══════════════ 6 紧急词表抑制规则 ══════════════
    section("6. 紧急词表的四种抑制规则")
    rules = RuleEngine.from_file(settings.rules_file())
    cases = [
        ("我现在眼睛看不清", True, "第一人称自述"),
        ("热玛吉会不会导致失明", False, "询问风险"),
        ("网上说做完会失明", False, "听说/转述"),
        ("我没有视力模糊", False, "否定"),
        ("我朋友做完眼睛看不清", False, "第三人称"),
        ("如果失明了怎么办", False, "假设"),
        ("有点疼", False, "泛词无共现"),
        ("脸发白还特别疼", True, "泛词 + 部位 + 程度"),
        # ── 以下四条来自真实误报 ──
        # 实测「做完热玛吉脸会肿吗？」被判成 P1 紧急：发了急诊提示模板 + 开了 P1 工单，
        # 而用户真正的问题一个字没回答。两个原因：
        #   ① 泛词表里有「热」，它是项目名「热玛吉」的第一个字 ——
        #      于是每句提到热玛吉的话都自带一个"症状词"；
        #   ② 部位+泛词共现规则不区分"正在发生"和"询问可能性"。
        ("做完热玛吉脸会肿吗？", False, "询问可能性（且「热」不应作为症状词）"),
        ("脸有点肿正常吗", False, "询问是否正常"),
        ("热玛吉术后会肿几天", False, "询问时间线"),
        # 反向守住：既是自述又是提问的，必须照常处理 —— 宁可误报不可漏报
        ("我做完热玛吉脸肿了要紧吗", True, "自述 + 提问：不能因为放宽而漏报"),
        ("我脸肿了一直不退", True, "「一直」表示当前状态"),
    ]
    for text, expect, why in cases:
        hits = rules.match_emergency(text)
        check(f"{why}：{text}", bool(hits) is expect,
              f"命中={[h.as_dict() for h in hits]}")

    # ══════════════ 7 多意图冲突消解 ══════════════
    section("7. 多意图冲突消解（纯规则，可单测）")
    check("booking 优先于 recommend", rules.resolve_intents(["recommend", "booking"])[0] == "booking")
    check("术后优先于科普", rules.resolve_intents(["knowledge_edu", "postcare"]) == ["postcare"])
    check("booking 与 knowledge_edu 可并存",
          set(rules.resolve_intents(["knowledge_edu", "booking"])) == {"booking", "knowledge_edu"})
    check("clarify 不进入路由表", rules.resolve_intents(["clarify"]) == [])

    # ══════════════ 8 事实核对出口判定（防"越改越空"回归）══════════════
    section("8. 事实核对出口判定（软/硬问题分档）")
    # 为什么专门测这段：它是一条【反直觉】规则 —— "预算耗尽时不一定降级"。
    # 没有测试锁住的话，后人看到"超限就该 loop_out"会觉得天经地义，一改就把
    # "把合格答案越改越空、最后丢弃"的毛病放回来。
    from app.graph.sub_knowledge import decide_verify_exit  # noqa: E402

    HARD = [{"status": "unsupported"}, {"status": "supported"}]
    SOFT = [{"status": "overstated"}]
    UNKNOWN = [{"status": "maybe_fine"}]      # 模型自由发挥的 status

    def decide(claims, rnd):
        return decide_verify_exit(claims=claims, round_no=rnd, max_loop=3)

    check("全句有依据 → 放行", decide([{"status": "supported"}], 1) == "grounded")
    check("硬问题且有预算 → 回炉", decide(HARD, 1) == "ungrounded")
    check("硬问题预算耗尽 → 降级（编造内容不得出门）", decide(HARD, 3) == "loop_out")
    check("软问题首轮 → 给一次修措辞的机会", decide(SOFT, 1) == "ungrounded")
    check("软问题修过一轮仍不完美 → 放行草稿，不再拿内容换完美",
          decide(SOFT, 2) == "grounded")
    check("未知 status 按硬问题处理（看不懂 ≠ 没问题）",
          decide(UNKNOWN, 1) == "ungrounded" and decide(UNKNOWN, 3) == "loop_out")
    check("核对没返回任何 claims → 按硬问题处理（空 claims 不能当成通过）",
          decide([], 1) == "ungrounded" and decide([], 3) == "loop_out")

    # ══════════════ 9 模型调用日志 ══════════════
    section("9. 模型调用日志（成本与延迟的唯一数据来源）")
    # 为什么在 fake 档位测这个：日志链路（网关 → sink → 存储）跟模型真假无关，
    # 在 fake 里测就能发现"忘了接线""thread_id 没传"这类问题，
    # 不必等到配好真实 API key、跑完一轮、再去数据库里翻。
    calls = deps.pg.llm_calls
    check("模型调用被记录", len(calls) > 0, f"{len(calls)} 条")
    check("每条都带角色与耗时",
          all(c.get("role") and isinstance(c.get("latency_ms"), int) for c in calls),
          str(calls[:1]))
    check("记录了 thread_id（能归属到具体某一轮对话）",
          all(c.get("thread_id") for c in calls),
          str([c.get("thread_id") for c in calls if not c.get("thread_id")][:3]))
    roles = sorted({c["role"] for c in calls})
    check("覆盖多个角色（说明不是只接了某一个入口）", len(roles) >= 4, str(roles))
    check("成功调用都标记为 ok", all(c["ok"] for c in calls),
          str([c for c in calls if not c["ok"]][:2]))

    # ══════════════ 10 二次复核触发判定 ══════════════
    section("10. 二次复核触发判定（防止把成本烧在噪声上）")
    # 为什么专门测：这是一条用实测数据推出来的**反直觉**规则 ——
    # "置信度低"不一定要升级。没有测试锁住的话，后人看到"低置信度就该复核"
    # 会觉得天经地义，一改就把 93% 的无效升级放回来。
    from app.graph.sub_risk import escalation_reason  # noqa: E402

    CLEAN = [{"level": "low", "findings": [], "confidence": 0.9, "abstain": False}] * 3

    def esc(reviews, high=False):
        return escalation_reason(reviews=reviews, high_risk=high)

    check("三份都 low、无 findings、置信度高 → 不升级", esc(CLEAN) is None)
    check("三份都干净但置信度低 → 不升级（实测 45 次升级里 42 次是这种）",
          esc([{**r, "confidence": 0.4} for r in CLEAN]) is None)
    check("确实有 findings 且置信度低 → 升级",
          esc([{**CLEAN[0], "confidence": 0.4, "findings": [{"span": "x"}]},
               CLEAN[1], CLEAN[2]]) == "low_confidence")
    check("有面板判 medium 且置信度低 → 升级",
          esc([{**CLEAN[0], "confidence": 0.4, "level": "medium"}, CLEAN[1], CLEAN[2]])
          == "low_confidence")
    check("有面板弃权 → 一律升级（弃权是明确的「判不了」，必须保守对待）",
          esc([{**CLEAN[0], "abstain": True}, CLEAN[1], CLEAN[2]]) == "abstain")
    check("意见冲突（high 对 low）→ 升级",
          esc([{**CLEAN[0], "level": "high"}, CLEAN[1], CLEAN[2]]) == "conflict")
    check("命中高风险标签 → 升级", esc(CLEAN, high=True) == "high_risk_tag")
    check("一份意见都没拿到 → 升级", esc([]) == "no_reviews")

    await deps.shutdown()

    # ══════════════ 汇总 ══════════════
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

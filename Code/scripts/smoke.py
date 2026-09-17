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

    # ══════════════ 0 工作流架构与真实图的一致性 ══════════════
    section("0. 工作流架构 ↔ 真实图（设计图过期了要当场报出来）")
    # 为什么需要：界面上的「工作流架构」是照 `Workflow/langgraph-main.mmd`
    # 手写下来的分层设计，机器推导不出"这是第几层"。手写的东西一定会漂移，
    # 所以这里拿**编译好的图**去交叉校验：
    #   · 架构里写了、代码里没有 → 设计图过期
    #   · 代码里有、架构没登记   → 加了节点没更新设计图
    # 两种情况都必须报出来。没有这道校验，那张图迟早变成
    # "看着很专业但和代码不符"的装饰品 —— 而画错的架构图比没有图更糟。
    from app.api.graph_topology import build_topology  # noqa: E402

    class _Rt:      # build_topology 只用到 graph 和 deps 两个属性
        pass

    _rt = _Rt()
    _rt.graph, _rt.deps = graph, deps
    topo = build_topology(_rt)
    check("架构与代码完全一致（没有漏登记的节点）",
          not topo["warnings"], "；".join(topo["warnings"]))
    check("架构确实覆盖到了子图内部节点（不是只画了主图）",
          any(s["id"] == "kb" and len(s["nodes"]) >= 8 for s in topo["architecture"]["subgraphs"])
          and any(s["id"] == "risk" and len(s["nodes"]) >= 8
                  for s in topo["architecture"]["subgraphs"]),
          str([(s["id"], len(s["nodes"])) for s in topo["architecture"]["subgraphs"]]))
    _declared = {n for lay in topo["architecture"]["layers"] for n in lay["nodes"]}
    check("每一层都至少有一个节点（空层说明分层写错了）",
          all(lay["nodes"] for lay in topo["architecture"]["layers"]),
          str([(lay["id"], len(lay["nodes"])) for lay in topo["architecture"]["layers"]]))
    check("路由点（菱形）都登记在 decisions 里、且确实是图里的节点",
          all(d in topo["graph"]["nodes"] for d in topo["architecture"]["decisions"])
          and "risk_gate" in topo["architecture"]["decisions"]
          and "dispatch" in topo["architecture"]["decisions"],
          str(topo["architecture"]["decisions"]))
    check("主图连线两端都是已登记的节点（画不出来的边等于没有）",
          all(src in _declared and dst in _declared
              for src, dst, _lab in topo["architecture"]["edges"]),
          str([(s, d) for s, d, _ in topo["architecture"]["edges"]
               if s not in _declared or d not in _declared]))

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

    # ══════════════ 8b 越界时"先用上下文试答一次"的安全默认 ══════════════
    section("8b. 越界问题的上下文试答（答不了必须退回原行为）")
    # 为什么专门测这段：`kb_intake` 判定"这不是知识库该答的问题"之后，原来直接
    # 跳到收口节点吐一句固定话术。实测顾客先说过"我做的是超声炮"（槽位里就写着），
    # 再问"我做的什么项目？"，答案明明在手边却回了"我这边先不做判断" ——
    # 用户的原话是"没有获取到历史记忆"，其实是**这条出口拒绝用已有信息**。
    #
    # 新增的这一步有风险（多了一次模型调用、多了一个可能乱答的地方），
    # 所以把最关键的那条不变量锁死：**空内容 = 什么都不做**。
    from app.graph.sub_knowledge import ctx_reply_patch  # noqa: E402

    empty = ctx_reply_patch("", "这是操作请求")
    check("答不了（空内容）→ 不写草稿，交回原路径转交",
          "kb_draft_content" not in empty, str(empty))
    check("答不了（纯空白）→ 同上（空白不能当成有内容）",
          "kb_draft_content" not in ctx_reply_patch("   \n  ", "拿不准"))
    check("答不了时不吞掉原因（审计要看得见为什么没答）",
          empty["audit_log"][0]["event"] == "kb_ctx_reply_declined"
          and "操作请求" in empty["audit_log"][0]["reason"], str(empty))
    answered = ctx_reply_patch("  您之前提到做的是超声炮。  ", "槽位里有")
    check("答得了 → 写草稿内容并去掉首尾空白",
          answered.get("kb_draft_content") == "您之前提到做的是超声炮。", str(answered))
    check("答得了 → citations / gaps 留空（这一层没有证据可引）",
          answered.get("kb_citations") == [] and answered.get("kb_gaps") == [],
          str(answered))

    # ══════════════ 8c 硬性规则的否定语境（"不要涂抹"不是用药建议）══════════════
    section("8c. 硬性规则的否定处理（否则最安全的提醒会被拦掉）")
    # 为什么专门测这段：硬性规则是**字面匹配**，而"禁止做某事"用的是同一个词。
    # 实测踩到（`scripts/multi_turn_cases.py M5`）：顾客术后发烧、眼睛看不清，
    # 正确的话恰恰是"**不要**自行涂抹任何外用产品" —— 被判成 MED-002「给出用药建议」，
    # 改三轮后升级为 block，于是这条最该发出去的提醒变成"内容被硬性阻断"，
    # 顾客什么都没收到，工单也没有。
    # 与紧急词表当初的坑同类（"热玛吉"里的"热"），结论一样：字面匹配必须处理否定。
    def hits(text: str) -> list[str]:
        return [h.rule_id for h in rules.check_text(text)]

    check("「不要自行涂抹…」不再被判成用药建议",
          "MED-002" not in hits("不要自行涂抹任何外用产品"), str(hits("不要自行涂抹任何外用产品")))
    check("祈使式否定（请勿 / 禁止 / 避免）同样放过",
          all("MED-002" not in hits(t) for t in
              ("请勿涂抹药膏", "禁止使用软膏", "避免涂抹刺激性产品")),
          str([hits(t) for t in ("请勿涂抹药膏", "禁止使用软膏", "避免涂抹刺激性产品")]))
    check("真的在给用药建议 → 照旧拦住（放宽不等于放行）",
          "MED-002" in hits("可以涂抹一点消炎药") and "MED-002" in hits("建议吃点药观察一下"),
          str(hits("可以涂抹一点消炎药")))
    check("否定管不到后半句：「不要吃辣，可以涂抹保湿霜」仍要拦",
          "MED-002" in hits("不要吃辣，可以涂抹保湿霜"),
          str(hits("不要吃辣，可以涂抹保湿霜")))
    check("否定被后面的肯定指令推翻：「无需担心，请涂抹药膏」仍要拦",
          "MED-002" in hits("无需担心，请涂抹药膏"), str(hits("无需担心，请涂抹药膏")))
    check("单字否定词不误伤：「特别疼的时候涂抹药膏」仍要拦",
          "MED-002" in hits("特别疼的时候涂抹药膏"), str(hits("特别疼的时候涂抹药膏")))
    check("其它硬性规则不受影响（诊断结论照旧拦）",
          "MED-001" in hits("你这是典型的过敏"), str(hits("你这是典型的过敏")))

    # ══════════════ 8d 用户主动要求转人工的识别 ══════════════
    section("8d. 「转人工」请求的识别（要能分清'要求转'和'打听人工客服'）")
    # 为什么专门测这段：这件事的结果是"把会话转给真人"，代价高且必须可预测。
    # 第一版只收了关键词、没处理问句，于是"人工客服几点下班"当场被判成转人工请求 ——
    # 而中文里"人工"两个字出现得太随意。与紧急词表处理"会不会…"是同一个取向。
    hr = rules.is_human_request
    check("明确要求转人工 → 认出来",
          all(hr(t) for t in ("转人工", "我要转人工客服", "帮我转接人工", "找个真人来")),
          str([hr(t) for t in ("转人工", "我要转人工客服", "帮我转接人工", "找个真人来")]))
    check("打听人工客服的信息 → **不算**转人工请求",
          not any(hr(t) for t in ("人工客服几点下班", "人工客服电话是多少", "人工费怎么算")),
          str([hr(t) for t in ("人工客服几点下班", "人工客服电话是多少", "人工费怎么算")]))
    check("普通咨询不会误判", not any(hr(t) for t in
                                 ("这个项目多少钱", "我朋友做热玛吉", "你们浦东店在哪")),
          str([hr(t) for t in ("这个项目多少钱", "我朋友做热玛吉", "你们浦东店在哪")]))
    check("阈值可配且默认 3（连续三次才真的转）",
          rules.human_request_threshold == 3, str(rules.human_request_threshold))

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

    # ══════════════ 11 跨轮记忆 ══════════════
    section("11. 跨轮记忆（指代消解的基础）")
    # 为什么专门测：`recent_turns` 是 state 里**声明了、也被读了、但从来没人写**的字段 ——
    # classify 的 Prompt 里"最近对话"永远是"（无）"。后果是用户上一轮刚讲过
    # "热玛吉和超声炮"，这一轮问"那个怎么样"，模型完全接不上，只会反问
    # "您说的是哪个项目" —— 用户会感觉它失忆了。
    # 这类"声明了但没人填充"的 state 字段没有任何机制会报错，只能靠断言盯住。
    m = "smoke-mem"
    first = await turn(graph, deps, m, "热玛吉和超声炮有什么区别")
    check("首轮 recent_turns 为空（还没有历史）",
          (first.get("recent_turns") or []) == [], str(first.get("recent_turns"))[:80])
    second = await turn(graph, deps, m, "那个怎么样")
    hist = second.get("recent_turns") or []
    check("次轮拿到了历史", len(hist) >= 2, f"{len(hist)} 条")
    check("历史里含上一轮的用户原话",
          any("热玛吉和超声炮" in str(h.get("content")) for h in hist), str(hist)[:120])
    check("历史里**不含**本轮这句话（取历史的时机必须在保存本轮之前）",
          not any("那个怎么样" in str(h.get("content")) for h in hist), str(hist)[:160])

    # ══════════════ 12 证据准入：绝对下限 + 相对系数 ══════════════
    section("12. 证据准入（为什么不能用一条绝对分数线）")
    # 为什么专门测：这里原来是一条 `score >= MIN_RERANK(0.30)` 的绝对线，
    # 看着天经地义，实测却是**错的** —— bge-reranker-v2-m3 的分数是 sigmoid 概率，
    # 绝对值取决于问法与文风。实测已审核资料上：
    #     完全不相关的问题（天气/写诗/菜谱）  最高分 0.0000–0.0002
    #     "皮秒做完会不会反黑"（资料字面有"反黑"） 0.1831
    # 一条 0.30 的线卡掉的**全是相关的**：15 条检索验收里 5 条
    # "命中了正确文档、分数却不到 0.30"，证据被丢光 → 回答降级成"资料不足"。
    # 这类"阈值其实标定错了"的问题不会报错、不会告警，只会让答案悄悄变差，
    # 所以必须用断言把**选法的语义**锁住，而不是锁一个具体数字。
    from app.graph.sub_knowledge import select_evidence  # noqa: E402

    def ev(scores):
        return select_evidence([({"doc_id": f"D{i}"}, s) for i, s in enumerate(scores)],
                               floor=0.02, rel_ratio=0.5, max_n=6)

    check("全部不相关（最高分 0.0002）→ 一条都不要（拒绝凭空作答的保护还在）",
          ev([0.0002, 0.0001, 0.0]) == [])
    check("最高分低于下限 → 一条都不要", ev([0.019, 0.018]) == [])
    check("相关但分数不高（0.183）→ **仍然保留**（这正是绝对分数线卡掉的那类）",
          [round(s, 4) for _c, s in ev([0.1831, 0.092, 0.011])] == [0.1831, 0.092])
    check("最高分一定被保留（相对规则不得把第一名筛掉）",
          len(ev([0.05, 0.05, 0.05])) >= 1)
    check("保留的是“最高分附近那一簇”，长尾被丢掉",
          [round(s, 4) for _c, s in ev([0.90, 0.80, 0.40, 0.10])] == [0.90, 0.80])
    check("候选数受 max_n 限制",
          len(select_evidence([({"doc_id": f"D{i}"}, 0.9) for i in range(20)],
                              floor=0.02, rel_ratio=0.5, max_n=6)) == 6)
    check("输入乱序也能正确选（不依赖调用方先排好）",
          [round(s, 4) for _c, s in ev([0.40, 0.90, 0.80])] == [0.9, 0.8])
    check("空候选 → 空结果（不得抛异常）", ev([]) == [])
    check("下调到下限刚好等于分数时算通过（边界取等号）", len(ev([0.02, 0.02])) == 2)

    # ══════════════ 13 弱证据必须加复核 ══════════════
    section("13. 弱证据必须多走一次独立复核（放松闸门就得同时加复核）")
    # 为什么专门测：这轮把证据闸门从"一条绝对分数线"改成"下限 + 相对系数"，
    # 结果是**更多轮次会带着分数不高的证据继续作答**。只放松、不加复核，
    # 等于两处一起变松 —— 而这两处都站在"别答错"这条链上。
    #
    # ★ 这里的精排必须**造一个低分的**：假精排的分数下限是 0.30
    #   （`0.30 + 重合度 × 0.02`），永远够不到"弱证据"那条线，
    #   于是这条新路径在 fake 档位里根本走不到 —— 这也正是
    #   "绝对阈值标定错了"当初始终没被测出来的原因：**假实现比真实情况乐观**，
    #   而乐观的假实现会让依赖它的所有断言一起失去意义。
    import dataclasses  # noqa: E402

    class _LowReranker:
        """故意返回低分：真实模型在口语提问上就是这个样子（实测 0.05–0.18）。"""

        async def score(self, query: str, docs: list[str]) -> list[float]:
            return [0.12] * len(docs)

    # ★ 问法必须选**真的会走到知识库子图**的那一句：上面第 1 节就用它跑通了
    #   kb_draft → 越界 → 修订 → 复审。我一开始随手写了个"热玛吉是怎么让皮肤变紧的"，
    #   结果那一轮根本没进子图（角色里只有 clarify）—— 断言于是变成
    #   "两次都没触发"，看着全绿其实什么也没验。**测试的前提本身也要被验证。**
    WEAK_Q = "热玛吉和超声炮有什么区别"      # 刻意不含任何高影响词
    deps_low = dataclasses.replace(deps, reranker=_LowReranker())
    graph_low = build_graph(deps_low, checkpointer=InMemorySaver())

    n0 = len(deps.pg.llm_calls)
    await turn(graph_low, deps_low, "smoke-weak", WEAK_Q)
    roles_low = [c["role"] for c in deps.pg.llm_calls[n0:]]
    # ★ 先验前提本身：这一轮必须真的走到了"证据之后"（kb_draft 或 kb_limit）。
    #   否则下面那条断言会因为"两次都没进子图"而变成**空跑通过** ——
    #   这比断言失败更坏，因为它会让人以为这条路径被守住了。
    check("前提：这一轮确实走到了知识库子图的证据之后",
          any(r in roles_low for r in ("kb_draft", "kb_limit")), str(sorted(set(roles_low))))
    check("低分证据 → 触发“证据是否充分”的独立复核",
          "evidence_second_opinion" in roles_low, str(sorted(set(roles_low))))

    n1 = len(deps.pg.llm_calls)
    await turn(graph, deps, "smoke-strong", WEAK_Q)
    roles_strong = [c["role"] for c in deps.pg.llm_calls[n1:]]
    check("同一句话、证据分数够高时**不**触发（说明触发条件确实是“弱证据”）",
          "evidence_second_opinion" not in roles_strong, str(sorted(set(roles_strong))))

    # ══════════════ 14 引用字段的容错 ══════════════
    section("14. citations 能收住裸字符串引用（否则整轮会变成转人工）")
    # 为什么专门测：模型看到对话历史里上一轮回答的 `[E1]` 标记，会"顺手"把它填进
    # 本轮的 citations。**这个坑复发过一次**：
    #   · 第一回只给 `SpecialistDraftOut` 加了容错，理由是"知识库草稿的 citations
    #     是溯源凭据，必须严格"；
    #   · 结果真实档位下 `kb_limit`（证据不足的降级路径，真实数据下最常走）
    #     6 次调用里 4 次因 `citations.0 Input should be an object` 失败 →
    #     连试两次不合格 → LLMError → **兜底转人工**。
    #   顾客问一句本该得到"资料不足 + 建议面诊"的话，收到的是"已为您转接人工客服"。
    # 真正的"引用可溯源"由下游 `kb_verify` 逐句核对，跟 schema 的形状无关。
    from app.graph.schemas import (  # noqa: E402
        KbDraftOut, ReceiptOut, SpecialistDraftOut)

    for _cls in (KbDraftOut, SpecialistDraftOut, ReceiptOut):
        _name = _cls.__name__
        _bad = _cls.model_validate({"content": "x", "citations": ["E1"]})
        check(f"{_name} 收住裸字符串引用（不再抛校验错）",
              [c.evidence_id for c in _bad.citations] == ["E1"], str(_bad.citations))
        _good = _cls.model_validate(
            {"content": "y", "citations": [{"evidence_id": "E1", "doc_id": "DOC-01",
                                            "quote": "原文", "version": "v1"}]})
        check(f"{_name} 正常对象原样通过（容错不等于放松）",
              _good.citations[0].doc_id == "DOC-01", str(_good.citations))

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

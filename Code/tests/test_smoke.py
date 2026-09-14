"""
冒烟测试：不依赖任何外部服务（Postgres / Milvus / DeepSeek / BGE 全用假实现）。

测的不是"模型答得好不好"，而是**图的接线与不变量**：
  1. 科普链路能跑通，且越界草稿会被打回修订、修订后通过
  2. 预约链路必须经过 interrupt 确认；未确认不执行；确认后结果回复要再送审
  3. 紧急信号走固定模板 + 并行转人工（不是模型生成）
  4. 凭据校验：内容被改动后必须校验失败
  5. clarify 预算：连续追问到上限后降级兜底，不会无限追问
  6. 紧急词表的四种抑制规则（询问风险 / 否定 / 第三人称 / 假设）
"""

from __future__ import annotations

import copy

import pytest

from app.graph.build import build_graph
from app.services.deps import Deps
from app.services.rules import RuleEngine
from app.settings import Settings


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    object.__setattr__(s, "profile", "fake")
    return s


@pytest.fixture
async def runtime(settings: Settings):
    deps = Deps.build(settings)
    await deps.startup()
    from langgraph.checkpoint.memory import InMemorySaver
    graph = build_graph(deps, checkpointer=InMemorySaver())
    yield graph, deps
    await deps.shutdown()


def _config(thread: str, deps: Deps) -> dict:
    return {"configurable": {"thread_id": thread}, "recursion_limit": deps.settings.recursion_limit}


async def _turn(graph, deps, thread: str, text: str) -> dict:
    return await graph.ainvoke(
        {"session_id": thread, "user_input": text, "channel": "test", "attachments": []},
        _config(thread, deps))


# ══════════════ 1. 科普链路 + 修订环 ══════════════
async def test_knowledge_flow_revises_then_passes(runtime):
    graph, deps = runtime
    state = await _turn(graph, deps, "t-kb", "热玛吉和超声炮有什么区别")

    assert state["verdict"] == "pass"
    # 第一版草稿含"保证年轻十岁"→ 硬规则 AD-001 命中 → 退回修订 → 第二版通过
    assert state["revision_count"] >= 1, "应当至少发生一次修订"
    assert state["review_round"] >= 2, "修订后必须重新送审"
    out = state.get("outbound") or {}
    assert out.get("kind") == "reply"
    assert "保证" not in out["text"], "被拦下的疗效承诺不得出现在最终出站内容里"
    assert out.get("token"), "出站必须携带放行凭据"
    # 每一次审查都要落库（审计可回溯）
    assert deps.pg.audits, "审查结论必须写审计"


# ══════════════ 2. 预约链路：确认 → 执行 → 结果复审 ══════════════
async def test_booking_requires_confirmation(runtime):
    graph, deps = runtime
    from langgraph.types import Command

    text = "帮我把热玛吉的预约改到浦东店周五下午"
    state = await _turn(graph, deps, "t-bk", text)
    pending = state.get("__interrupt__")
    assert pending, "操作类请求必须挂起等待用户确认"
    payload = pending[0].value
    assert payload["plan_hash"], "挂起时必须带回方案哈希"

    # 用户否认 → 不执行
    denied = await graph.ainvoke(
        Command(resume={"confirmed": False, "plan_hash": payload["plan_hash"]}),
        _config("t-bk", deps))
    assert not denied.get("execution_result"), "未确认时绝不能执行"
    assert denied["confirm_result"] == "denied"


async def test_booking_executes_and_rechecks_result(runtime):
    graph, deps = runtime
    from langgraph.types import Command

    text = "帮我把热玛吉的预约改到浦东店周五下午"
    state = await _turn(graph, deps, "t-bk2", text)
    payload = state["__interrupt__"][0].value
    done = await graph.ainvoke(
        Command(resume={"confirmed": True, "plan_hash": payload["plan_hash"]}),
        _config("t-bk2", deps))

    assert done.get("execution_result"), "确认后应当执行"
    assert done["idempotency_key"], "必须记录幂等键"
    # 第二次审查的对象是"执行结果回复"
    kinds = [a.get("review_kind") for a in deps.pg.audits if a.get("review_kind")]
    assert "result_reply" in kinds, "执行结果的回复必须再次送审"
    out = done.get("outbound") or {}
    assert out.get("kind") == "reply" and out.get("token")


async def test_plan_hash_mismatch_voids_plan(runtime):
    graph, deps = runtime
    from langgraph.types import Command

    text = "帮我把热玛吉的预约改到浦东店周五下午"
    state = await _turn(graph, deps, "t-bk3", text)
    tampered = await graph.ainvoke(
        Command(resume={"confirmed": True, "plan_hash": "tampered-hash"}),
        _config("t-bk3", deps))
    assert not tampered.get("execution_result"), "方案哈希不符时绝不能执行"
    assert tampered["confirm_result"] == "mismatch"


# ══════════════ 3. 紧急信号 ══════════════
async def test_emergency_path_uses_template_and_handoff(runtime):
    graph, deps = runtime
    state = await _turn(graph, deps, "t-em", "我做完水光第三天，现在脸发白还特别疼")

    assert state["emergency"] is True
    assert state["emergency_hits"], "必须记录命中的词与簇"
    ticket = state.get("handoff_ticket") or {}
    assert ticket.get("priority") == "P0", "疑似坏死信号应为 P0"
    out = state.get("outbound") or {}
    # 紧急场景并行两条出口：已审模板提示 + 人工工单
    assert out.get("text") and "急诊" in out["text"], "应当发出预审模板提示"


# ══════════════ 4. 凭据校验 ══════════════
def test_release_token_binds_content(runtime):
    _, deps = runtime
    token = deps.security.sign_release(content="原始内容", plan_hash="p1", review_kind="content")
    deps.security.verify_token(token, content="原始内容", plan_hash="p1", review_kind="content")

    with pytest.raises(Exception):
        deps.security.verify_token(token, content="被改过的内容", plan_hash="p1",
                                   review_kind="content")
    with pytest.raises(Exception):
        deps.security.verify_token(token, content="原始内容", plan_hash="p2",
                                   review_kind="content")


# ══════════════ 5. clarify 预算 ══════════════
async def test_clarify_budget_exhausts(runtime):
    graph, deps = runtime
    thread = "t-cl"
    seen = []
    for _ in range(deps.settings.max_clarify + 2):
        state = await _turn(graph, deps, thread, "那个怎么样")
        seen.append((state.get("clarify_count", 0), bool(state.get("handoff_ticket"))))

    assert seen[0][0] >= 1, "第一轮应当追问"
    # 预算用尽后不应再无限追问：要么转人工，要么降级给一般性说明
    final = await _turn(graph, deps, thread, "就是那个呀")
    assert final.get("handoff_ticket") or final.get("outbound"), "超预算后必须有兜底动作"


# ══════════════ 6. 紧急词表的抑制规则 ══════════════
@pytest.mark.parametrize("text,should_hit", [
    ("我现在眼睛看不清", True),            # 第一人称自述 → 命中
    ("热玛吉会不会导致失明", False),        # 询问风险 → 不命中
    ("网上说做完会失明", False),            # 听说 → 不命中
    ("我没有视力模糊", False),              # 否定 → 不命中
    ("我朋友做完眼睛看不清", False),        # 第三人称 → 不命中
    ("如果失明了怎么办", False),            # 假设 → 不命中
    ("有点疼", False),                     # 泛词无共现 → 不命中
    ("脸发白还特别疼", True),               # 泛词 + 部位 + 程度 → 命中
])
def test_emergency_lexicon_rules(settings, text, should_hit):
    rules = RuleEngine.from_file(settings.rules_file())
    hits = rules.match_emergency(text)
    assert bool(hits) is should_hit, f"{text} → {[h.as_dict() for h in hits]}"


# ══════════════ 7. 多意图冲突消解 ══════════════
def test_intent_conflict_resolution(settings):
    rules = RuleEngine.from_file(settings.rules_file())
    # booking 优先，recommend 让位
    assert rules.resolve_intents(["recommend", "booking"])[0] == "booking"
    # 术后优先于科普
    got = rules.resolve_intents(["knowledge_edu", "postcare"])
    assert got == ["postcare"]
    # booking + knowledge_edu 可以并存
    assert set(rules.resolve_intents(["knowledge_edu", "booking"])) == {"booking", "knowledge_edu"}
    # clarify 单独出现时不会被 resolve 掉
    assert rules.resolve_intents(["clarify"]) == []

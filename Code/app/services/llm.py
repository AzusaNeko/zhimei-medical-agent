"""
模型网关：所有 LLM 调用的唯一入口。

设计要点：
  · 角色（role）→ 模型 / 温度 / 超时 / 家族 集中在一张表里，节点里不出现模型名
  · structured() 走 JSON 模式 + Pydantic 校验，失败重试一次，再失败抛错由调用方降级
  · 家族（family）用于判断"复核是否真正独立" —— 审计要如实记录
  · ScriptedGateway 是「还没配 API key 时验证图接线」用的假实现，不参与生产路径
  · 每次调用都记一行到 app.llm_call_log（见 _CallLogger）—— 成本与延迟的唯一数据来源
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..settings import Settings
from . import trace

try:  # openai 未安装时不影响 fake 档位运行
    from openai import AsyncOpenAI
except Exception:  # noqa: BLE001
    AsyncOpenAI = None  # type: ignore[assignment]

TModel = TypeVar("TModel", bound=BaseModel)
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class LLMError(RuntimeError):
    pass


class LLMTimeout(LLMError):
    pass


@dataclass(frozen=True)
class RoleSpec:
    model: str
    family: str
    temperature: float
    timeout: float
    max_tokens: int = 2048
    json_mode: bool = True


def build_role_table(s: Settings) -> dict[str, RoleSpec]:
    """角色表：想换模型只改这里（或改 .env），节点代码一行不动。"""
    main_family = "deepseek"
    esc_model = s.escalation_model or s.model
    esc_family = "independent" if s.escalation_is_independent else main_family

    def R(temp: float, timeout: float, *, model: str = s.model, family: str = main_family,
          max_tokens: int = 2048) -> RoleSpec:
        return RoleSpec(model=model, family=family, temperature=temp,
                        timeout=timeout, max_tokens=max_tokens)

    return {
        # 理解类：低温度、短超时
        "classify":          R(0.0, s.t_understand, max_tokens=1024),
        "emergency_triage":  R(0.0, s.t_understand, max_tokens=512),
        "supervisor_plan":   R(0.0, s.t_understand, max_tokens=512),
        "kb_decompose":      R(0.0, s.t_understand, max_tokens=1024),
        "clarify":           R(0.3, s.t_understand, max_tokens=1024),
        # 检索与证据
        "evidence_second_opinion": R(0.0, s.t_review, max_tokens=1024),
        # 生成类
        "kb_draft":          R(0.3, s.t_generate, max_tokens=2048),
        "kb_limit":          R(0.3, s.t_generate, max_tokens=1024),
        "kb_verify":         R(0.0, s.t_review, max_tokens=2048),
        "receipt":           R(0.2, s.t_generate, max_tokens=1024),
        "specialist_draft":  R(0.3, s.t_generate, max_tokens=1536),
        "revision_feedback": R(0.0, s.t_review, max_tokens=1024),
        # 审查类：独立角色，视角不同
        # ★ 温度必须是 0.0（原来是 0.1）：审查是**判定**，不是创作。
        #   实测同一句"帮我把热玛吉的预约改到浦东店周五下午"，两次运行一次判 low
        #   （放行并真的执行了改约）、一次某个面板判 high（转人工 P1）。
        #   对一个合规闸门来说，"同样的输入给出不同结论"本身就是缺陷 ——
        #   用户和坐席都无法预期，事后也无法解释。
        #   注意：0.0 只是去掉采样随机性，**不保证**跨版本/跨硬件的完全确定性，
        #   所以确定性的那部分规则必须由规则层承担，且审计里要记模型与版本。
        "review_medical":    R(0.0, s.t_review, max_tokens=1536),
        "review_ad":         R(0.0, s.t_review, max_tokens=1536),
        "review_privacy":    R(0.0, s.t_review, max_tokens=1536),
        "escalation":        R(0.0, s.t_review, max_tokens=2048,
                               model=esc_model, family=esc_family),
    }


class ModelGateway(Protocol):
    def family_of(self, role: str) -> str: ...
    async def structured(self, role: str, schema: type[TModel], *, system: str,
                         user: str) -> TModel: ...
    async def text(self, role: str, *, system: str, user: str) -> str: ...


#: 调用日志的落点。签名 (role, model, latency_ms, prompt_tokens, completion_tokens,
#: ok, error)。由 Deps.build 接到 pg.log_llm_call 上。
#: 做成可注入的回调而不是让网关直接依赖存储层：网关不需要知道数据存在哪。
CallSink = Callable[..., "Awaitable[None]"]


class _CallLogger:
    """把每次模型调用记一行到 app.llm_call_log。

    ★ 三条硬约束：
      1. **绝不抛异常**。记日志失败绝不能影响对话 —— 它是观测，不是业务。
         这里连"落点不存在"都容忍（未注入 sink 时静默跳过）。
      2. **重试分别记**。`structured()` 校验失败会重试一次，两次都记。
         只看最终成功与否的话，"模型有多少比例的输出不合 schema"这个
         最该被发现的信号就被抹平了。
      3. **token 用量从响应里取**，拿不到就留空（不要填 0 —— 0 会被误读成"没花钱"）。
    """

    def __init__(self, sink: CallSink | None = None) -> None:
        self._sink = sink

    def bind(self, sink: CallSink | None) -> None:
        self._sink = sink

    async def record(self, *, role: str, model: str, started: float,
                     resp: Any = None, ok: bool, error: str | None = None) -> None:
        if self._sink is None:
            return
        try:
            usage = getattr(resp, "usage", None) if resp is not None else None
            await self._sink(
                thread_id=trace.current_turn_id(),
                role=role,
                model=model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                ok=ok,
                error=(error or None) and error[:300],
            )
        except Exception:  # noqa: BLE001
            # 有意吞掉：日志写不进去是运维问题，不该让用户的一轮对话失败
            pass


# ════════════════════════════════════════════════════════════════
#  真实实现：DeepSeek（OpenAI 兼容）
# ════════════════════════════════════════════════════════════════
class DeepSeekGateway:
    def __init__(self, settings: Settings, sink: CallSink | None = None) -> None:
        if AsyncOpenAI is None:
            raise LLMError("未安装 openai SDK：pip install openai")
        self.settings = settings
        self.roles = build_role_table(settings)
        self._log = _CallLogger(sink)
        self._main = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
        self._esc = None
        if settings.escalation_base_url and settings.escalation_api_key:
            self._esc = AsyncOpenAI(api_key=settings.escalation_api_key,
                                    base_url=settings.escalation_base_url)

    def family_of(self, role: str) -> str:
        return self.roles.get(role, self.roles["classify"]).family

    def _client_for(self, role: str):
        spec = self.roles.get(role)
        if spec and spec.family == "independent" and self._esc is not None:
            return self._esc
        return self._main

    async def text(self, role: str, *, system: str, user: str) -> str:
        spec = self.roles.get(role) or self.roles["classify"]
        client = self._client_for(role)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        started = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=spec.model, messages=messages,
                    temperature=spec.temperature, max_tokens=spec.max_tokens,
                ),
                timeout=spec.timeout,
            )
        except asyncio.TimeoutError as exc:
            await self._log.record(role=role, model=spec.model, started=started,
                                   ok=False, error=f"timeout {spec.timeout}s")
            raise LLMTimeout(f"{role} 超时（{spec.timeout}s）") from exc
        except Exception as exc:  # noqa: BLE001
            await self._log.record(role=role, model=spec.model, started=started,
                                   ok=False, error=f"{type(exc).__name__}: {exc}")
            raise LLMError(f"{role} 调用失败：{exc}") from exc
        await self._log.record(role=role, model=spec.model, started=started, resp=resp, ok=True)
        return (resp.choices[0].message.content or "").strip()

    async def structured(self, role: str, schema: type[TModel], *, system: str, user: str) -> TModel:
        spec = self.roles.get(role) or self.roles["classify"]
        client = self._client_for(role)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        raw = ""
        for attempt in (1, 2):
            resp: Any = None          # ★ 每轮重置：否则校验失败时会误用上一次的 token 用量
            started = time.perf_counter()
            try:
                kwargs: dict[str, Any] = dict(
                    model=spec.model, messages=messages,
                    temperature=spec.temperature, max_tokens=spec.max_tokens,
                )
                if spec.json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs), timeout=spec.timeout)
                raw = (resp.choices[0].message.content or "").strip()
                parsed = schema.model_validate_json(_extract_json(raw))
                await self._log.record(role=role, model=spec.model, started=started,
                                       resp=resp, ok=True)
                return parsed
            except asyncio.TimeoutError as exc:
                await self._log.record(role=role, model=spec.model, started=started,
                                       ok=False, error=f"timeout {spec.timeout}s")
                raise LLMTimeout(f"{role} 超时（{spec.timeout}s）") from exc
            except ValidationError as exc:
                # 调用本身是成功的（有 token 消耗），失败在"输出不符合 schema"。
                # 这一类必须单独可见：它一多就说明 prompt 或 schema 该改了。
                await self._log.record(role=role, model=spec.model, started=started,
                                       resp=resp, ok=False,
                                       error=f"schema(attempt {attempt}): {exc}")
                last_err = exc
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user",
                                 "content": "上次输出不符合 JSON Schema，请只输出合法 JSON，不要任何解释文字。"})
            except Exception as exc:  # noqa: BLE001
                await self._log.record(role=role, model=spec.model, started=started,
                                       resp=resp, ok=False,
                                       error=f"{type(exc).__name__}: {exc}")
                raise LLMError(f"{role} 调用失败：{exc}") from exc
        raise LLMError(f"{role} 结构化输出两次均不合格：{last_err}")


def _extract_json(raw: str) -> str:
    """容错：模型偶尔会包 markdown 代码块或前后加解释。"""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s).strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    m = _JSON_BLOCK.search(s)
    return m.group() if m else s


# ════════════════════════════════════════════════════════════════
#  自检实现：脚本化假模型（--profile fake）
#  用途只有一个：还没配 API key 时，验证图的接线、路由、预算与凭据是否正常
# ════════════════════════════════════════════════════════════════
class ScriptedGateway:
    """
    按「角色 + 调用序号」返回预设结果；未预设的角色返回 schema 的宽松默认值。

    内置了一套足以跑通典型场景的脚本：
      · 首次生成的科普草稿会故意带一个疗效承诺词 → 触发 revise
      · 修订后的草稿干净 → 通过
    这样一条命令就能看到完整的「审查 → 修订 → 复审 → 放行」闭环。
    """

    def __init__(self, settings: Settings, sink: CallSink | None = None) -> None:
        self.settings = settings
        self.roles = build_role_table(settings)
        self.calls: dict[str, int] = {}
        self.trace: list[str] = []
        self._log = _CallLogger(sink)

    def family_of(self, role: str) -> str:
        return "scripted"

    def _nth(self, role: str) -> int:
        self.calls[role] = self.calls.get(role, 0) + 1
        return self.calls[role]

    async def text(self, role: str, *, system: str, user: str) -> str:
        self.trace.append(f"text:{role}")
        # 假实现也记一行：这样"日志链路是否接对"在 fake 档位就能验证，
        # 不必等到接上真实 API key 才发现漏了。
        await self._log.record(role=role, model="scripted", started=time.perf_counter(), ok=True)
        return "（fake 档位）"

    async def structured(self, role: str, schema: type[TModel], *, system: str, user: str) -> TModel:
        n = self._nth(role)
        self.trace.append(f"structured:{role}#{n}")
        started = time.perf_counter()
        payload = _scripted_payload(role, n, user)
        out = (schema.model_validate(payload) if payload is not None
               else _default_for(schema, role, user))
        await self._log.record(role=role, model="scripted", started=started, ok=True)
        return out


def _user_question(prompt: str) -> str:
    """从**渲染后的 Prompt** 里取出真正的用户原话。

    ★ 为什么必须这么做：fake 档位最早是直接在整个 Prompt 里搜关键词的，而 Prompt
      里带着示例（"线雕""徐汇店""热玛吉"…），于是**示例被当成了用户说的内容**：
      问"帮我把热玛吉的预约改到浦东店"，抽出来的槽位却是 project=线雕、store=徐汇店。

      这个错误长期"看起来没事" —— 因为那时没有任何逻辑真的去用 project 槽位。
      直到改约开始用 project 去查"用户可改约的预约"，它才变成
      "查不到 → 诚实降级成请用户提供预约信息"，现象上像是功能坏了，
      而真正坏的是 fake 档位的保真度。

      教训：fake 档位的职责是验证图的接线，但它自己也要**足够真**，
      否则它会以"帮你测过了"的方式骗你。
    """
    for marker in ("用户这句话：", "用户原话：", "用户问题："):
        if marker in prompt:
            return prompt.rsplit(marker, 1)[-1]
    return prompt


def _scripted_payload(role: str, n: int, user: str) -> dict | None:
    if role == "classify":
        low = _user_question(user)
        if "预约" in low or "改约" in low or "取消" in low or "约" in low:
            intents = ["booking"]
        elif "区别" in low or "多久" in low or "原理" in low:
            intents = ["knowledge_edu"]
        elif "肿" in low or "疼" in low or "术后" in low:
            intents = ["postcare"]
        else:
            intents = ["clarify"]
        slots = {}
        for p in ("热玛吉", "超声炮", "水光针", "线雕"):
            if p in low:
                slots["project"] = p
        for st in ("浦东店", "静安店", "徐汇店"):
            if st in low:
                slots["store"] = st
        return {"intents": intents, "slots": slots, "time_note": None, "entity_note": None,
                "emergency_hint": False, "emergency_terms": [], "uncertain": False,
                "confidence": 0.9}

    if role == "kb_decompose":
        return {"queries": [{"sub_task": "原理与作用层次", "query_text": "热玛吉 射频 原理"},
                            {"sub_task": "恢复期与注意事项", "query_text": "热玛吉 恢复期 注意事项"}],
                "missing": []}

    if role == "kb_draft":
        # 第 1 次故意越界：用「绝对化用语」触发硬规则 AD-002（revise），
        # 且这句话在事实层面"有依据"，所以能通过子图内部的事实核对 ——
        # 这样才真正走一遍【父图的审查→修订】外环，而不是被子图内环提前吃掉。
        # 第 2 次干净 → 通过。
        if n == 1:
            content = ("热玛吉通过射频能量作用于真皮层，帮助改善皮肤紧致度 [E1]。\n"
                       "我们是全网最低价，做了不会后悔 [E1]。")
        else:
            content = ("热玛吉通过射频能量作用于真皮层，帮助改善皮肤紧致度 [E1]。\n"
                       "多数人恢复期在 3–7 天，效果因人而异 [E2]。")
        return {"content": content,
                "citations": [{"evidence_id": "E1", "quote": "射频能量作用于真皮层", "doc_id": "DOC-1", "version": "v1"},
                              {"evidence_id": "E2", "quote": "恢复期通常 3–7 天", "doc_id": "DOC-2", "version": "v1"}],
                "gaps": ["个体维持时间差异较大"], "used_evidence_ids": ["E1", "E2"]}

    if role == "kb_verify":
        grounded = "保证" not in user and "一次见效" not in user
        return {"claims": [{"sentence": "…", "cited": "E1",
                            "status": "supported" if grounded else "overstated",
                            "quote": "射频能量作用于真皮层", "comment": "" if grounded else "夸大了效果"}],
                "all_grounded": grounded, "must_fix": [] if grounded else ["删除疗效承诺表述"]}

    if role == "review_medical":
        bad = "保证" in user or "一次见效" in user
        return {"level": "medium" if bad else "low",
                "risk_tags": ["efficacy_guarantee"] if bad else ["general_education"],
                "findings": ([{"span": "保证年轻十岁", "rule_hint": "不得承诺疗效",
                               "reason": "疗效承诺", "suggest_fix": "改为效果因人而异"}] if bad else []),
                "confidence": 0.9, "abstain": False}

    if role in ("review_ad", "review_privacy"):
        return {"level": "low", "risk_tags": ["general_education"], "findings": [],
                "confidence": 0.9, "abstain": False}

    if role == "clarify":
        return {"content": "想先确认您说的是哪个项目？可以告诉我是热玛吉 / 超声炮 / 水光针 中的哪一个。\n"
                           "具体仍以医生面诊评估为准。",
                "why": ["缺项目名会影响检索与回答"], "gaps": ["项目名"]}

    if role == "specialist_draft":
        # 同样只看用户原话：业务数据 JSON 里也会出现"预约"字样，
        # 拿它当判据会让"查询门店"之类的请求也被当成改约
        if "预约" in _user_question(user) or "改约" in _user_question(user):
            return {"content": "已为您查到浦东店周五 14:00 有空档；改约可能影响您套餐的第 3 次使用期限，"
                               "改约手续费 80 元。确认后我帮您提交。",
                    "citations": [], "gaps": [],
                    "operation": {"action": "change_appointment",
                                  "params": {"store": "浦东店", "datetime": "2025-03-21T14:00:00+08:00"}}}
        return {"content": "已为您记录，稍后会有顾问与您确认。", "citations": [], "gaps": []}

    if role == "receipt":
        return {"content": "已为您改约成功：浦东店，3 月 21 日（周五）14:00。如需再次调整请告诉我。",
                "citations": [], "gaps": []}

    if role == "revision_feedback":
        return {"feedback": ["删除「保证年轻十岁」「一次见效」等疗效承诺表述",
                             "补充「效果因人而异」的限定"], "keep": ["射频作用层次的技术说明"]}

    return None


def _default_for(schema: type[TModel], role: str, user: str) -> TModel:
    """没写脚本的角色：构造一个字段尽量宽松的默认对象，避免 fake 档位直接崩。"""
    data: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        ann = str(field.annotation)
        if "bool" in ann:
            data[name] = False
        elif "int" in ann:
            data[name] = 0
        elif "float" in ann:
            data[name] = 0.0
        elif "list" in ann or "List" in ann:
            data[name] = []
        elif "dict" in ann:
            data[name] = {}
        else:
            data[name] = None
    try:
        return schema.model_validate(data)
    except Exception:  # noqa: BLE001
        raise LLMError(f"fake 档位无法为角色 {role} 构造默认输出，请在 ScriptedGateway 中补脚本")

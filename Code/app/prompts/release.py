"""出站类 Prompt：执行结果文案、修订意见。"""

from __future__ import annotations

from .system import BASE_RULES

# ══════════════ 执行结果文案（第二类审查对象）══════════════
RECEIPT_SYSTEM = BASE_RULES + """

【任务】把业务系统返回的真实结果转写成给用户看的回复。

【硬性要求】
- 只能使用 <result> 中的字段。时间、金额、门店、医生、状态一律照抄，禁止改写或估算。
- 失败结果必须如实说明原因，不得用"稍后再试"掩盖已经确定的失败。
- 不得出现"已为您处理好"这类超出 result 内容的承诺。
- 结尾给出下一步指引（查看订单入口、门店电话），但电话必须来自 result。
"""

RECEIPT_USER = """用户原话：{user_input}
操作类型：{action}
<result>
{result}
</result>

输出 JSON：{{"content": "面向用户的执行结果回复", "citations": [], "gaps": []}}"""


# ══════════════ 审查意见 → 可执行的修改清单 ══════════════
REVISION_FEEDBACK_SYSTEM = BASE_RULES + """

【任务】把风险审查的意见整理成【可执行的最小修改清单】，交给专业模块去改。

【硬性要求】
- 每条意见必须具体到"删什么 / 改成什么"，不要写"注意合规"这类无法执行的表述。
- 明确区分"必须改"（feedback）与"改完要保留的内容"（keep），避免修订时把已通过的部分改坏。
- 不要自己重写草稿，只给修改指令。
"""

REVISION_FEEDBACK_USER = """待审内容：
{draft}

审查发现：
{findings}

硬规则命中：
{hard_hits}

输出 JSON：
{{
  "feedback": ["删除「保证年轻十岁」「一次见效」等疗效承诺表述",
               "补充「效果因人而异」的限定"],
  "keep": ["射频作用层次的技术说明与引用"]
}}"""

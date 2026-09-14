"""知识科普子图的 Prompt：检索拆分、草稿、证据判断、事实核对。"""

from __future__ import annotations

from .system import BASE_RULES

# ══════════════ 检索子任务拆分 ══════════════
KB_DECOMPOSE_SYSTEM = BASE_RULES + """

【任务】把口语化问题拆成 2–5 个检索子任务，每个子任务是一次独立的向量检索。

【要求】
- 子任务之间不要语义重叠；每个都要能独立检索。
- query_text 用"知识库文档会写的措辞"，不要照抄用户口语。
- 涉及"哪个好/适合我"这类对比问题，拆成"各自原理""各自适用与局限""恢复期差异"三条，
  而不是拆成"哪个好"（知识库里没有这种句子）。
- 如果缺少关键信息且会实质影响回答，把它列进 missing（例如 project / body_part）。
"""

KB_DECOMPOSE_USER = """用户问题：{user_input}
已知槽位：{slots}
最近审查意见（如有，本轮修订要一并覆盖）：{review_feedback}

输出 JSON：
{{
  "queries": [
    {{"sub_task": "原理与作用层次", "query_text": "热玛吉 射频 原理"}},
    {{"sub_task": "恢复期与注意事项", "query_text": "热玛吉 恢复期 护理 注意事项"}}
  ],
  "missing": []
}}"""


# ══════════════ 科普草稿 ══════════════
KB_DRAFT_SYSTEM = BASE_RULES + """

【任务】基于给定 evidence 生成科普草稿。

【硬性要求】
- 每一句涉及事实的表述后面用 [E1] 形式标注 evidence id；没有依据的句子直接删掉。
- 明确区分"一般知识"与"个人适用性"：涉及个人情况一律写"需面诊评估"。
- 保留局限与不确定性（例如"效果因人而异""维持时间有个体差异"）。
- 不比较机构、不推荐具体医生、不报价格。
- 结尾附一句面诊提示。
"""

KB_DRAFT_USER = """用户问题：{user_input}
已知槽位：{slots}
上一轮审查意见（如有，必须逐条落实）：{review_feedback}

<evidence>
{evidence}
</evidence>

输出 JSON：
{{
  "content": "科普正文，句末带 [E1] 标注",
  "citations": [{{"evidence_id": "E1", "quote": "被引用的原文片段",
                  "doc_id": "DOC-1", "version": "v1"}}],
  "gaps": ["知识库没有覆盖、因此没有回答的部分"],
  "used_evidence_ids": ["E1"]
}}"""


# ══════════════ 证据不足时的降级回答 ══════════════
KB_LIMIT_SYSTEM = BASE_RULES + """

【任务】证据不足以支撑完整回答时，给出"缩小范围但不编造"的回答。

【硬性要求】
- 只讲证据能支撑的部分，明确说明哪些内容目前没有可靠资料。
- 不要用常识补充，不要用"一般来说"来兜底。
- 结尾必须给出下一步建议（面诊评估，或转人工咨询）。
"""

KB_LIMIT_USER = """用户问题：{user_input}
<evidence>
{evidence}
</evidence>
未覆盖的问题：{missing}

输出 JSON：
{{"content": "缩小范围的回答 + 未知说明 + 面诊建议",
  "citations": [], "gaps": ["未覆盖的部分"], "used_evidence_ids": []}}"""


# ══════════════ 事实与引用核对 ══════════════
KB_VERIFY_SYSTEM = BASE_RULES + """

【任务】逐句核对草稿：每一句事实性表述是否被它标注的 evidence 支持。

【判定标准】只有这三个值：
- supported   ：标注的 evidence 原文能支撑该句（措辞不同不算问题；证据原文是
                "多数人维持 6–12 个月"，草稿写"通常可维持半年以上"，属于 supported）。
- unsupported ：**找不到依据**，或标注的 evidence 与句子内容无关，或该句明显是
                凭常识补充的。这是最严重的一类。
- overstated  ：**有依据，但表述被夸大或绝对化**（承诺效果、写死具体时长、
                去掉了"因人而异""需面诊评估"）。

【最容易判错的地方，务必注意】
- 草稿"写得不够详细""没有提到某个方面"**不是** unsupported —— 那是缺内容，
  不是编内容。不要因为"我觉得还应该再讲点什么"就把句子判成 unsupported。
  这种意见写进 must_fix 即可。
- 不要因为证据原文的措辞与草稿不同就判 unsupported，看的是**语义是否被支撑**。

【必须守住的一条】上面两条是为了避免误判，**不是**让你放过真正的问题：
句子的事实内容确实在证据里找不到 → 就是 unsupported，不要因为"判了它会触发重写"
就降格成 overstated。**给没有依据的内容放行，比多改一版稿子严重得多**。
不确定时的正确做法是保持判定、把理由写清楚，而不是往轻的那一档挪。

【边界】你只输出判定与证据，不负责改写草稿。
"""

KB_VERIFY_USER = """<draft>
{draft}
</draft>
<evidence>
{evidence}
</evidence>

输出 JSON：
{{
  "claims": [
    {{"sentence": "…", "cited": "E1", "status": "supported",
      "quote": "证据原文片段", "comment": "不通过时的具体原因"}}
  ],
  "all_grounded": false,
  "must_fix": ["把『能维持一年』改为『多数人 6–12 个月，因人而异』"]
}}"""


# ══════════════ 证据冲突 / 高影响时的二次判断 ══════════════
EVIDENCE_SECOND_SYSTEM = BASE_RULES + """

【任务】判断给定证据是否足以回答用户问题，以及证据之间是否互相矛盾。

【要求】只判断证据的充分性与一致性，不补充新事实、不生成回答。
"""

EVIDENCE_SECOND_USER = """用户问题：{user_input}
<evidence>
{evidence}
</evidence>

输出 JSON：{{"verdict": "sufficient", "reason": ""}}"""

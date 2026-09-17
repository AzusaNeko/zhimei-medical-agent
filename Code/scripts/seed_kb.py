"""
演示数据播种：把知识库、门店、医生、项目、档期、预约、用户与画像灌进 PG 与 Milvus。

用法（在 Code 目录下，真实档位）：
    python scripts/seed_kb.py                # 幂等：重复执行只会 upsert，不会重复插入

它在做什么：
  1. 用 BGE-M3 把 kb_chunk 文本编码成 dense + sparse，写入 Milvus（建集合 + upsert）
  2. 把文档元数据写入 PG 的 kb_document / kb_chunk / kb_chunk_vector
  3. 灌门店、医生、资质、项目、档期、预约、用户画像（MVP 的本地模拟业务表）

为什么 kb_document.status 必须是 approved：
  检索的过滤条件写死了 doc_status == "approved" 且未过期 ——
  演示数据如果不设成 approved，检索会一条都查不到，看起来像"检索坏了"。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ★ 先导入 settings（触发 .env 加载，把 HF_ENDPOINT 写进环境变量），再导入其它模块。
#   顺序反了的话 huggingface_hub 已经按默认地址初始化完毕，镜像配置就白设了。
from app.settings import Settings  # noqa: E402
from app.services import auth  # noqa: E402

from app.services.encoder import BgeM3Encoder  # noqa: E402
from app.services.milvus_store import MilvusHybridStore  # noqa: E402
from app.services.pg import PgStore  # noqa: E402

CST = timezone(timedelta(hours=8))

# ── 演示登录账号 ──
DEMO_EMAIL = "demo@zhimei.test"
DEMO_PASSWORD = "zhimei-demo-2026"
#: 四个角色各一个，方便演示"同一张工单、不同角色看到的脱敏结果不同"
AGENT_ACCOUNTS = [
    ("service@zhimei.test", "客服小美", "service"),
    ("doctor@zhimei.test", "张医生", "doctor"),
    ("compliance@zhimei.test", "合规专员", "compliance"),
    ("admin@zhimei.test", "系统管理员", "admin"),
]

# ══════════════════════════════════════════════════════════════
#  演示知识库
# ══════════════════════════════════════════════════════════════
#
# ★ 结构：一篇文档 = 多个 chunk（`sections` 里每一项切一块）。
#
#   原来每篇只有**一个 chunk**（整篇当一块），后果是检索只能"整篇召回"：
#   一篇讲了原理又讲了恢复期的文档，无论问哪一半都会把整篇塞进证据里 ——
#   引用会指向一大段与问题无关的文字，而"逐句核对"也就失去了粒度。
#   chunk 是**检索与引用的单位**，它的粒度决定了引用能不能精准。
#
# ★ 写法约束（写内容时必须遵守，违反会被硬规则拦在出站前）：
#   · 只写一般性科普，**不承诺疗效、不用绝对化用语、不报价格**；
#   · 涉及个人适用性一律落到"需医生面诊评估"；
#   · 每段尽量**自包含**：它会被单独检索出来、单独被引用，
#     所以不要写"如上所述""前文提到"。
#
# ⚠️ 以下是**演示用示例内容**（依据公开的通用医学常识整理），
#    上线前必须由医学顾问与法务逐条审定，并替换为机构真实资料。
KB = [
    {
        "doc_id": "DOC-01", "title": "热玛吉（射频紧致）：原理、适用与恢复期",
        "project": "热玛吉", "doc_type": "项目资料",
        "sections": [
            "热玛吉属于单极射频类项目，通过射频能量在真皮层产生热作用，"
            "用于改善皮肤松弛与轮廓紧致度。它作用的是皮肤层次本身，不是肌肉或脂肪。",
            "适用方向通常是面部与颈部轻中度松弛、下颌轮廓不清晰、皮肤质地粗糙。"
            "是否适合需要医生面诊，结合皮肤厚度、松弛程度与既往治疗史判断。",
            "不适用或需谨慎的情况包括：治疗区域有活动性感染或开放性伤口、"
            "体内有心脏起搏器等植入式电子设备、妊娠或哺乳期、"
            "治疗区域近期做过其他填充类项目。以上均需由医生评估后决定。",
            "治疗过程通常需要外敷麻药，单次约 60–90 分钟。"
            "治疗中会有温热与短暂的刺痛感，具体强度与能量参数、个人耐受有关，"
            "能量参数由医生根据皮肤状态设定，不追求“越痛越有效”。",
            "治疗后常见反应是局部泛红、轻微肿胀与紧绷感，多在数天内逐渐缓解。"
            "恢复期一般 3–7 天，因人而异；期间建议温和清洁、加强保湿与防晒，"
            "避免高温环境（桑拿、高温瑜伽）与剧烈运动。",
            "效果呈现与维持时间存在个体差异，与皮肤基础、生活习惯、"
            "是否按疗程进行都有关系；通常需要一段时间逐步显现。"
            "具体能达到什么程度、能维持多久，需面诊评估，本资料不作承诺。",
            "术后如出现持续加重的疼痛、明显水疱、皮肤破溃或颜色异常改变，"
            "不属于常见反应，应及时联系操作机构或前往正规医疗机构就诊。",
        ],
    },
    {
        "doc_id": "DOC-02", "title": "超声炮（聚焦超声提升）：原理、适用与注意事项",
        "project": "超声炮", "doc_type": "项目资料",
        "sections": [
            "超声炮利用聚焦超声，把能量集中在更深的筋膜层（SMAS 层），"
            "产生热凝固作用，用于面部与下颌轮廓的提升。"
            "它与射频类项目的**能量作用层次不同**，因此适合的方向也不同。",
            "常见适用方向是下颌缘轮廓不清晰、面颊轻度下垂、"
            "希望在不做手术的前提下改善轮廓。是否适合需面诊评估。",
            "与热玛吉的区别可以这样理解：热玛吉作用层次相对浅一些，"
            "侧重皮肤紧致与质地；超声炮作用层次更深，侧重轮廓提升。"
            "两者作用机制与层次不同，不能简单说哪个“更好”，"
            "需要医生根据松弛的类型（皮肤松弛 vs 轮廓下垂）来判断。",
            "治疗后可能出现局部酸胀、轻微肿胀与触痛，多在数天到两周内缓解；"
            "少数人会有短暂的局部麻木感。恢复期因人而异。",
            "操作对**层次与能量的控制要求较高**，必须由具备资质的医师操作。"
            "治疗前应如实告知既往手术史、填充史与用药史（尤其抗凝药物），"
            "由医生评估是否存在禁忌。",
            "术后一周内建议避免高温环境与剧烈运动，避免用力按摩治疗区域；"
            "如出现明显不对称、持续剧痛或皮肤异常改变，应及时就医。",
        ],
    },
    {
        "doc_id": "DOC-03", "title": "水光针（注射补水）：原理、护理与风险",
        "project": "水光针", "doc_type": "项目资料",
        "sections": [
            "水光针属于注射类项目，通过多点微量注射把透明质酸等成分送入真皮浅层，"
            "用于改善皮肤干燥与光泽度。它是有创操作，皮肤表面会留下针孔。",
            "常用成分以透明质酸为主，实际配方由医生根据皮肤状态决定。"
            "不同机构的配方存在差异，注射前应明确了解所用产品与来源。",
            "术后 24 小时内避免化妆、避免用手触摸注射区域，"
            "注意补水与防晒；一周内避免高温环境、剧烈运动与饮酒，"
            "以免加重红肿或影响恢复。",
            "常见反应是注射点少量出血、局部红肿与淤青，多在数天内缓解。"
            "少数人会出现短暂的不平整或干燥，通常会逐步改善；"
            "恢复时间存在个体差异。",
            "需谨慎或暂缓的情况包括：注射区域有感染或炎症、"
            "对成分过敏、妊娠或哺乳期、正在使用抗凝药物。"
            "有过敏史（尤其对麻醉药或透明质酸类产品）必须提前告知医生。",
            "顾客常问：打完水光针脸又红又肿、还发烧，是不是发炎了？"
            "如出现持续加重的红肿、明显疼痛、发热、皮疹或异常分泌物（化脓、流脓），"
            "应尽快联系操作机构或到正规医疗机构就诊，不要自行处理。",
        ],
    },
    {
        "doc_id": "DOC-04", "title": "光子嫩肤（强脉冲光）：原理与疗程",
        "project": "光子嫩肤", "doc_type": "项目资料",
        "sections": [
            "光子嫩肤使用强脉冲光（IPL），属于宽谱光，作用于皮肤表浅层，"
            "用于改善肤色不均、泛红与浅表色斑。它与其他激光项目的波长与作用深度不同。",
            "常见适用方向是肤色暗沉、日晒导致的色素沉着、毛细血管扩张引起的泛红。"
            "是否适合与肤质、近期日晒情况有关，需面诊评估。",
            "通常按疗程进行，多次治疗、间隔数周，具体次数由医生根据反应决定。"
            "单次治疗时间较短，多数人无需恢复期。",
            "治疗后可能出现短暂泛红与轻微灼热感，通常数小时内缓解；"
            "少数人会出现色素暂时加深（顾客常说的“反黑”），多可自行消退，"
            "但需要时间。**治疗后严格防晒是关键**，否则色沉风险上升。",
            "近期有明显日晒、正在口服光敏性药物（如某些抗生素、异维A酸类）、"
            "妊娠期、治疗区域有感染或活动性皮肤病的，需先由医生评估。",
        ],
    },
    {
        "doc_id": "DOC-05", "title": "皮秒激光：原理、适用与色沉风险",
        "project": "皮秒", "doc_type": "项目资料",
        "sections": [
            "皮秒激光以极短脉宽输出能量，通过光机械效应击碎色素颗粒，"
            "用于改善色素性问题（如部分类型的色斑、纹身）。"
            "与光子嫩肤的宽谱光不同，它是单一波长的激光。",
            "适用方向需由医生根据色斑的**类型**判断 —— 色素性问题种类很多，"
            "不同类型对治疗的反应差别很大，有些类型甚至不适合激光治疗。"
            "因此不建议把“祛斑”当成一个笼统需求来处理。",
            "治疗后会有短暂的红肿与灼热感，部分人会出现结痂，通常数天内脱落，"
            "**不要自行抠掉**。恢复期与能量、治疗范围有关。",
            "主要风险是**炎症后色素沉着（反黑）**，与肤质、能量设置、"
            "术后防晒都有关；肤色较深的人群风险相对更高。"
            "此外还存在色素减退、水疱与瘢痕的风险，虽不常见但需知晓。",
            "治疗后必须严格防晒（物理遮挡 + 防晒霜），并按医嘱使用修护类产品；"
            "避免使用刺激性护肤品与去角质。出现水疱、持续疼痛或异常色沉应及时复诊。",
        ],
    },
    {
        "doc_id": "DOC-06", "title": "线雕（埋线提升）：原理、适用与风险",
        "project": "线雕", "doc_type": "项目资料",
        "sections": [
            "线雕是通过在皮下埋置可吸收线材，利用线的提拉与刺激作用"
            "改善面部松弛。它属于**有创操作**，需要在无菌条件下由医师完成。",
            "常见适用方向是面部轻中度下垂、下颌轮廓不清晰。"
            "松弛程度较重时，医生可能建议其他方式，需面诊评估。",
            "术后常见反应包括肿胀、淤青、局部牵拉感与轻微疼痛，"
            "多在 1–2 周内逐步缓解；部分人会有短暂的做表情时牵拉不适。"
            "恢复期因人而异。",
            "需要知晓的风险包括：感染、线头外露或可触及、局部凹凸不平、"
            "两侧不对称、血肿，以及少数情况下的线体移位。"
            "这些与操作技术、线材选择与术后护理都有关。",
            "顾客常问：做完线雕脸肿、摸到线头，正常吗？"
            "术后两周内避免夸张表情、用力揉搓与面部按摩，避免高温环境与剧烈运动；"
            "按医嘱护理创口。如出现明显红肿发热、剧烈疼痛、皮肤颜色改变或线头外露，"
            "应及时联系操作机构复诊。",
            "有凝血功能异常、正在使用抗凝药物、瘢痕体质、"
            "治疗区域有感染或炎症的，需在术前由医生评估是否适合。",
        ],
    },
    {
        "doc_id": "DOC-07", "title": "瘦脸针（A 型肉毒毒素）：原理、禁忌与注意事项",
        "project": "瘦脸针", "doc_type": "项目资料",
        "sections": [
            "瘦脸针通常指 A 型肉毒毒素注射，通过作用于肌肉使其暂时性放松，"
            "用于改善咬肌肥大导致的面下部宽大。它作用的是肌肉，不是脂肪或骨骼。",
            "因此它只对**咬肌型**的面部宽大有效。如果是脂肪堆积或骨骼原因，"
            "效果与预期会不一致 —— 这需要医生通过面诊与触诊判断类型，"
            "不建议仅凭照片或自我判断决定。",
            "效果是**暂时**的，通常需要定期重复注射。"
            "维持时间存在个体差异，与剂量、肌肉状态与个人代谢有关，本资料不作承诺。",
            "**明确的禁忌与需谨慎的情况**：妊娠或哺乳期、"
            "神经肌肉疾病（如重症肌无力）、注射区域有感染、"
            "对制剂成分过敏、正在使用某些影响神经肌肉传导的药物。"
            "以上必须由医生评估，不可自行判断。",
            "注射后常见反应是局部酸胀、轻微肿胀与淤青，多在数天内缓解。"
            "短期内避免按摩、揉搓注射区域，避免高温环境与剧烈运动，"
            "以免药物扩散到非目标肌肉。",
            "少数情况下可能出现表情不对称、笑容不自然或局部凹陷，"
            "多与药物扩散有关，通常会随时间缓解。"
            "如出现吞咽困难、视物异常、呼吸费力或全身无力，"
            "属于需要**立即就医**的情况，不要等待观察。",
        ],
    },
    {
        "doc_id": "DOC-08", "title": "玻尿酸填充：原理、血管风险与应对",
        "project": "玻尿酸填充", "doc_type": "项目资料",
        "sections": [
            "玻尿酸（透明质酸）填充是通过注射补充容积，用于改善凹陷、"
            "轮廓塑形或唇部形态。它属于注射类有创操作，效果通常是可逆的"
            "（可用透明质酸酶部分降解，但需由医生判断）。",
            "不同部位的注射风险差别很大：鼻部、眉间、额头、太阳穴等区域"
            "血管分布密集，属于**高风险区域**；唇部与颊部相对常规但仍需谨慎。",
            "**最重要的风险是血管栓塞**：填充物误入血管可能造成局部皮肤坏死，"
            "罕见情况下可导致视力损害。这类并发症与操作者的解剖经验、"
            "注射技术直接相关，因此必须由具备资质的医师在有抢救条件的机构完成。",
            "顾客常问：打完玻尿酸突然特别疼、皮肤发白、看东西不清楚，能先观察吗？"
            "出现下列情况属于**紧急情况，必须立即就医**，不要等待观察："
            "注射区域剧烈疼痛、皮肤发白或花斑样改变、视力模糊或视野缺损、"
            "眼周或额部皮肤颜色异常。越早处理，后果越轻。",
            "术后常见反应是局部肿胀、淤青与触痛，多在数天到两周内缓解；"
            "注射后一周内避免高温环境、剧烈运动与用力按压注射区域。"
            "怀孕哺乳期、注射区域感染、凝血异常、对成分过敏者需术前评估。",
            "注射前应确认产品来源与批号、是否可在国内合法使用，"
            "并要求查看医师的执业资质。**不要在非医疗机构接受注射类项目。**",
        ],
    },
    {
        "doc_id": "DOC-09", "title": "术前准备与面诊流程（通用）",
        "project": None, "doc_type": "通用指引",
        "sections": [
            "任何医美项目的第一步都是**面诊**，而不是直接约治疗时间。"
            "面诊要解决的问题是：你的诉求属于哪一类问题、哪种方式适合、"
            "以及有没有不适合做的情况。",
            "面诊时应主动告知的信息包括：既往医美治疗史（尤其是近期做过的项目）、"
            "慢性病史、过敏史（含药物与麻醉药过敏）、正在服用的药物"
            "（特别是抗凝药、光敏性药物）、是否妊娠或哺乳。",
            "术前的通用准备：治疗前一周避免饮酒；"
            "如有服用抗凝或活血类药物需先咨询医生是否需要调整（**不要自行停药**）；"
            "治疗当天不要化妆，保持治疗区域清洁；避免在治疗前暴晒。",
            "有权在治疗前了解：使用的是什么产品与设备、产品的合法来源与批号、"
            "操作医师的执业资质、可能的反应与风险、以及出现问题时的处理流程。"
            "这些都是合理的提问，正规机构不会回避。",
            "如果对方案有疑虑、或沟通中感到被催促决定，可以暂缓。"
            "医美项目绝大多数不是紧急需求，没有必须当天决定的理由。",
        ],
    },
    {
        "doc_id": "DOC-10", "title": "通用风险提示与需要立即就医的信号（通用）",
        "project": None, "doc_type": "风险提示",
        "sections": [
            "任何医美项目都存在个体差异与不确定性。同样的方案与操作，"
            "不同人的反应与恢复过程可能不同。本资料提供的是通用信息，"
            "不构成诊断或治疗建议，具体方案与风险需由医生面诊评估后确定。",
            "常见的、通常可自行缓解的反应包括：局部红肿、淤青、"
            "轻微疼痛与紧绷感、短暂的感觉异常。这些多在数天内逐步缓解。",
            "顾客常问：肿得厉害、还一直发烧，是不是感染了？伤口流脓、"
            "起水疱、皮肤发白要不要紧？这类“是不是出问题了”的判断依据是——"
            "下列情况**不属于**常见反应，应及时联系操作机构或前往正规医疗机构就诊："
            "持续加重的疼痛、明显水疱或皮肤破溃、皮肤颜色异常改变"
            "（发白、发紫、花斑样）、发热伴局部红肿、异常分泌物（化脓、流脓）。",
            "顾客常问：打完针眼睛看不清、疼得受不了，要不要马上跑医院？"
            "答案是不要等、不要观察。下列情况属于**需要立即就医**的紧急信号："
            "注射后局部剧烈疼痛伴皮肤发白、视力模糊或视野缺损、"
            "吞咽困难或呼吸费力、意识改变。玻璃酸类注射后的视力异常"
            "尤其紧急，越早处理越有可能挽回。",
            "就医时请把这几件事说清楚：做的是什么项目、什么时候做的、"
            "用了什么产品（如有批号更好）、目前有哪些症状、"
            "症状是什么时候开始出现的。这些信息直接影响处理方式。",
            "不要因为“刚做完项目不好意思问”而拖延。术后异常反应属于医疗问题，"
            "联系操作机构或就医是正常且必要的流程。",
        ],
    },
    {
        "doc_id": "DOC-11", "title": "术后护理总则（通用）",
        "project": None, "doc_type": "护理指南",
        "sections": [
            "术后护理的通用原则是：**减少刺激、加强保护、按医嘱执行**。"
            "具体到某个项目会有差异，应以操作机构的书面医嘱为准，"
            "本资料给出的是一般性建议。",
            "皮肤类项目（光电、注射）术后通常需要：温和清洁（避免磨砂与去角质）、"
            "充分保湿、严格防晒；避免使用含酸类、高浓度维A醇等刺激性成分的产品，"
            "直到医生确认可以恢复。",
            "顾客常问：做完能去汗蒸、泡温泉吗？多久能运动、能喝酒？"
            "术后短期内（通常一周左右，视项目而定）建议避免："
            "高温环境（桑拿、蒸汽、高温瑜伽）、剧烈运动、饮酒、"
            "用力揉搓或按摩治疗区域。这些都可能加重红肿或影响恢复。",
            "防晒是多数项目的术后重点：优先物理遮挡（帽子、口罩、伞），"
            "配合使用防晒霜并及时补涂。日晒可能加重色素沉着，"
            "尤其对光子、激光类项目后的皮肤。",
            "如医生开了外用药或修护产品，按**医嘱的用法用量**使用，"
            "不要自行加量或叠加多种产品。出现不适先停用并咨询医生。",
        ],
    },
    {
        "doc_id": "DOC-12", "title": "门店与医生资质：怎么核实（通用）",
        "project": None, "doc_type": "机构信息",
        "sections": [
            "开展医疗美容服务的机构必须持有《医疗机构执业许可证》，"
            "诊疗科目中应包含医疗美容相关科目。生活美容机构不得开展注射、"
            "激光等医疗美容项目 —— 这是判断“能不能在这做”的第一道线。",
            "操作医师应持有《医师执业证书》，且执业范围与所做的项目相符。"
            "可以通过国家卫生健康委员会官网的医师执业注册信息查询入口，"
            "按姓名与机构进行核实。",
            "使用的注射类产品、设备应可追溯：正规产品有批准文号与批号，"
            "可以要求查看外包装与产品信息。来路不明的产品是主要风险来源之一。",
            "面诊时可以合理询问：由哪位医师操作、他的执业资质、"
            "该项目在本机构由谁负责、出现并发症时的处理预案与转诊通道。"
            "正规机构会正面回答这些问题。",
            "本机构可提供门店地址、联系电话与接诊医师的执业信息，"
            "具体以到店公示与官方查询结果为准。",
        ],
    },
    {
        "doc_id": "DOC-13", "title": "项目对比：热玛吉 / 超声炮 / 线雕怎么选（通用）",
        "project": None, "doc_type": "对比说明",
        "sections": [
            "这三个项目常被拿来比较，但它们**作用的层次和机制不同**，"
            "所以不是“谁比谁好”，而是“各自解决哪一类问题”。",
            "热玛吉（单极射频）：能量主要作用于真皮层，"
            "侧重**皮肤紧致与质地**改善，适合皮肤松弛为主的情况。",
            "超声炮（聚焦超声）：能量聚焦在更深的筋膜层，"
            "侧重**轮廓提升**，适合面颊下垂、下颌轮廓不清晰为主的情况。",
            "线雕（埋线提升）：通过埋置线材做**物理提拉**，"
            "属于有创操作，适合希望提拉效果更直接、且能接受恢复期与相关风险的情况。"
            "它的风险谱与另外两个不同，术前沟通要更充分。",
            "选择的关键不是“哪个效果最强”，而是判断你的松弛属于哪一类："
            "皮肤松弛、轮廓下垂，还是两者兼有。这需要医生面诊结合触诊判断，"
            "仅凭照片或自我描述容易判断偏差。",
            "另外要考虑的因素：能接受的恢复期、既往治疗史与填充史、"
            "是否有禁忌情况。有些人会被告知需要联合方案，也有人被告知"
            "暂时不适合做任何一项 —— 后者同样是负责任的结论。",
        ],
    },
    {
        "doc_id": "DOC-14", "title": "疗程与费用：为什么无法在咨询阶段报价（通用）",
        "project": None, "doc_type": "通用指引",
        "sections": [
            "医美项目的费用通常**无法在不面诊的情况下给出准确数字**，"
            "原因不是“不想说”，而是价格与几个只有面诊才能确定的因素直接相关。",
            "影响价格的主要因素包括：所需的产品或耗材数量"
            "（如注射类按支数、光电类按发数或治疗范围）、"
            "采用的具体设备与产品型号、治疗部位与面积、"
            "是否联合其他项目、以及由哪位医师操作。",
            "因此正规的做法是：面诊评估 → 给出方案 → 按方案报价 → "
            "费用明细写入知情同意与收费单据。如果对方在没见到你的情况下"
            "就能报出一个精确数字，那个数字通常不是针对你的情况的。",
            "需要留意的收费相关风险：低价引流后现场加项、"
            "以“进口/升级”名义临时加价、"
            "把必须分次进行的项目按一次性打包报价。"
            "遇到这些情况可以要求书面明细并暂缓决定。",
            "本机构的具体价格以到院面诊后的方案报价与收费公示为准，"
            "不在咨询阶段给出确定金额。",
        ],
    },
    {
        "doc_id": "DOC-15", "title": "常见问题速查（通用）",
        "project": None, "doc_type": "常见问题",
        "sections": [
            "问：做完项目多久能看到效果？"
            "答：因项目而异。注射类通常较快，光电类往往需要一段时间逐步显现，"
            "部分项目需要按疗程进行。具体时间与个人情况有关，需面诊评估，"
            "本资料不作承诺。",
            "问：效果能维持多久？"
            "答：与项目类型、个人代谢、生活习惯与是否按疗程进行都有关，"
            "个体差异明显。肉毒毒素类属于暂时性效果，需定期重复；"
            "填充类与光电类的维持时间也各不相同。",
            "问：疼不疼？"
            "答：多数项目会在治疗前使用表面麻醉，过程中有不同程度的热感或刺痛感，"
            "与能量参数和个人耐受有关。医生会根据反应调整参数，"
            "不建议把“忍着更疼”当成有效标准。",
            "问：做完能马上上班吗？"
            "答：取决于项目。多数光电类项目仅有短暂泛红，通常不影响日常；"
            "注射类可能有肿胀与淤青；线雕等有创项目需要更长的恢复时间。"
            "建议在面诊时按自己的日程安排与医生沟通。",
            "问：可以自己在家做类似护理吗？"
            "答：**医疗美容项目必须由有资质的医师在医疗机构内完成**，"
            "包括注射、激光、射频等。生活美容与医疗美容的界限不能模糊，"
            "自行操作或在不具备资质的地方接受操作，风险显著更高。",
        ],
    },
]

STORES = [("S-01", "浦东店", "上海", "上海市浦东新区示例路 1 号", "021-0000-0001"),
          ("S-02", "静安店", "上海", "上海市静安区示例路 2 号", "021-0000-0002"),
          ("S-03", "徐汇店", "上海", "上海市徐汇区示例路 3 号", "021-0000-0003")]

DOCTORS = [("D-01", "张医生", "主治医师", "S-01"),
           ("D-02", "李医生", "副主任医师", "S-02"),
           ("D-03", "王医生", "主治医师", "S-01")]

PROJECTS = [("P-01", "热玛吉", "射频紧致"), ("P-02", "超声炮", "超声提升"),
            ("P-03", "水光针", "注射补水"), ("P-04", "光子嫩肤", "光电类")]


async def main() -> int:
    parser = argparse.ArgumentParser(description="灌演示数据（幂等，可重复执行）")
    parser.add_argument("--recreate", action="store_true",
                        help="先删掉 Milvus 集合再重建（改了 schema 时用，会清空向量）")
    args = parser.parse_args()

    settings = Settings()
    # 灌数据只需要 PG + Milvus + BGE，不需要大模型 —— 别因为还没配 API key 就卡住
    settings.validate(require_llm=False)
    print(f"连接 Postgres：{settings.pg_dsn.split('@')[-1]}")
    print(f"连接 Milvus  ：{settings.milvus_uri}")

    pg = PgStore(settings.pg_dsn)
    await pg.connect()
    store = MilvusHybridStore(settings.milvus_uri, settings.milvus_collection,
                              token=settings.milvus_token)
    store.ensure_collection(recreate=args.recreate)
    encoder = BgeM3Encoder(settings.bge_m3_path, device=settings.embed_device,
                           concurrency=settings.embed_concurrency)
    try:
        # ══════════ 业务模拟表 ══════════
        await _seed_business(pg)
        # ══════════ 知识库 ══════════
        n_docs, n_chunks = await _seed_kb(pg, store, encoder, settings)
        print(f"\n✓ 完成：知识库 {n_docs} 篇 / {n_chunks} 个 chunk（已写入 Milvus）、"
              f"门店 {len(STORES)}、医生 {len(DOCTORS)}、项目 {len(PROJECTS)}")
        print("  现在可以跑：python -m app.cli -t \"热玛吉和超声炮有什么区别\"")
        print("  或验证检索链路：python scripts/check_retrieval.py")
    finally:
        await encoder.close()
        store.close()
        await pg.close()
    return 0


async def _seed_business(pg: PgStore) -> None:
    async with pg.pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO app.store (store_id, name, city, address, phone) VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (store_id) DO UPDATE SET name=EXCLUDED.name", STORES)
        await conn.executemany(
            "INSERT INTO app.doctor (doctor_id, name, title, store_id) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (doctor_id) DO UPDATE SET name=EXCLUDED.name", DOCTORS)
        await conn.executemany(
            """INSERT INTO app.doctor_credential (credential_id, doctor_id, kind, cert_no, issued_by, status)
               VALUES ($1,$2,'执业医师资格',$3,'示例卫健委','valid')
               ON CONFLICT (credential_id) DO NOTHING""",
            [(f"C-{d[0]}", d[0], f"1100000000{d[0]}") for d in DOCTORS])
        await conn.executemany(
            "INSERT INTO app.project (project_id, name, category) VALUES ($1,$2,$3) "
            "ON CONFLICT (project_id) DO UPDATE SET name=EXCLUDED.name", PROJECTS)

        # 档期：从明天起 7 天，每天 10:00 / 14:00 / 16:00
        slots = []
        base = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
        for d in range(1, 8):
            for h in (10, 14, 16):
                start = base + timedelta(days=d, hours=h)
                slots.append(("D-01", "S-01", start, start + timedelta(hours=1)))
        await conn.executemany(
            """INSERT INTO app.schedule_slot (doctor_id, store_id, start_at, end_at, capacity, booked)
               SELECT $1,$2,$3,$4,1,0
               WHERE NOT EXISTS (SELECT 1 FROM app.schedule_slot
                                 WHERE doctor_id=$1 AND store_id=$2 AND start_at=$3)""",
            slots)

        # 用户 / 身份 / 授权 / 画像
        #
        # ★★ 演示用户必须是**固定 UUID**，不能用 gen_random_uuid() ★★
        #
        #   原来的写法是 `INSERT ... ON CONFLICT DO NOTHING RETURNING user_id`，
        #   看起来是"有就复用、没有就建"。但 app_user 的主键是
        #   `user_id UUID DEFAULT gen_random_uuid()` —— **每次插入都是一个新 UUID，
        #   永远不会冲突**，所以那句 ON CONFLICT 形同虚设：**每跑一次种子就多一个演示用户**。
        #
        #   它带来的连锁反应很难查：演示预约那行的主键是固定的，而它的
        #   `DO UPDATE` 里没有更新 user_id，于是预约永远绑在**第一次**那个用户上。
        #   之后再跑种子，打印出来的新 user_id 名下根本没有预约 ——
        #   改约场景会走"查不到可改约的预约"的诚实降级，看上去像功能坏了。
        #
        #   固定下来还有额外好处：文档、脚本、肌肉记忆里的 user_id 不再漂移。
        DEMO_USER_ID = "22222222-2222-2222-2222-222222222222"
        user_id = await conn.fetchval(
            """INSERT INTO app.app_user
                 (user_id, display_name, gender, birth_year,
                  email, password_hash, email_verified)
               VALUES ($1, '演示用户', 2, 1993, $2, $3, true)
               ON CONFLICT (user_id) DO UPDATE
               SET display_name = EXCLUDED.display_name,
                   email = EXCLUDED.email,
                   password_hash = EXCLUDED.password_hash,
                   email_verified = true
               RETURNING user_id""",
            DEMO_USER_ID, DEMO_EMAIL, auth.hash_password(DEMO_PASSWORD))
        await conn.execute(
            """INSERT INTO app.user_identity (user_id, channel, external_id, verified)
               VALUES ($1,'demo','demo-user-0001',true) ON CONFLICT DO NOTHING""", user_id)
        await conn.execute(
            """INSERT INTO app.user_consent (user_id, scope, purpose, granted)
               VALUES ($1,'profile','service',true) ON CONFLICT DO NOTHING""", user_id)
        await conn.execute(
            """INSERT INTO app.user_profile (user_id, summary, preferences)
               VALUES ($1,'女性，关注面部紧致；曾在浦东店做过水光针。','{"store":"浦东店"}')
               ON CONFLICT (user_id) DO NOTHING""", user_id)

        # 一条演示预约（归属该用户）
        slot_id = await conn.fetchval(
            "SELECT slot_id FROM app.schedule_slot ORDER BY start_at LIMIT 1")
        # ★ ON CONFLICT 要 DO UPDATE（而不是 DO NOTHING）：
        #   改约一旦真的执行过，这行的 status 就变成 'changed'，而改约只能作用于
        #   status='booked' 的预约 —— 用 DO NOTHING 的话，【演示只能成功跑一次】，
        #   第二次开始就永远"查不到可改约的预约"，而现象看起来像功能坏了。
        #   version 不动：它由改约自己 +1，是乐观锁的基准，重置反而会掩盖并发问题。
        # ★ user_id 也必须一起更新：预约的主键是固定的，而演示用户以前每跑一次种子
        #   就换一个 UUID，导致这行一直绑在最早那个用户上。现在用户 id 固定了，
        #   这条 UPDATE 是为了把历史上绑错的行纠正回来（幂等，跑几次都一样）。
        await conn.execute(
            """INSERT INTO app.appointment (appointment_id, user_id, slot_id, project_id, status, fee_cents)
               VALUES ('11111111-1111-1111-1111-111111111111',$1,$2,'P-01','booked',8000)
               ON CONFLICT (appointment_id) DO UPDATE
               SET status='booked', slot_id=EXCLUDED.slot_id,
                   user_id=EXCLUDED.user_id, updated_at=now()""", user_id, slot_id)
        print("✓ 业务模拟数据已就绪（门店 / 医生 / 资质 / 项目 / 档期 / 用户 / 画像 / 预约）")
        print(f"  ★ 演示用户 user_id = {user_id}")
        print(f"    真实档位跑 CLI 时请带上： python -m app.cli --demo --user {user_id}")
        print("    （不绑用户 → 会话 auth.verified=false → 预约类请求会被判 need_info）")

        # ── 登录账号 ──
        # ★ 演示口令写死在种子里，因为它们**本来就是公开的演示账号**。
        #   真上线时必须删掉这些账号（或改成部署时随机生成并只打印一次）。
        #   口令本身够长（≥10 位）—— 密码强度靠长度，不靠复杂度。
        for email, name, role in AGENT_ACCOUNTS:
            await conn.execute(
                """INSERT INTO ops.agent_user (agent_id, name, role, email, password_hash)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (agent_id) DO UPDATE
                   SET name = EXCLUDED.name, role = EXCLUDED.role,
                       email = EXCLUDED.email, password_hash = EXCLUDED.password_hash""",
                f"agent-{role}", name, role, email, auth.hash_password(DEMO_PASSWORD))
        print("\n✓ 登录账号已就绪：")
        print(f"    顾客端  {DEMO_EMAIL} / {DEMO_PASSWORD}")
        for email, name, role in AGENT_ACCOUNTS:
            print(f"    坐席端  {email} / {DEMO_PASSWORD}   （{name} · {role}）")
        print("    ⚠ 演示口令，上线前必须删除这些账号或改成随机生成")


async def _seed_kb(pg: PgStore, store: MilvusHybridStore, encoder: BgeM3Encoder,
                   settings: Settings) -> tuple[int, int]:
    """把知识库灌进 PG 与 Milvus。返回 (文档数, chunk 数)。

    ★ 一篇文档 = 多个 chunk（`sections` 每项一块）。chunk 是**检索与引用的单位**：
      粒度太粗的话（整篇一块），无论问哪一半都会把整篇塞进证据里，
      "逐句核对引用"也就失去了意义 —— 因为引用指向的是一大段无关文字。

    ★ 幂等靠 `ON CONFLICT (doc_id, seq) DO UPDATE`：`chunk_id` 保持不变，
      于是 Milvus 的主键（= chunk_id）也稳定，重复跑不会堆积重复向量。

    ★ 文档**变短**时要主动清理：只 upsert 新 chunk 不够，
      旧的 seq 行还留在库里与向量集合里，检索会把一段**已经不存在于文档中**
      的文本召回来，还带着 doc_id 出现在引用里 —— 这种"幽灵证据"比检索不到更糟。
    """
    now = int(time.time())
    # 一次把所有 chunk 编码完：逐篇调用会让 BGE 反复进出前向，白等很多次
    flat: list[tuple[str, dict, int]] = []      # (doc_id, doc, seq)
    for doc in KB:
        for seq, text in enumerate(doc["sections"]):
            flat.append((doc["doc_id"], doc, seq))
    texts = [doc["sections"][seq] for _doc_id, doc, seq in flat]
    vecs = await encoder.encode(texts)

    rows = []
    dropped_pks: list[int] = []
    async with pg.pool.acquire() as conn:
        for (doc_id, doc, seq), text, dense, sparse in zip(
                flat, texts, vecs["dense"], vecs["sparse"]):
            # 文档头：每篇只在 seq==0 时写一次
            if seq == 0:
                await conn.execute(
                    """INSERT INTO app.kb_document
                       (doc_id, title, project_id, doc_type, status, version, source,
                        reviewed_by, reviewed_at, effective_at)
                       VALUES ($1,$2,$3,$4,'approved','v1','demo-seed','medical-advisor',now(),now())
                       ON CONFLICT (doc_id) DO UPDATE
                         SET title=EXCLUDED.title, project_id=EXCLUDED.project_id,
                             doc_type=EXCLUDED.doc_type,
                             status='approved', version='v1'""",
                    doc_id, doc["title"], doc["project"], doc["doc_type"])
                # 文档变短时，把多出来的 chunk 连同向量一起清掉
                stale = await conn.fetch(
                    "SELECT chunk_id FROM app.kb_chunk WHERE doc_id=$1 AND seq >= $2",
                    doc_id, len(doc["sections"]))
                stale_ids = [r["chunk_id"] for r in stale]
                if stale_ids:
                    await conn.execute(
                        "DELETE FROM app.kb_chunk_vector WHERE chunk_id = ANY($1::bigint[])",
                        stale_ids)
                    await conn.execute(
                        "DELETE FROM app.kb_chunk WHERE chunk_id = ANY($1::bigint[])",
                        stale_ids)
                    dropped_pks.extend(stale_ids)
            chunk_id = await conn.fetchval(
                """INSERT INTO app.kb_chunk (doc_id, seq, text, token_count)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (doc_id, seq) DO UPDATE SET text=EXCLUDED.text
                   RETURNING chunk_id""", doc_id, seq, text, len(text))
            rows.append({
                # pk 必填（集合是 auto_id=False）：用 chunk_id 当主键，保证重复执行幂等
                "pk": chunk_id,
                "chunk_id": chunk_id, "doc_id": doc_id, "title": doc["title"],
                "project": doc["project"] or "", "doc_type": doc["doc_type"], "version": "v1",
                "doc_status": "approved", "expire_at": 0, "text": text,
                "dense": dense, "sparse": sparse,
            })
            await conn.execute(
                """INSERT INTO app.kb_chunk_vector (chunk_id, embed_model, embed_version, milvus_pk)
                   VALUES ($1,'BAAI/bge-m3','v1',$1)
                   ON CONFLICT (chunk_id, embed_version) DO UPDATE SET milvus_pk=EXCLUDED.milvus_pk""",
                chunk_id)

        # ★ 整篇文档**消失**（改名 / 删除）时，上面"变短清理"救不了它：
        #   循环只遍历当前 KB 里的 doc_id，改名后（DOC-1 → DOC-01）旧文档一行都不会被碰到，
        #   它会永远躺在库里被检索到，并被当成引用来源 —— 用户点开引用发现"这文档我们早不用了"。
        #   所以按来源反查一遍：凡是本脚本灌进去的（source='demo-seed'）却已经不在清单里的，整篇删掉。
        #   只删 source='demo-seed'：人手写的、其它来源的文档一律不碰。
        orphans = await conn.fetch(
            """SELECT doc_id FROM app.kb_document
               WHERE source = 'demo-seed' AND doc_id <> ALL($1::text[])""",
            [doc["doc_id"] for doc in KB])
        for row in orphans:
            gone = [r["chunk_id"] for r in await conn.fetch(
                "SELECT chunk_id FROM app.kb_chunk WHERE doc_id=$1", row["doc_id"])]
            if gone:
                await conn.execute(
                    "DELETE FROM app.kb_chunk_vector WHERE chunk_id = ANY($1::bigint[])", gone)
                await conn.execute(
                    "DELETE FROM app.kb_chunk WHERE chunk_id = ANY($1::bigint[])", gone)
                dropped_pks.extend(gone)
            await conn.execute("DELETE FROM app.kb_document WHERE doc_id=$1", row["doc_id"])
            print(f"  （已下架改名/删除的旧文档 {row['doc_id']}，含 {len(gone)} 个 chunk）")
    if dropped_pks:
        n = store.delete_pks(dropped_pks)
        print(f"  （清理了 {len(dropped_pks)} 个已不存在的 chunk 及其向量，Milvus 实删 {n} 条）")
    store.upsert_chunks(rows)

    # ★★ 对账：以 PG 为权威，把 Milvus 里多出来的向量删掉 ★★
    #   上面两处清理都是"我知道哪些旧 id 该删"——可一旦历史上有过一次没删干净
    #   （改过名、脚本中途报错、手工 import 过），PG 里已经查不到那些 id 了，
    #   再按 id 去删就是"拿擦掉的清单找东西"，永远删不掉。
    #   而残留向量的危害是具体的：混合检索会把它召回、放进证据、生成引用，
    #   用户点开引用却发现知识库里没有这篇 —— 比检索不到更糟。
    #   所以这里反过来问 Milvus"你手里有哪些"（list_pk_docs），减去 PG 里应有的，
    #   差集一律删掉。跑完这一步，两边状态与历史无关，只看当前 KB。
    #
    #   ⚠️ 删的范围**只限 PG 里已彻底不存在的 doc_id**。
    #      不能简单地"pk 不在本次 rows 里就删"：kb_document 表是可以人工录入的，
    #      那些文档不在本脚本的 KB 清单里，却完全合法 —— 那样删就是拿种子脚本
    #      去清别人建的知识库。判断依据必须是"PG 里还有没有这个 doc_id"。
    known_docs = {r["doc_id"] for r in await pg._fetch("SELECT doc_id FROM app.kb_document")}
    valid = {r["pk"] for r in rows}
    ghost = sorted(r["pk"] for r in store.list_pk_docs()
                   if r["pk"] not in valid and r["doc_id"] not in known_docs)
    if ghost:
        n = store.delete_pks(ghost)
        print(f"  （对账：Milvus 中 {len(ghost)} 条所属文档已不存在的向量已删除，实删 {n} 条）")
    return len(KB), len(rows)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

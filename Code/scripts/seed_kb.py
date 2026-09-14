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

from app.services.encoder import BgeM3Encoder  # noqa: E402
from app.services.milvus_store import MilvusHybridStore  # noqa: E402
from app.services.pg import PgStore  # noqa: E402

CST = timezone(timedelta(hours=8))

# ── 演示知识库（每条都会被切成一个 chunk；真实项目应由审核流水线产出）──
KB = [
    ("DOC-1", "热玛吉项目说明", "热玛吉", "项目资料",
     "热玛吉通过射频能量作用于真皮层，帮助改善皮肤紧致度。单次治疗通常 60–90 分钟，"
     "需由医生根据皮肤状态评估治疗参数。"),
    ("DOC-2", "热玛吉恢复期与注意事项", "热玛吉", "护理指南",
     "热玛吉治疗后恢复期通常 3–7 天，可能出现轻微红肿与紧绷感。"
     "效果因人而异，维持时间与个人皮肤状态、生活习惯有关，需面诊评估。"),
    ("DOC-3", "超声炮项目说明", "超声炮", "项目资料",
     "超声炮利用聚焦超声作用于更深的筋膜层，主要用于下颌轮廓与面部提升。"
     "与射频类项目的能量作用层次不同，适合方向也不同。"),
    ("DOC-4", "水光针术后护理", "水光针", "护理指南",
     "水光针术后 24 小时内避免化妆与剧烈运动，注意补水与防晒，"
     "如出现持续红肿或异常疼痛请及时联系医生。"),
    ("DOC-5", "医美项目通用风险提示", None, "风险提示",
     "任何医美项目都存在个体差异，可能出现红肿、淤青等常见反应；"
     "具体方案与风险需由医生面诊评估后确定，本资料不构成诊断或治疗建议。"),
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
        rows = await _seed_kb(pg, store, encoder, settings)
        print(f"\n✓ 完成：知识库 {len(KB)} 篇（{rows} 个 chunk 已写入 Milvus）、"
              f"门店 {len(STORES)}、医生 {len(DOCTORS)}、项目 {len(PROJECTS)}")
        print("  现在可以跑：python -m app.cli -t \"热玛吉和超声炮有什么区别\"")
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
        user_id = await conn.fetchval(
            """INSERT INTO app.app_user (display_name, gender, birth_year)
               VALUES ('演示用户', 2, 1993)
               ON CONFLICT DO NOTHING RETURNING user_id""")
        if user_id is None:
            user_id = await conn.fetchval("SELECT user_id FROM app.app_user LIMIT 1")
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
        await conn.execute(
            """INSERT INTO app.appointment (appointment_id, user_id, slot_id, project_id, status, fee_cents)
               VALUES ('11111111-1111-1111-1111-111111111111',$1,$2,'P-01','booked',8000)
               ON CONFLICT (appointment_id) DO UPDATE
               SET status='booked', slot_id=EXCLUDED.slot_id, updated_at=now()""", user_id, slot_id)
        print("✓ 业务模拟数据已就绪（门店 / 医生 / 资质 / 项目 / 档期 / 用户 / 画像 / 预约）")
        print(f"  ★ 演示用户 user_id = {user_id}")
        print(f"    真实档位跑 CLI 时请带上： python -m app.cli --demo --user {user_id}")
        print("    （不绑用户 → 会话 auth.verified=false → 预约类请求会被判 need_info）")


async def _seed_kb(pg: PgStore, store: MilvusHybridStore, encoder: BgeM3Encoder,
                   settings: Settings) -> int:
    now = int(time.time())
    texts = [k[4] for k in KB]
    vecs = await encoder.encode(texts)
    rows = []
    async with pg.pool.acquire() as conn:
        for (doc_id, title, project, doc_type, text), dense, sparse in zip(
                KB, vecs["dense"], vecs["sparse"]):
            await conn.execute(
                """INSERT INTO app.kb_document
                   (doc_id, title, project_id, doc_type, status, version, source,
                    reviewed_by, reviewed_at, effective_at)
                   VALUES ($1,$2,$3,$4,'approved','v1','demo-seed','medical-advisor',now(),now())
                   ON CONFLICT (doc_id) DO UPDATE
                     SET title=EXCLUDED.title, status='approved', version='v1'""",
                doc_id, title, project, doc_type)
            chunk_id = await conn.fetchval(
                """INSERT INTO app.kb_chunk (doc_id, seq, text, token_count)
                   VALUES ($1, 0, $2, $3)
                   ON CONFLICT (doc_id, seq) DO UPDATE SET text=EXCLUDED.text
                   RETURNING chunk_id""", doc_id, text, len(text))
            rows.append({
                # pk 必填（集合是 auto_id=False）：用 chunk_id 当主键，保证重复执行幂等
                "pk": chunk_id,
                "chunk_id": chunk_id, "doc_id": doc_id, "title": title,
                "project": project or "", "doc_type": doc_type, "version": "v1",
                "doc_status": "approved", "expire_at": 0, "text": text,
                "dense": dense, "sparse": sparse,
            })
            await conn.execute(
                """INSERT INTO app.kb_chunk_vector (chunk_id, embed_model, embed_version, milvus_pk)
                   VALUES ($1,'BAAI/bge-m3','v1',$1)
                   ON CONFLICT (chunk_id, embed_version) DO UPDATE SET milvus_pk=EXCLUDED.milvus_pk""",
                chunk_id)
    store.upsert_chunks(rows)
    return len(rows)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

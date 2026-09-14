"""
检索链路体检：验证「BGE-M3 编码 → Milvus 混合检索 → BGE-Reranker 精排」整条链真的通。

用法（在 Code 目录下，灌完 seed 之后跑）：
    python scripts/check_retrieval.py
    python scripts/check_retrieval.py -q "水光针术后要注意什么"

为什么需要单独验这个：
  · `seed_kb.py` 成功只代表"写进去了"；Milvus 的 `get_collection_stats` 在 flush 前
    往往返回 row_count=0，看着像没写进去，其实数据在 growing segment 里可搜。
  · 真正说明问题的是"能不能搜出相关内容、精排分数是否合理"。
  · 这条链是 RAG 的地基，地基不通，后面审查再严谨也没用。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings("ignore")

from app.services.encoder import BgeM3Encoder  # noqa: E402
from app.services.milvus_store import MilvusHybridStore  # noqa: E402
from app.services.reranker import BgeReranker  # noqa: E402
from app.settings import Settings  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--query", default="热玛吉和超声炮有什么区别")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--projects", default="",
                        help="按项目过滤（逗号分隔），例如 --projects 热玛吉,超声炮；留空=不过滤")
    args = parser.parse_args()

    projects = [p.strip() for p in args.projects.split(",") if p.strip()] or None

    s = Settings()
    s.validate(require_llm=False)
    print(f"Milvus  : {s.milvus_uri}  集合: {s.milvus_collection}")
    print(f"BGE-M3  : {s.bge_m3_path}")
    print(f"Reranker: {s.bge_reranker_path}\n")

    from pymilvus import MilvusClient
    client = MilvusClient(uri=s.milvus_uri, token=s.milvus_token or None)
    if s.milvus_collection not in client.list_collections():
        print(f"✗ 集合 {s.milvus_collection} 不存在 → 先跑 python scripts/seed_kb.py")
        return 1
    # flush 之后 get_collection_stats 才是准的
    client.flush(s.milvus_collection)
    stats = client.get_collection_stats(s.milvus_collection)
    rows = stats.get("row_count", 0)
    print(f"集合统计（flush 后）: {stats}")
    if not rows:
        print("! row_count 仍为 0，可能尚未 flush 完成；下面直接用检索验证")

    enc = BgeM3Encoder(s.bge_m3_path, device=s.embed_device, concurrency=2)
    reranker = BgeReranker(s.bge_reranker_path, device=s.rerank_device, concurrency=2)
    store = MilvusHybridStore(s.milvus_uri, s.milvus_collection, token=s.milvus_token)
    try:
        vecs = await enc.encode([args.query])
        print(f"检索项目过滤: {projects if projects else '（不过滤，全库检索）'}")
        hits = await store.search(query_text=args.query, query_dense=vecs["dense"][0],
                                  query_sparse=vecs["sparse"][0], projects=projects,
                                  top_k=s.recall_k)
        print(f"\n① 混合检索（dense + sparse → RRF）召回 {len(hits)} 条")
        for h in hits[: args.top]:
            print(f"   {h['score']:.4f}  [{h['doc_id']}] {h['title']}")
        if not hits:
            print("\n✗ 检索为空 —— 检查 kb_document.status 是否都是 approved")
            return 1

        docs = [h["text"] for h in hits[: s.rerank_top_k]]
        scores = await reranker.score(args.query, docs)
        ranked = sorted(zip(docs, scores), key=lambda x: -x[1])
        print(f"\n② 精排（BGE-Reranker）后前 {min(args.top, len(ranked))} 条")
        for text, sc in ranked[: args.top]:
            print(f"   {sc:.4f}  {text[:46]}…")

        passed = max(sc for _, sc in ranked) >= s.min_rerank
        print(f"\n③ 阈值检查：最高分 {max(sc for _, sc in ranked):.4f} "
              f"{'≥' if passed else '<'} MIN_RERANK={s.min_rerank}  "
              f"{'✓ 证据可用' if passed else '✗ 会被判证据不足，考虑调低 MIN_RERANK'}")
        print("\n" + "═" * 60)
        print("检索链路 OK" if passed else "检索链路能跑，但阈值偏严")
        print("═" * 60)
        return 0 if passed else 1
    finally:
        await enc.close()
        await reranker.close()
        store.close()
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

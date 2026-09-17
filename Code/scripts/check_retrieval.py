"""
检索链路体检：验证「BGE-M3 编码 → Milvus 混合检索 → BGE-Reranker 精排」整条链真的通，
并且**召回的是对的那篇文档**。

用法（在 Code 目录下，灌完 seed 之后跑）：
    python scripts/check_retrieval.py                    # 跑全部验收用例
    python scripts/check_retrieval.py -q "水光针术后要注意什么"   # 单条临时查
    python scripts/check_retrieval.py --projects 热玛吉      # 带项目过滤查单条

为什么需要单独验这个：
  · `seed_kb.py` 成功只代表"写进去了"；Milvus 的 `get_collection_stats` 在 flush 前
    往往返回 row_count=0，看着像没写进去，其实数据在 growing segment 里可搜。
  · 真正说明问题的是"能不能搜出相关内容、精排分数是否合理"。
  · 这条链是 RAG 的地基，地基不通，后面审查再严谨也没用。

★ 为什么要从"查一条看看"升级成"带期望的验收表"：
  检索最危险的失效方式不是**搜不到**（那会走 kb_limit 降级，看得见），
  而是**搜到了一篇看起来相关的错文档** —— 分数正常、引用有出处、审查也挑不出毛病，
  但它答的不是用户问的那个项目。这种错只有拿"我期望命中哪一篇"去比才会暴露。
  所以下面每个用例都写死期望命中的 doc_id，命中不了就是失败（退出码 1）。

★ 通用资料（DOC-09..DOC-15）的 project 是空串，专门有用例验证
  "带项目过滤时通用资料没有被误伤" —— 这正是本项目踩过的坑：
  过滤条件只写 `project in ["热玛吉"]`，会把写着"需要立即就医的信号"的
  通用风险资料一并排除，且不留任何痕迹。
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

# (用例名, 用户会怎么问, 期望命中的 doc_id 之一, 检索时用的项目过滤)
# 期望写成**集合**：同一问题命中文档本身或对比文档都算合理。
#
# ★★ 这些问句是**故意**和知识库里的措辞不一样的改写句，别图省事抄成一致的 ★★
#   知识库的安全条目里带了"顾客常问：…"的口语引导（为了让精排能桥过口语词），
#   如果把验收问句写成和引导一样，就成了"拿写进去的句子考自己"——
#   分数必然好看，却完全测不出"换个说法还能不能召回"。
#   这里要验的正是泛化：用户不会照着知识库说话。
CASES: list[tuple[str, str, set[str], list[str] | None]] = [
    ("项目原理", "热玛吉是怎么让皮肤变紧的", {"DOC-01"}, ["热玛吉"]),
    ("项目原理", "超声炮适合什么人做", {"DOC-02"}, ["超声炮"]),
    ("术后护理", "水光针打完要注意什么", {"DOC-03", "DOC-11"}, ["水光针"]),
    ("色沉风险", "皮秒做完会不会反黑", {"DOC-05"}, ["皮秒"]),
    ("禁忌", "瘦脸针有什么禁忌症", {"DOC-07"}, ["瘦脸针"]),
    # ★ 安全关键：血管栓塞是玻尿酸最凶的并发症。问句刻意不写成"眼睛看不见了"，
    #   而是换一种说法（"看东西发黑""要不要去急诊"），验的是能不能桥过去。
    ("安全关键", "玻尿酸注射后一只眼睛看东西发黑，需要去急诊吗", {"DOC-08", "DOC-10"},
     ["玻尿酸填充"]),
    ("安全关键", "术后第三天了还在发烧而且肿得更厉害了，这正常吗", {"DOC-10", "DOC-11"},
     ["热玛吉"]),
    ("安全关键", "伤口开始化脓了，是不是得回去找医生", {"DOC-10", "DOC-03"}, ["水光针"]),
    ("术后注意", "做完项目多久能去健身房", {"DOC-11"}, ["热玛吉"]),
    # ★ 通用资料：这几条问的都不带项目名，必须仍能召回通用文档
    ("通用资料", "怎么确认这家店的医生有没有资质", {"DOC-12", "DOC-15"}, None),
    ("通用资料", "为什么不能直接在咨询里告诉我价格", {"DOC-14"}, None),
    ("通用资料", "热玛吉和超声炮到底怎么选", {"DOC-13", "DOC-01", "DOC-02"}, None),
]

# ★ 这两条专测"带项目过滤时通用资料有没有被误伤"：
#   问的是热玛吉，但正确回答依赖通用护理/风险资料。
FILTER_CASES: list[tuple[str, str, set[str], list[str]]] = [
    ("过滤不误伤通用", "热玛吉做完要注意什么", {"DOC-01", "DOC-11"}, ["热玛吉"]),
    ("过滤不误伤风险", "热玛吉有没有风险", {"DOC-01", "DOC-10"}, ["热玛吉"]),
    # 项目过滤 + 口语症状：DOC-10 的 project 是空串，必须仍能被召回
    ("过滤不误伤安全", "热玛吉做完脸又肿又烫，要不要去医院", {"DOC-10", "DOC-01", "DOC-11"},
     ["热玛吉"]),
]

# ★ 安全关键用例：名字里带"安全关键"的，除了要命中对的文档，
#   还必须达到证据阈值 —— 不达标直接让脚本失败（理由见文件末尾的 ③ 段注释）。
CRITICAL_NAMES: set[str] = {"安全关键"}


async def _run_case(store: MilvusHybridStore, enc: BgeM3Encoder, reranker: BgeReranker,
                    name: str, query: str, expect: set[str],
                    projects: list[str] | None, recall_k: int, top: int) -> tuple[bool, str]:
    """跑一条用例，返回 (是否通过, 展示用的一行摘要)。"""
    vecs = await enc.encode([query])
    hits = await store.search(query_text=query, query_dense=vecs["dense"][0],
                             query_sparse=vecs["sparse"][0], projects=projects,
                             top_k=recall_k)
    if not hits:
        return False, f"✗ {name:8s} 召回为空  ← {query}"

    # ★ 按 chunk 顺序取前 top 条去重后的 doc_id。
    #   不能先做 doc 去重再截断：那样 3 条都来自同一篇时会把别的文档顶掉，
    #   让"这一篇霸榜"看起来像"召回了 3 篇"。
    seen: list[str] = []
    for h in hits[: max(top * 3, 9)]:
        if h["doc_id"] not in seen:
            seen.append(h["doc_id"])
    got = set(seen[:top])

    docs = [h["text"] for h in hits[: top * 2]]
    scores = await reranker.score(query, docs)
    best = max(scores) if scores else 0.0

    ok = bool(expect & got)
    mark = "✓" if ok else "✗"
    proj = "、".join(projects) if projects else "全库"
    line = (f"{mark} {name:8s} 精排 {best:.3f} [{proj}] 期望 {'/'.join(sorted(expect))} "
            f"实得 {'/'.join(seen[:top]) or '（空）'}  ← {query}")
    return ok, line


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--query", default="", help="只查这一条（临时排查用）")
    parser.add_argument("--projects", default="", help="配合 -q：项目过滤，逗号分隔")
    parser.add_argument("--top", type=int, default=3, help="命中前 N 篇即算通过")
    args = parser.parse_args()

    single = [p.strip() for p in args.projects.split(",") if p.strip()] or None

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
    print(f"集合统计（flush 后）: {stats}")

    enc = BgeM3Encoder(s.bge_m3_path, device=s.embed_device, concurrency=2)
    reranker = BgeReranker(s.bge_reranker_path, device=s.rerank_device, concurrency=2)
    store = MilvusHybridStore(s.milvus_uri, s.milvus_collection, token=s.milvus_token)
    try:
        # ── 临时单查模式 ──
        if args.query:
            vecs = await enc.encode([args.query])
            hits = await store.search(query_text=args.query, query_dense=vecs["dense"][0],
                                      query_sparse=vecs["sparse"][0], projects=single,
                                      top_k=s.recall_k)
            print(f"混合检索召回 {len(hits)} 条"
                  f"（项目过滤：{'、'.join(single) if single else '不过滤'}\n")
            for h in hits[: args.top]:
                print(f"   {h['score']:.4f}  [{h['doc_id']}] {h['title']}")
                print(f"            {h['text'][:60]}…")
            return 0 if hits else 1

        # ── 验收表模式 ──
        cases = CASES + [("过滤用例 " + n, q, e, p) for n, q, e, p in FILTER_CASES]
        # 一次性把全部 query 编码完：逐条编码会让 BGE 反复进出前向，白等几十秒
        vecs = await enc.encode([q for _n, q, _e, _p in cases])

        results: list[tuple[bool, str]] = []
        best_scores: list[tuple[str, float]] = []
        for (name, query, expect, projects), dense, sparse in zip(
                cases, vecs["dense"], vecs["sparse"]):
            hits = await store.search(query_text=query, query_dense=dense,
                                      query_sparse=sparse, projects=projects,
                                      top_k=s.recall_k)
            if not hits:
                results.append((False, f"✗ {name:12s} 召回为空  ← {query}"))
                continue
            seen: list[str] = []
            for h in hits[: max(args.top * 3, 9)]:
                if h["doc_id"] not in seen:
                    seen.append(h["doc_id"])
            got = set(seen[: args.top])
            docs = [h["text"] for h in hits[: args.top * 2]]
            scores = await reranker.score(query, docs)
            best_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else -1
            best = scores[best_idx] if best_idx >= 0 else 0.0
            best_scores.append((name, best))
            ok = bool(expect & got)
            proj = "、".join(projects) if projects else "全库"
            line = (f"{'✓' if ok else '✗'} {name:12s} 精排 {best:.3f} [{proj}] "
                    f"期望 {'/'.join(sorted(expect))} "
                    f"实得 {'/'.join(seen[: args.top]) or '（空）'}  ← {query}")
            # ★ 低于阈值时把"被判最低分的到底是哪一条、开头写了什么"一并打出来。
            #   否则每次都要另写临时脚本去查"是拿错了块，还是对的块被打低了" ——
            #   这两种情况的修法完全不同（前者改召回，后者改词面或阈值），
            #   而屏幕上只有一个分数，看不出是哪一种。
            if best < s.min_rerank and best_idx >= 0:
                line += (f"\n        ↳ 最高分来自 [{hits[best_idx]['doc_id']}] "
                         f"{hits[best_idx]['text'][:56]}…")
            results.append((ok, line))

        print(f"\n① 检索命中验收（前 {args.top} 篇内含期望文档即通过）")
        for _ok, line in results:
            print("   " + line)

        passed = sum(1 for ok, _ in results if ok)
        total = len(results)
        # ★ 阈值看的是**每个用例里最高的那个精排分**的最小值，而不是所有分数的全局最小值：
        #   同一次检索里必然有跑题的 chunk 分数很低（那正是精排的意义），
        #   把那些算进来，等于要求"连无关内容也得高分"，阈值永远不可能达标。
        worst_name, worst = min(best_scores, key=lambda x: x[1]) if best_scores else ("-", 0.0)
        below = [(n, sc) for n, sc in best_scores if sc < s.min_rerank]

        print(f"\n② 精排阈值（MIN_RERANK={s.min_rerank}）：{len(below)}/{len(best_scores)} 条"
              f"最高分未达阈值；最低者 {worst:.4f}（{worst_name}）")
        for n, sc in below:
            print(f"   ⚠ {n} 最高分 {sc:.4f} → 该轮会一条证据都不保留，降级成“资料不足”")

        # ★★ 安全关键用例单独把关，并且**计入退出码** ★★
        #   为什么不能只当作警告：命中验收（①）只看"对的文档有没有进前 3"，
        #   而证据阈值（②）决定"进了前 3 之后会不会被用"。两者可以同时出现
        #   "①全绿 ②全红"——检索明明找到了正确资料，却因为分数不够而
        #   一条都不用，最终回答退化成"我这边资料不足，建议面诊"。
        #   对"打完玻尿酸眼睛发黑""术后发烧还更肿了"这类问题，
        #   这个降级结果是**危险的**：用户得到的印象是"没什么大不了的"。
        #   所以安全关键的用例不达标必须让脚本失败，而不是在屏幕上闪一行 ✗。
        crit_below = [(n, sc) for n, sc in below if n in CRITICAL_NAMES]
        print(f"\n③ 安全关键用例的阈值检查（{len(CRITICAL_NAMES)} 条）")
        if crit_below:
            for n, sc in crit_below:
                print(f"   ✗ {n} 最高分 {sc:.4f} < {s.min_rerank} —— "
                      f"安全相关问题会被答成“资料不足”，必须修")
        else:
            print("   ✓ 全部高于阈值：安全相关问题的证据会被正常使用")

        print("\n" + "═" * 66)
        print(f"检索验收：命中 {passed}/{total}"
              + (f"，安全关键低于阈值 {len(crit_below)} 条" if crit_below else "，安全关键阈值通过"))
        if passed == total and not crit_below:
            print("检索链路 OK")
        elif passed != total:
            print("✗ 有用例未命中期望文档，见上面标 ✗ 的行")
        else:
            print("✗ 命中都对，但安全关键用例不达证据阈值 —— 退化为“资料不足”")
        print("═" * 66)
        return 0 if (passed == total and not crit_below) else 1
    finally:
        await enc.close()
        await reranker.close()
        store.close()
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""
环境体检：一条命令看清「哪些就绪、哪些还差、下一步做什么」。

用法（在 Code 目录下）：
    python scripts/check_env.py

设计原则：
  · **不依赖任何还没装的库** —— 缺什么就报什么，而不是自己先崩掉
  · 每项检查都给出「怎么修」，而不是只报错
  · 退出码：0 = 可以直接跑；1 = 还有阻塞项（方便接进 CI 或脚本）

分五块：配置 → Python 依赖 → 依赖服务连通性 → 数据状态 → 模型缓存
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import ROOT, Settings  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"
RESULTS: list[tuple[str, str, str, str]] = []      # (level, 项目, 说明, 修复建议)

#: 知识库是否已灌好（由 check_services 设置，用于决定"下一步"里还要不要提 seed）
KB_READY = False

_ICON = {OK: "✓", WARN: "!", FAIL: "✗"}


def _emit(level: str, item: str, detail: str = "", fix: str = "") -> None:
    """记录并**立刻打印** —— 攒到最后打印会让分节标题下面空着，不好读。"""
    RESULTS.append((level, item, detail, fix))
    print(f"  {_ICON[level]} {item}" + (f"   —— {detail}" if detail else ""))
    if fix and level != OK:
        print(f"      → {fix}")


def ok(item: str, detail: str = "") -> None:
    _emit(OK, item, detail)


def warn(item: str, detail: str = "", fix: str = "") -> None:
    _emit(WARN, item, detail, fix)


def fail(item: str, detail: str = "", fix: str = "") -> None:
    _emit(FAIL, item, detail, fix)


def section(title: str) -> None:
    print(f"\n{title}")


def has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def parse_host_port(uri: str, default_port: int) -> tuple[str, int]:
    """从 postgresql:// 或 http:// 里抠出 host/port。"""
    rest = uri.split("://", 1)[-1]
    rest = rest.split("/", 1)[0]
    if "@" in rest:
        rest = rest.rsplit("@", 1)[-1]
    if ":" in rest:
        host, _, port = rest.partition(":")
        return host or "localhost", int(port or default_port)
    return rest or "localhost", default_port


# ════════════════════════════════════════════════════════════════
#  1. 配置
# ════════════════════════════════════════════════════════════════
def check_config(s: Settings) -> None:
    section("1. 配置")
    env_file = ROOT / ".env"
    if env_file.exists():
        ok(".env 存在", str(env_file))
    else:
        warn(".env 不存在（会全部走默认值）", fix="复制 .env.example 为 .env")

    if s.is_fake:
        ok("运行档位", "fake（不连外部依赖，只看图接线）")
    else:
        ok("运行档位", f"real → {s.pg_dsn.rsplit('@', 1)[-1]} / {s.milvus_uri}")

    if s.api_key:
        ok("DEEPSEEK_API_KEY 已配置", f"{s.api_key[:6]}…（{len(s.api_key)} 位）")
    else:
        fail("DEEPSEEK_API_KEY 未配置", "对话链路无法启动",
             "编辑 .env 填 DEEPSEEK_API_KEY=（灌知识库不需要它）")

    if s.escalation_model:
        ok("二次复核用独立模型", s.escalation_model)
    else:
        warn("二次复核为同族", "MVP 预期如此（审计会记 escalation_independent=false）",
             "想真正独立复核：填 ESCALATION_BASE_URL / API_KEY / MODEL")

    hf = os.environ.get("HF_ENDPOINT")
    if hf:
        ok("HuggingFace 镜像已开", hf)
    else:
        warn("未设 HF_ENDPOINT", "国内直连可能很慢或超时",
             "在 .env 里加 HF_ENDPOINT=https://hf-mirror.com")


# ════════════════════════════════════════════════════════════════
#  2. Python 依赖
# ════════════════════════════════════════════════════════════════
DEP_GROUPS: list[tuple[str, list[str], str]] = [
    ("图与校验", ["langgraph", "langgraph.checkpoint.postgres", "psycopg", "pydantic"], "langgraph>=1.0.9"),
    ("业务库", ["asyncpg"], "asyncpg>=0.30"),
    ("检索", ["pymilvus", "FlagEmbedding"], "pymilvus~=2.4.0"),
    ("模型客户端", ["openai"], "openai>=1.50"),
    ("配置与接口", ["yaml", "fastapi", "uvicorn"], "PyYAML>=6.0"),
]


def check_deps() -> dict[str, bool]:
    section("2. Python 依赖")
    present: dict[str, bool] = {}
    for group, modules, pkg in DEP_GROUPS:
        missing = [m for m in modules if not has(m)]
        for m in modules:
            present[m] = m not in missing
        if not missing:
            ok(group, "、".join(modules))
        else:
            fail(group, "缺：" + "、".join(missing),
                 f"pip install -r requirements.txt   （对应 {pkg}）")
    if all(present.get(m, False) for m in ("asyncpg", "pymilvus", "FlagEmbedding", "openai")):
        ok("真实依赖齐备", "可以接 Postgres / Milvus / DeepSeek")
    return present


# ════════════════════════════════════════════════════════════════
#  3. 依赖服务
# ════════════════════════════════════════════════════════════════
async def check_services(s: Settings, deps: dict[str, bool]) -> None:
    section("3. 依赖服务连通性")
    global KB_READY

    pg_host, pg_port = parse_host_port(s.pg_dsn, 5432)
    if port_open(pg_host, pg_port):
        ok("Postgres 端口可达", f"{pg_host}:{pg_port}")
    else:
        fail("Postgres 端口不通", f"{pg_host}:{pg_port}",
             "docker compose -p zhimei up -d   （或检查 .env 里的 PG_DSN）")

    m_host, m_port = parse_host_port(s.milvus_uri, 19530)
    if port_open(m_host, m_port):
        ok("Milvus 端口可达", f"{m_host}:{m_port}")
    else:
        fail("Milvus 端口不通", f"{m_host}:{m_port}",
             "docker compose -p zhimei up -d   （或检查 .env 里的 MILVUS_URI）")

    # ── 真连一次 Postgres ──
    if not deps.get("asyncpg"):
        warn("跳过 Postgres 实连", "未安装 asyncpg", "pip install asyncpg")
    elif not port_open(pg_host, pg_port):
        warn("跳过 Postgres 实连", "端口不通")
    else:
        try:
            import asyncpg
            conn = await asyncio.wait_for(asyncpg.connect(s.pg_dsn), timeout=8)
            try:
                n = await conn.fetchval(
                    "select count(*) from information_schema.tables "
                    "where table_schema in ('app','ops')")
                lg = await conn.fetchval(
                    "select count(*) from information_schema.schemata where schema_name='lg'")
                approved = await conn.fetchval(
                    "select count(*) from app.kb_document where status='approved'")
                user_row = await conn.fetchrow(
                    "select user_id from app.app_user limit 1")
            finally:
                await conn.close()
            if n:
                ok("业务表已建", f"app/ops 共 {n} 张")
            else:
                fail("业务表未建", "app/ops 下没有表",
                     'psql "<PG_DSN>" -f sql/schema.sql   '
                     '或 docker cp sql/schema.sql zhimei-pg:/tmp/ && '
                     'docker exec zhimei-pg psql -U zhimei -d zhimei -f /tmp/schema.sql')
            if lg:
                ok("lg schema 已存在", "LangGraph checkpoint 表已创建")
            else:
                warn("lg schema 还不存在", "首次跑图时会由 AsyncPostgresSaver.setup() 自动创建")
            if approved:
                ok("知识库有已审核文档", f"{approved} 篇")
            else:
                warn("知识库里没有 approved 文档", "检索会一条都查不到",
                     "python scripts/seed_kb.py")
            # 真实档位跑 CLI 必须带 --user，这里直接把它打出来省得再查库
            if user_row:
                ok("演示用户 user_id", f"{user_row['user_id']}  ← CLI 用 --user 传这个")
        except Exception as exc:  # noqa: BLE001
            fail("Postgres 实连失败", str(exc)[:120], "检查 PG_DSN 的账号/库名/密码")

    # ── 真连一次 Milvus ──
    if not deps.get("pymilvus"):
        warn("跳过 Milvus 实连", "未安装 pymilvus", "pip install 'pymilvus~=2.4.0'")
    elif not port_open(m_host, m_port):
        warn("跳过 Milvus 实连", "端口不通")
    else:
        try:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=s.milvus_uri, token=s.milvus_token or None)
            collections = client.list_collections()
            if s.milvus_collection in collections:
                try:
                    stats = client.get_collection_stats(s.milvus_collection)
                    rows = stats.get("row_count", "?")
                except Exception:  # noqa: BLE001
                    rows = "?"
                if rows in (0, "0"):
                    warn(f"集合 {s.milvus_collection} 存在但为空", "还没灌数据",
                         "python scripts/seed_kb.py")
                else:
                    ok(f"集合 {s.milvus_collection} 已就绪", f"{rows} 条向量")
                    KB_READY = True
            else:
                warn(f"集合 {s.milvus_collection} 不存在",
                     f"当前实例已有集合：{collections or '（无）'}",
                     "python scripts/seed_kb.py")
            client.close()
        except Exception as exc:  # noqa: BLE001
            fail("Milvus 实连失败", str(exc)[:120], "确认容器 healthy 且 MILVUS_URI 正确")


# ════════════════════════════════════════════════════════════════
#  4. 模型缓存
# ════════════════════════════════════════════════════════════════
def model_status(model_path: str) -> tuple[bool, str]:
    """返回 (是否已就绪, 说明)。既支持 HF 仓库名，也支持本地目录。"""
    p = Path(model_path)
    if p.exists() and p.is_dir():                      # 本地目录
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return True, f"本地目录 {p}（{size / 1e9:.2f} GB）"

    home = os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    cache = Path(home) if home else Path.home() / ".cache" / "huggingface"
    repo_dir = cache / "hub" / ("models--" + model_path.replace("/", "--"))
    if repo_dir.exists():
        size = sum(f.stat().st_size for f in repo_dir.rglob("*") if f.is_file())
        if size > 1e6:
            return True, f"HF 缓存 {repo_dir.name}（{size / 1e9:.2f} GB）"
    return False, f"未在 {cache} 找到（首次运行会下载）"


def check_models(s: Settings) -> None:
    section("4. 模型缓存")
    for label, path in (("BGE-M3（嵌入，约 2.2GB）", s.bge_m3_path),
                        ("BGE-Reranker（精排，约 600MB）", s.bge_reranker_path)):
        ready, detail = model_status(path)
        if ready:
            ok(label + " 已缓存", detail)
        else:
            warn(label + " 未缓存", detail,
                 "首次运行会自动下载；国内先设 HF_ENDPOINT=https://hf-mirror.com")


# ════════════════════════════════════════════════════════════════
#  汇总
# ════════════════════════════════════════════════════════════════
def report(s: Settings, deps: dict[str, bool]) -> int:
    failures = [r for r in RESULTS if r[0] == FAIL]
    warnings = [r for r in RESULTS if r[0] == WARN]
    passed = [r for r in RESULTS if r[0] == OK]

    # fake 档位用内存 + 脚本化假模型，不需要任何外部依赖与 API key ——
    # 上面那些 ✗ 只是"真实档位还缺什么"的信息，不构成 fake 档位的阻塞项
    if s.is_fake:
        print("\n" + "═" * 64)
        print(f"体检小结：{len(passed)} 项通过 · {len(warnings)} 项提醒 · {len(failures)} 项阻塞"
              "（fake 档位下这些都不影响运行）")
        print("跑对话   (app.cli / app.api)  : ✓ 可以跑 —— fake 档位用内存与脚本化假模型，不需要外部依赖")
        print("灌知识库 (scripts/seed_kb.py) : — 不适用（fake 档位用内置演示知识库）")
        print("\n现在就能验证图与接口层：")
        print("  python scripts/smoke.py && python scripts/smoke_api.py && python scripts/smoke_ops.py")
        print("  python -m app.cli --demo --profile fake")
        print("═" * 64)
        return 1 if failures else 0

    # 两条链路分开判断：灌数据不需要大模型
    seed_missing = [m for m in ("asyncpg", "pymilvus", "FlagEmbedding") if not deps.get(m)]
    seed_blockers = list(seed_missing)
    if not port_open(*parse_host_port(s.pg_dsn, 5432)) or \
       not port_open(*parse_host_port(s.milvus_uri, 19530)):
        seed_blockers.append("依赖服务不可达")
    chat_blockers = list(seed_blockers) + ([] if s.api_key else ["DEEPSEEK_API_KEY 未配置"])

    print("\n" + "═" * 64)
    print(f"体检小结：{len(passed)} 项通过 · {len(warnings)} 项提醒 · {len(failures)} 项阻塞")
    print(f"灌知识库 (scripts/seed_kb.py) : {'✓ 可以跑' if not seed_blockers else '✗ 还差 ' + '、'.join(seed_blockers)}")
    print(f"跑对话   (app.cli / app.api)  : {'✓ 可以跑' if not chat_blockers else '✗ 还差 ' + '、'.join(chat_blockers)}")

    if chat_blockers:
        print("\n下一步：")
        steps = []
        if seed_missing:
            steps.append("pip install -r requirements.txt")
        if not port_open(*parse_host_port(s.pg_dsn, 5432)) or \
           not port_open(*parse_host_port(s.milvus_uri, 19530)):
            steps.append("docker compose -p zhimei up -d")
        if any(r[0] == FAIL and "业务表未建" in r[1] for r in RESULTS):
            steps.append('psql "<PG_DSN>" -f sql/schema.sql')
        if not s.api_key:
            steps.append("编辑 .env 填 DEEPSEEK_API_KEY=")
        if not KB_READY:
            steps.append("python scripts/download_models.py   # 国内建议先把 BGE 模型下到本地")
            steps.append("python scripts/seed_kb.py")
        steps.append('python -m app.cli -t "热玛吉和超声炮有什么区别" --user <user_id>')
        steps.append("python scripts/check_retrieval.py   # 验证检索链条真的通")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
    else:
        print("\n环境就绪，可以照 SELF-TEST.md 逐项自测了。")
    print("═" * 64)
    return 1 if failures else 0


async def main() -> int:
    settings = Settings()
    print("智美医美顾问 MVP · 环境体检")
    print(f"项目目录：{ROOT}")
    check_config(settings)
    deps = check_deps()
    if not settings.is_fake:
        await check_services(settings, deps)
    check_models(settings)
    return report(settings, deps)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

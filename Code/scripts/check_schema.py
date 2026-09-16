"""
数据库结构检查：**新建库**与**升级已有库**两条路径都必须真的能跑通。

    python scripts/check_schema.py        （在 Code 目录下执行）

═══════════ 为什么需要它 ═══════════

`sql/schema.sql` 是这个项目**唯一**的表结构来源，升级方式就是"把它再跑一遍"
（`check_env.py` 打印的下一步里就是这么写的）。但这条路有个安静的坑：

  所有建表语句都是 `CREATE TABLE IF NOT EXISTS`，而它对**已存在**的表是
  **整条跳过**的 —— 表里少一列也不会补。于是"加了一列"这件事，
  在新建库上完全正常，在已有库上**什么都没发生**，直到运行期报一个
  毫无线索的 500：`column "is_test" does not exist`，
  而建表语句里明明写着这一列。

  更隐蔽的是第二层：`CREATE INDEX` 引用了新列时，会在**建列语句之前**执行，
  直接报错并**中断整个脚本** —— 于是后面所有补列的 ALTER 一行都跑不到。
  看起来"我明明写了补列语句"，跑完列还是不在。

这个脚本真的抓到过这两个坑（`idx_app_user_email` 依赖 `email`、
`idx_ticket_queue_real` 依赖 `is_test`，两条索引都写在各自建表语句的下面，
是最不可能被怀疑的位置）。所以现在把两条路径都做成自动检查：

  1. **新建库路径** —— 空库跑一遍，列/类型/索引必须齐。
  2. **升级路径** —— 在空库里**把新列删掉**模拟旧库，塞一行历史数据，
     再跑一遍 schema.sql，验证列被补上、历史行被回填成默认值、索引被建回来。

两条都在**临时库**里做，跑完即删，不碰你真库里的数据。
需要 `CREATEDB` 权限；没有就跳过并说明原因，不算失败。
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

try:
    import asyncpg
except Exception:  # noqa: BLE001
    asyncpg = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402,F401
from app.settings import settings  # noqa: E402

SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
TESTDB = "zhimei_schematest"

#: ★ 单一事实来源：所有"后加的列"都登记在这里。
#:   加列时除了改 schema.sql，**必须**往这里加一行 —— 否则升级路径就没被测到，
#:   而没被测到的升级路径等于没有。
#:   格式：(schema, 表, 列, 期望类型)
MIGRATED_COLUMNS: list[tuple[str, str, str, str]] = [
    ("app", "app_user", "email", "text"),
    ("app", "app_user", "password_hash", "text"),
    ("app", "app_user", "email_verified", "boolean"),
    ("app", "app_user", "verify_token", "text"),
    ("app", "app_user", "verify_expires", "timestamp with time zone"),
    ("app", "app_user", "last_login_at", "timestamp with time zone"),
    ("ops", "agent_user", "status", "text"),
    ("ops", "agent_user", "email", "text"),
    ("ops", "agent_user", "password_hash", "text"),
    ("ops", "agent_user", "last_login_at", "timestamp with time zone"),
    ("app", "review_audit", "escalation_reason", "text"),
    ("ops", "handoff_ticket", "is_test", "boolean"),
    ("app", "chat_session", "human_request_count", "integer"),
]

#: 引用了"后加的列"的索引 —— 它们必须在建列之后创建，所以会被上面这个列表连累。
MIGRATED_INDEXES = ["app.idx_app_user_email", "ops.idx_ticket_queue_real"]

#: 与本次升级无关、但新建库必须有的索引（防手滑删掉）
BASE_INDEXES = ["ops.idx_ticket_queue"]

OK, FAIL, SKIP = [], [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond or not detail else '   —— ' + detail}")


async def col_type(conn, sch: str, tbl: str, col: str) -> str | None:
    return await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema=$1 AND table_name=$2 AND column_name=$3", sch, tbl, col)


async def scenario_fresh(conn, sql: str) -> None:
    """路径 1：空库跑一遍。"""
    await conn.execute(sql)
    for sch, tbl, col, want in MIGRATED_COLUMNS:
        got = await col_type(conn, sch, tbl, col)
        check(f"新建库有 {sch}.{tbl}.{col}", got == want, f"实际 {got}，期望 {want}")
    for name in MIGRATED_INDEXES + BASE_INDEXES:
        check(f"新建库有索引 {name}",
              await conn.fetchval("SELECT to_regclass($1)", name) is not None)
    # 幂等：新建库上重复执行也必须无报错（onboarding 时会反复跑）
    try:
        await conn.execute(sql)
        check("新建库上重复执行 schema.sql 无报错（幂等）", True)
    except Exception as exc:  # noqa: BLE001
        check("新建库上重复执行 schema.sql 无报错（幂等）", False,
              f"{type(exc).__name__}: {exc}")


async def scenario_upgrade(conn, sql: str) -> None:
    """路径 2：把新列删掉模拟旧库，再跑一遍 schema.sql。"""
    for sch, tbl, col, _ in MIGRATED_COLUMNS:
        await conn.execute(f'ALTER TABLE {sch}.{tbl} DROP COLUMN IF EXISTS "{col}"')
    still = [f"{sch}.{tbl}.{col}" for sch, tbl, col, _ in MIGRATED_COLUMNS
             if await col_type(conn, sch, tbl, col) is not None]
    check("已成功模拟出「旧库」（新列都不存在）", not still,
          f"还有列没删掉：{still}")

    # 旧库里塞一行历史数据：升级后必须被回填成 DEFAULT，而不是 NULL
    await conn.execute(
        "INSERT INTO ops.handoff_ticket (thread_id, reason, priority) "
        "VALUES ('schema-check', '升级路径检查', 'P1')")

    try:
        await conn.execute(sql)
    except Exception as exc:  # noqa: BLE001
        # 这一条就是那两个坑的症状：CREATE INDEX 引用了还不存在的列
        check("旧库上重复执行 schema.sql 无报错", False,
              f"{type(exc).__name__}: {exc}（多半是 CREATE INDEX 引用了尚未补上的列，"
              f"它会在补列之前执行并中断整个脚本）")
        return
    check("旧库上重复执行 schema.sql 无报错", True)

    for sch, tbl, col, want in MIGRATED_COLUMNS:
        got = await col_type(conn, sch, tbl, col)
        check(f"升级后补上了 {sch}.{tbl}.{col}", got == want, f"实际 {got}，期望 {want}")
    for name in MIGRATED_INDEXES:
        check(f"升级后建回了索引 {name}",
              await conn.fetchval("SELECT to_regclass($1)", name) is not None)

    got = await conn.fetchval("SELECT is_test FROM ops.handoff_ticket "
                              "WHERE thread_id='schema-check'")
    check("历史数据被回填成 false（不是 NULL）", got is False, f"实际 {got!r}")
    check("NOT NULL 约束生效（新增列给了 DEFAULT，已有行才加得上）",
          (await conn.fetchval(
              "SELECT is_nullable FROM information_schema.columns WHERE table_schema='ops' "
              "AND table_name='handoff_ticket' AND column_name='is_test'")) == "NO")


async def main() -> int:
    if asyncpg is None:
        print("✗ 未安装 asyncpg，无法检查（pip install asyncpg）")
        return 1

    if not SQL_PATH.exists():
        print(f"✗ 找不到 {SQL_PATH}")
        return 1
    sql = io.open(SQL_PATH, encoding="utf-8").read()

    base = settings.pg_dsn.rsplit("/", 1)[0]
    admin_dsn, test_dsn = base + "/postgres", base + f"/{TESTDB}"

    try:
        admin = await asyncpg.connect(admin_dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  连不上 Postgres（{type(exc).__name__}: {exc}）")
        print("    跳过：这个检查需要真实库来建临时库。先 docker compose -p zhimei up -d")
        return 0

    try:
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {TESTDB}")
            await admin.execute(f"CREATE DATABASE {TESTDB}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  建临时库失败（{type(exc).__name__}: {exc}）—— 多半是账号没有 CREATEDB 权限")
            print("    跳过。想跑的话给账号加权限：ALTER ROLE zhimei CREATEDB;")
            return 0

        try:
            print(f"临时库 {TESTDB} 已建好（跑完即删，不碰真库数据）\n")
            conn = await asyncpg.connect(test_dsn)
            try:
                print("1. 新建库路径：空库跑一遍 schema.sql")
                await scenario_fresh(conn, sql)
                print("\n2. 升级路径：删掉新列模拟旧库，再跑一遍 schema.sql")
                await scenario_upgrade(conn, sql)
            finally:
                await conn.close()
        finally:
            await admin.execute(f"DROP DATABASE IF EXISTS {TESTDB}")
            print(f"\n临时库 {TESTDB} 已删除")
    finally:
        await admin.close()

    print("\n" + "═" * 62)
    if FAIL:
        print(f"通过 {len(OK)} 项，失败 {len(FAIL)} 项")
        print("失败项：")
        for n in FAIL:
            print(f"  · {n}")
    else:
        print(f"通过 {len(OK)} 项，失败 0 项 —— 新建库与升级两条路径都通")
    print("═" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

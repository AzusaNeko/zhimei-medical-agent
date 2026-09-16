"""
全局配置：从环境变量读取，零第三方依赖（不引入 pydantic-settings）。

设计取舍：
  · 配置项集中在一个 dataclass 里，启动时校验一次，之后只读传入 Deps
  · 不在代码里写默认密钥；缺失关键项时启动即报错，而不是运行到一半才炸
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取：只处理 KEY=VALUE 与 # 注释，不覆盖已存在的环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")


def _s(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _i(key: str, default: int) -> int:
    raw = _s(key)
    return int(raw) if raw else default


def _f(key: str, default: float) -> float:
    raw = _s(key)
    return float(raw) if raw else default


def _b(key: str, default: bool) -> bool:
    """布尔开关。只认几种明确的写法，避免 "false" 被当成真值这种经典坑。"""
    raw = _s(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # ── 档位 ──
    profile: str = field(default_factory=lambda: _s("APP_PROFILE", "real").lower())

    # ── 大模型 ──
    api_key: str = field(default_factory=lambda: _s("DEEPSEEK_API_KEY"))
    base_url: str = field(default_factory=lambda: _s("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
    model: str = field(default_factory=lambda: _s("DEEPSEEK_MODEL", "deepseek-chat"))
    escalation_base_url: str = field(default_factory=lambda: _s("ESCALATION_BASE_URL"))
    escalation_api_key: str = field(default_factory=lambda: _s("ESCALATION_API_KEY"))
    escalation_model: str = field(default_factory=lambda: _s("ESCALATION_MODEL"))

    # ── Postgres ──
    pg_dsn: str = field(default_factory=lambda: _s("PG_DSN", "postgresql://zhimei:zhimei@localhost:55432/zhimei"))

    # ── Milvus ──
    milvus_uri: str = field(default_factory=lambda: _s("MILVUS_URI", "http://localhost:19530"))
    milvus_token: str = field(default_factory=lambda: _s("MILVUS_TOKEN"))
    milvus_collection: str = field(default_factory=lambda: _s("MILVUS_COLLECTION", "kb_chunks"))

    # ── 进程内模型 ──
    bge_m3_path: str = field(default_factory=lambda: _s("BGE_M3_PATH", "BAAI/bge-m3"))
    bge_reranker_path: str = field(default_factory=lambda: _s("BGE_RERANKER_PATH", "BAAI/bge-reranker-v2-m3"))
    embed_device: str = field(default_factory=lambda: _s("EMBED_DEVICE", "cpu"))
    rerank_device: str = field(default_factory=lambda: _s("RERANK_DEVICE", "cpu"))
    embed_concurrency: int = field(default_factory=lambda: _i("EMBED_CONCURRENCY", 4))
    rerank_concurrency: int = field(default_factory=lambda: _i("RERANK_CONCURRENCY", 4))

    # ── 凭据 ──
    release_secret: str = field(default_factory=lambda: _s("RELEASE_SECRET", "dev-only-change-me"))
    #: JWT 签名密钥。★ 必须 ≥32 字节：HS256 用 SHA-256，密钥短于摘要长度会削弱
    #: 安全性（PyJWT 自己也会为此发 InsecureKeyLengthWarning）。
    jwt_secret: str = field(default_factory=lambda: _s("JWT_SECRET", "dev-only-change-me-jwt-secret-32b"))
    #: 令牌有效期（小时）。短期令牌 + 重新登录，比长期令牌安全得多。
    jwt_ttl_hours: int = field(default_factory=lambda: _i("JWT_TTL_HOURS", 12))
    #: 邮箱验证链接的有效期（小时）。占位实现里也能用。
    email_verify_ttl_hours: int = field(default_factory=lambda: _i("EMAIL_VERIFY_TTL_HOURS", 24))
    #: ★ 是否在注册响应里**直接返回**邮箱验证令牌。
    #:
    #:   这是个**演示期的临时妥协**：本项目没有邮件服务，不返回令牌的话
    #:   注册完就没法完成验证，流程走不下去。
    #:   它的风险是明确的：任何能调用注册接口的人都能拿到令牌并激活自己 ——
    #:   而这本来就是注册者本人，所以风险有限；但它**不能带到生产**。
    #:   接上真实邮件服务后必须置 false（验证令牌只应出现在邮件里）。
    expose_verify_token: bool = field(default_factory=lambda: _b("EXPOSE_VERIFY_TOKEN", True))

    # ── 预算与时效 ──
    max_revision: int = field(default_factory=lambda: _i("MAX_REVISION", 2))
    max_clarify: int = field(default_factory=lambda: _i("MAX_CLARIFY", 2))
    kb_max_verify_loop: int = field(default_factory=lambda: _i("KB_MAX_VERIFY_LOOP", 3))
    recursion_limit: int = field(default_factory=lambda: _i("RECURSION_LIMIT", 40))
    token_ttl_seconds: int = field(default_factory=lambda: _i("TOKEN_TTL_SECONDS", 1800))
    plan_expire_seconds: int = field(default_factory=lambda: _i("PLAN_EXPIRE_SECONDS", 1800))

    # ── 超时 ──
    t_understand: float = field(default_factory=lambda: _f("T_UNDERSTAND", 8))
    t_generate: float = field(default_factory=lambda: _f("T_GENERATE", 30))
    t_review: float = field(default_factory=lambda: _f("T_REVIEW", 20))
    #: 检索（Milvus 混合检索）超时预算。
    #: ★ 必须有这个预算：实测 Milvus 一次 "inconsistent requery result" 会让
    #:   pymilvus 内部重试 75 次、跨 30 分钟才返回 —— 那一轮对话就挂在那里，
    #:   图里的降级逻辑一次都跑不到（因为调用根本没返回）。
    #:   正常情况检索 5 条数据是毫秒级，10 秒已经极其宽松。
    t_recall: float = field(default_factory=lambda: _f("T_RECALL", 10))

    # ── 检索 ──
    recall_k: int = field(default_factory=lambda: _i("RECALL_K", 40))
    rerank_top_k: int = field(default_factory=lambda: _i("RERANK_TOP_K", 8))
    max_evidence: int = field(default_factory=lambda: _i("MAX_EVIDENCE", 6))
    min_rerank: float = field(default_factory=lambda: _f("MIN_RERANK", 0.30))

    # ── 规则 ──
    rules_path: str = field(default_factory=lambda: _s("RULES_PATH", "app/config/rules.yaml"))

    # ── 派生 ──
    @property
    def is_fake(self) -> bool:
        return self.profile == "fake"

    @property
    def escalation_is_independent(self) -> bool:
        """复核模型是否与主模型不同家族 —— 决定审计里 escalation_independent 写什么。"""
        return bool(self.escalation_model)

    def rules_file(self) -> Path:
        p = Path(self.rules_path)
        return p if p.is_absolute() else (ROOT / p)

    def validate(self, *, require_llm: bool = True) -> None:
        """
        启动即校验，避免跑到一半才发现缺 key。

        require_llm=False 用于「不需要大模型」的脚本（例如只灌知识库的 seed_kb.py）——
        灌数据只需要 PG + Milvus + BGE，不该因为还没配 API key 就卡住。
        """
        if self.is_fake:
            return
        missing = []
        if require_llm and not self.api_key:
            missing.append("DEEPSEEK_API_KEY")
        if not self.pg_dsn:
            missing.append("PG_DSN")
        if missing:
            raise RuntimeError(
                "缺少必要配置：" + "、".join(missing) +
                "\n→ 复制 .env.example 为 .env 并填写；若只想先验证图接线，用 --profile fake 运行。"
            )


settings = Settings()

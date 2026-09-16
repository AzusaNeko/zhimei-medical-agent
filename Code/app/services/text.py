"""
文本处理：清洗、脱敏预扫描、子句切分。

子句切分是紧急信号判定的基础 —— 判定必须按「子句」而不是整句做，
否则「我朋友眼睛看不清，我现在没事」这类混合句会被误判。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# 零宽字符与控制字符（用户粘贴常带）
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\x00-\x08\x0b\x0c\x0e-\x1f]")
_WS = re.compile(r"[ \t\u3000]+")

# 子句分隔：中英文标点 + 换行
_CLAUSE_SEP = re.compile(r"[，。！？；、,.!?;\n\r]+")

MAX_INPUT_CHARS = 2000

# PII 预扫描（仅用于脱敏与硬规则 PRIV-001，不用于业务）
_PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "bank_card": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
}


#: 邮箱：只用于 `mask()`，**不放进 `_PII_PATTERNS`**。
#:
#: ★ 这个不对称是刻意的。`_PII_PATTERNS` 是 `prescan()` 的输入，而 prescan
#:   的结果会喂给硬规则 PRIV-001（"泄露个人敏感信息"）。如果把邮箱也放进去，
#:   用户自己主动留一个邮箱就会被判成隐私泄露 —— 那会把正常咨询拦下来。
#:   脱敏要的是"别把邮箱原文给不该看的人"，不是"检测到邮箱就升级风险"。
#:   两件事目的不同，所以走两条路径。改这里之前先想清楚要的是哪一种。
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def clean(text: str) -> str:
    """去不可见字符、折叠空白、限长。不改语义，不删标点。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = _INVISIBLE.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s[:MAX_INPUT_CHARS]


def prescan(text: str) -> dict[str, list[str]]:
    """预扫描敏感串，供审计与脱敏使用。返回 {类型: [命中...]}。"""
    out: dict[str, list[str]] = {}
    for kind, pat in _PII_PATTERNS.items():
        found = pat.findall(text or "")
        if found:
            out[kind] = found
    return out


def mask(text: str) -> str:
    """脱敏：手机号保留前 3 后 4，身份证保留前 4 后 4，银行卡与邮箱局部打码。

    ★ 邮箱是**后补的**：原来只处理身份证/银行卡/手机号，结果坐席工作台里
      "发起人邮箱"在 service 角色下也是完整可见的 —— 而注释里写的是
      "与手机号同一条规则"。注释声称的行为和代码实际做的不一致，
      这种偏差不会被任何测试发现，只能靠实际打开界面看。
    """
    s = text or ""
    # 顺序有讲究：邮箱先处理。不然 `13800138000@x.com` 会先被手机号规则
    # 改成 `138****8000@x.com`，邮箱规则就再也匹配不上了。
    s = _EMAIL.sub(lambda m: _mask_email(m.group()), s)
    s = _PII_PATTERNS["id_card"].sub(lambda m: m.group()[:4] + "*" * 10 + m.group()[-4:], s)
    s = _PII_PATTERNS["bank_card"].sub(lambda m: "*" * len(m.group()), s)
    s = _PII_PATTERNS["phone"].sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], s)
    return s


def _mask_email(addr: str) -> str:
    """`demo@zhimei.test` → `d***@zhimei.test`。

    保留：
      · 局部名首字符 —— 让人能认出"是哪个邮箱"，但猜不出全名；
      · 域名整个保留 —— 域名不是个人信息，而且**坐席需要它**
        判断是不是企业邮箱 / 该走哪个渠道联系。
    局部名只有 1 个字符时也要打 3 个星号，避免 1 个字符的邮箱被直接还原。
    """
    local, sep, domain = addr.partition("@")
    if not sep:
        return addr
    return f"{local[:1]}{'*' * max(3, len(local) - 1)}@{domain}"


def split_clauses(text: str) -> list[str]:
    """切成子句，保留顺序。空子句丢弃。"""
    return [c.strip() for c in _CLAUSE_SEP.split(text or "") if c and c.strip()]


def find_terms(clause: str, terms: Iterable[str]) -> list[tuple[str, int]]:
    """在子句里找词，返回 [(词, 起始下标)]，长词优先（避免「疼」抢先匹配「特别疼」）。

    ★ 词表来自 YAML，未加引号的 120 / 315 会被解析成 int —— 这里统一转成字符串，
      否则会在运行时抛 "object of type 'int' has no len()"。
    """
    hits: list[tuple[str, int]] = []
    for term in sorted({str(t) for t in terms if t is not None}, key=len, reverse=True):
        start = clause.find(term)
        if start >= 0:
            hits.append((term, start))
    return hits


def dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))

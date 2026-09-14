"""
把 BGE 模型预先下载到项目内的本地目录。

为什么需要这个脚本（直接用 FlagEmbedding 的自动下载会失败）：
  1. FlagEmbedding 内部调 `snapshot_download()` 拉取仓库**全部文件**，
     其中包括 `imgs/.DS_Store` 这类 macOS 垃圾文件；
  2. 国内镜像 hf-mirror.com 会直接对这些文件返回 **403 Forbidden**，
     于是整个下载失败（哪怕模型权重本身能下）。
  3. 所以正确做法是：自己控制下载（带 ignore 规则）→ 落到本地目录 →
     把 BGE_M3_PATH / BGE_RERANKER_PATH 指向该目录，运行时就不再联网。

用法（在 Code 目录下）：
    python scripts/download_models.py            # 下到 models/ 下
    python scripts/download_models.py --force    # 已存在也重下

下载完成后脚本会打印要写进 .env 的两行配置。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ★ 先导入 settings：确保 HF_ENDPOINT / HF_HOME / HF_HUB_DISABLE_SYMLINKS
#   在 huggingface_hub 导入之前写进环境变量
from app.settings import ROOT, Settings  # noqa: E402

from huggingface_hub import snapshot_download  # noqa: E402

#: 这些文件与推理无关，而且镜像常对它们返回 403
IGNORE_PATTERNS = [
    "*.DS_Store", ".DS_Store", "**/.DS_Store",
    "imgs/*", "images/*", "*.png", "*.jpg", "*.jpeg",
    "*.md", "*.gitattributes", "*.gitignore",
    "onnx/*", "openvino/*", "*.onnx",
]

TARGETS = [
    ("BAAI/bge-m3", "bge-m3"),
    ("BAAI/bge-reranker-v2-m3", "bge-reranker-v2-m3"),
]


def dir_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e9


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    settings = Settings()
    out_root = ROOT / "models"
    out_root.mkdir(exist_ok=True)

    print(f"镜像：{settings.bge_m3_path if '://' in settings.bge_m3_path else '(看 HF_ENDPOINT)'}")
    import os
    print(f"HF_ENDPOINT = {os.environ.get('HF_ENDPOINT', '(未设，将直连 huggingface.co)')}")
    print(f"HF_HOME     = {os.environ.get('HF_HOME', '(默认用户缓存)')}")
    print(f"下载目录    = {out_root}\n")

    results: list[tuple[str, Path]] = []
    for repo_id, folder in TARGETS:
        dest = out_root / folder
        weight_files = list(dest.rglob("*.safetensors")) + list(dest.rglob("*.bin"))
        if weight_files and not args.force:
            print(f"✓ {repo_id} 已存在（{dir_size(dest):.2f} GB），跳过")
            results.append((repo_id, dest))
            continue

        print(f"↓ {repo_id} → {dest}")
        started = time.time()
        try:
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(dest),
                ignore_patterns=IGNORE_PATTERNS,
                max_workers=args.workers,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"\n✗ {repo_id} 下载失败：{type(exc).__name__}: {str(exc)[:300]}")
            print("  常见原因：")
            print("   · 403/404 → 镜像缺文件，试试去掉部分 ignore 规则或换 HF_ENDPOINT")
            print("   · 超时    → 网络不稳，重跑本脚本即可断点续传")
            print("   · 权限    → HF_HOME 指向的目录不可写")
            return 1
        size = dir_size(dest)
        print(f"✓ 完成，用时 {time.time() - started:.0f}s，{size:.2f} GB\n")
        results.append((repo_id, dest))

    print("═" * 64)
    print("下载完成。把下面两行写进 .env（替换原来的仓库名）：\n")
    for repo_id, dest in results:
        key = "BGE_M3_PATH" if "bge-m3" in repo_id else "BGE_RERANKER_PATH"
        print(f"{key}={dest}")
    print("\n然后跑：python scripts/seed_kb.py")
    print("═" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())

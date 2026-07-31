#!/usr/bin/env python3
"""运行固定演示 Base 的完整同步闭环。"""

from __future__ import annotations

import argparse
from pathlib import Path

from feishu_base_adapter import PROJECT_ROOT, sync_base

DEFAULT_CONFIG = (
    PROJECT_ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "assets"
    / "feishu_demo_base.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "generated_output"


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 CHA CUP 固定飞书 Base")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="只读和本地执行，不上传或回写")
    parser.add_argument("--force", action="store_true", help="忽略输入指纹，重新处理全部任务")
    args = parser.parse_args()

    report = sync_base(
        args.config,
        args.output_dir,
        dry_run=args.dry_run,
        force=args.force,
    )
    summary = report["summary"]
    sync = report["feishu_sync"]
    print("飞书 Base 同步完成")
    print(f"读取任务: {sync['records_read']}")
    print(f"通过: {summary['passed']}")
    print(f"需修改: {summary['blocked']}")
    print(f"回写: {sync['records_written']}")
    print(f"上传图片: {sync['images_uploaded']}")
    print(f"跳过未变化: {sync['unchanged_skipped']}")


if __name__ == "__main__":
    main()

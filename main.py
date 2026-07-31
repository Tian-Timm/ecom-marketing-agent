#!/usr/bin/env python3
"""CHA CUP 唯一流水线入口。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = (
    ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "scripts"
    / "run_pipeline.py"
)
DEFAULT_INPUT = (
    ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "assets"
    / "marketing_tasks.csv"
)
OUTPUT_DIR = ROOT / "generated_output"
FEISHU_SYNC = (
    ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "scripts"
    / "sync_feishu_base.py"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="CHA CUP 营销图片任务入口")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--feishu", action="store_true", help="从固定演示 Base 读取并回写")
    parser.add_argument("--dry-run", action="store_true", help="飞书模式只读试运行")
    parser.add_argument("--force", action="store_true", help="飞书模式强制重新处理")
    args = parser.parse_args()

    if args.feishu:
        command = [sys.executable, str(FEISHU_SYNC)]
        if args.dry_run:
            command.append("--dry-run")
        if args.force:
            command.append("--force")
        return subprocess.run(command, check=False).returncode

    input_path = args.input.resolve()
    command = [
        sys.executable,
        str(PIPELINE),
        "--input",
        str(input_path),
        "--output-dir",
        str(OUTPUT_DIR),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

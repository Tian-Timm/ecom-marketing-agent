#!/usr/bin/env python3
"""CHA CUP 唯一流水线入口。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / ".agents" / "skills" / "simple-visual-compliance" / "scripts" / "run_pipeline.py"
DEFAULT_INPUT = ROOT / ".agents" / "skills" / "simple-visual-compliance" / "assets" / "marketing_tasks.csv"
OUTPUT_DIR = ROOT / "generated_output"

def main() -> int:
    input_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_INPUT
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

#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _load_main():
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from code_review.cli import main

    return main


if __name__ == "__main__":
    raise SystemExit(_load_main()())
#!/usr/bin/env python3
"""兼容入口：文章/文档 → HTML 的逻辑已迁至 ``tools/pagegen/doc_to_html.py``。"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent.parent / "pagegen" / "doc_to_html.py"),
        run_name="__main__",
    )

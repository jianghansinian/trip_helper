#!/usr/bin/env python3
"""
清理已生成 HTML 中残留的 Markdown 格式痕迹（常见为模型输出的 **bold** / *italic* / __bold__ / _italic_）。

目标：
- 删除正文文本节点里的星号/下划线强调符号，而不改动 HTML 结构（不做重新排版）。
- 跳过 <script>/<style>/<code>/<pre> 等区域，避免破坏代码或样式。

用法：
  python3 tools/i18n/clean_html_markdown_artifacts.py -i guides/foo.html
  python3 tools/i18n/clean_html_markdown_artifacts.py --dir guides
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, List

from bs4 import BeautifulSoup, NavigableString, Tag

SKIP_PARENT_TAGS = {
    "a",
    "code",
    "pre",
    "kbd",
    "script",
    "style",
    "noscript",
    "textarea",
    "title",
    "head",
}


def clean_markdown_emphasis_text(s: str) -> str:
    # **bold** / __bold__
    s2 = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s2 = re.sub(r"__([^_]+)__", r"\1", s2)
    # *italic* / _italic_ (avoid eating list bullets like "*   ")
    s2 = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", s2)
    s2 = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", s2)
    # bullet artifacts: "*   Foo" -> "Foo" (only when it's a bullet-like pattern)
    s2 = re.sub(r"(^|\n)\s*\*\s{2,}", r"\1", s2)
    return s2


def iter_text_nodes(soup: BeautifulSoup) -> Iterable[NavigableString]:
    for t in soup.find_all(string=True):
        if not isinstance(t, NavigableString):
            continue
        p = t.parent
        if p is None:
            continue
        if p.name and p.name.lower() in SKIP_PARENT_TAGS:
            continue
        yield t


def clean_html(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "html.parser")
    changed = False

    for t in list(iter_text_nodes(soup)):
        old = str(t)
        if "*" not in old and "_" not in old:
            continue
        new = clean_markdown_emphasis_text(old)
        if new != old:
            t.replace_with(new)
            changed = True

    if changed:
        path.write_text(str(soup), encoding="utf-8")
    return changed


def iter_html_files_in_dir(d: Path) -> List[Path]:
    return sorted([p for p in d.rglob("*.html") if p.is_file()])


def main() -> None:
    ap = argparse.ArgumentParser(description="清理 HTML 中残留的 Markdown 强调符号 (*, **, _, __)")
    ap.add_argument("-i", "--input", help="单个 HTML 文件")
    ap.add_argument("--dir", help="目录：递归处理所有 .html")
    args = ap.parse_args()

    if not args.input and not args.dir:
        ap.error("请指定 --input 或 --dir")

    files: List[Path] = []
    if args.input:
        files.append(Path(args.input).resolve())
    if args.dir:
        files.extend(iter_html_files_in_dir(Path(args.dir).resolve()))

    any_changed = False
    for f in files:
        if not f.exists():
            print(f"SKIP (missing): {f}")
            continue
        changed = clean_html(f)
        any_changed = any_changed or changed
        print(("CLEANED" if changed else "OK"), f)

    raise SystemExit(0 if any_changed else 0)


if __name__ == "__main__":
    main()


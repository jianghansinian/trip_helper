#!/usr/bin/env python3
"""
将 **Markdown**、**纯文本 (.txt)** 或已有 **站内结构 HTML** 译为目标语言并写出完整网页。

位于 ``tools/pagegen/``：文档 → 完整 HTML；版式、外链 CSS、导航与 **i18n 壳层**（语言选择器、
``data-i18n``、底部脚本）由 ``translate.py`` 模板与 ``site_css_links`` 一并生成，无需再单独跑
``add-i18n-support.py``。

链路：本地写稿 → 本工具 → 可选 ``tools/deploy/deploy.py``（``--deploy``）。

输入
    - **.md**：YAML front matter 可选（title / description / date / source_url / featured_emoji / theme / blog_variant）。
    - **.txt**：若以 ``---`` 开头则与 .md 相同；否则**首行为标题**，其余为正文（内置轻量转 HTML，不走 markdown 库）。
    - **.html**：整页翻译。

用法: ``python3 tools/pagegen/doc_to_html.py``；默认任务 YAML：``tools/pagegen/doc_to_html.yaml``。
旧入口 ``tools/i18n/article_to_target_page.py`` 已废弃；请直接使用本脚本。

环境变量: DEEPSEEK_API_KEY / OPENAI_API_KEY 等（与 translate.py 一致）。
"""

from __future__ import annotations

import argparse
import asyncio
import html
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

# 裸 URL 识别（http/https）；右括号等从 URL 末尾剥掉，避免 “(https://…)” 整段被吃进
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\")]+")
_SKIP_LINKIFY_ANCESTOR_TAGS = frozenset({"a", "head"})
_SKIP_LINKIFY_PARENT_TAGS = frozenset(
    {"a", "code", "pre", "kbd", "script", "style", "noscript", "textarea", "title"}
)

# LLM 译文中常见的「手势 / 指向 / 鼓掌 / 祈祷手」等符号（含肤色修饰 + VS16）
_HAND_EMOJI_BASE_CODEPOINTS: Tuple[int, ...] = (
    0x1F449,
    0x1F448,
    0x1F446,
    0x1F447,
    0x261D,
    0x270A,
    0x270B,
    0x270C,
    0x270D,
    0x1F44C,
    0x1F44D,
    0x1F44E,
    0x1F44A,
    0x1F44F,
    0x1F450,
    0x1F485,
    0x1F590,
    0x1F596,
    0x1F64C,
    0x1F64F,
    0x1F90C,
    0x1F90F,
    0x1F918,
    0x1F919,
    0x1F91A,
    0x1F91D,
    0x1F91E,
    0x1F91F,
    0x1FAF5,
)


def _compile_hand_emoji_strip_pattern() -> re.Pattern[str]:
    parts: List[str] = []
    for cp in _HAND_EMOJI_BASE_CODEPOINTS:
        parts.append(
            re.escape(chr(cp)) + r"(?:\uFE0F|[\U0001F3FB-\U0001F3FF](?:\uFE0F)?)?"
        )
    return re.compile("|".join(parts))


_HAND_EMOJI_STRIP_RE = _compile_hand_emoji_strip_pattern()


def strip_llm_hand_emoji(text: str) -> str:
    """去掉译文中常见的 GPT 式手势 emoji，并收紧多余空格。"""
    if not text:
        return text
    s = _HAND_EMOJI_STRIP_RE.sub("", text)
    s = re.sub(r"[ \t\f\v]{2,}", " ", s)
    s = re.sub(r" *\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def strip_markdown_emphasis_artifacts(text: str) -> str:
    """
    去掉 LLM 偶尔输出的 Markdown 强调符号（*, **, _, __）。
    目标是把 “**Reddit**” 这种残留变成 “Reddit”，而不是保留星号进入最终 HTML。
    """
    if not text:
        return text
    s = text
    # **bold** / __bold__
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    # *italic* / _italic_（尽量避免吃掉列表符号“* ”）
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", s)
    return s


_PAGEGEN_DIR = Path(__file__).resolve().parent
_URL_TRANSLATE = _PAGEGEN_DIR.parent / "url-translate"
_REPO_ROOT = _PAGEGEN_DIR.parent.parent
_DEPLOY_SCRIPT = _REPO_ROOT / "tools" / "deploy" / "deploy.py"
_DEFAULT_JOB_YAML = _PAGEGEN_DIR / "doc_to_html.yaml"
def _default_article_config_path() -> Path:
    if _DEFAULT_JOB_YAML.is_file():
        return _DEFAULT_JOB_YAML
    return _DEFAULT_JOB_YAML

if str(_URL_TRANSLATE) not in sys.path:
    sys.path.insert(0, str(_URL_TRANSLATE))
_TOOLS_DIR = _PAGEGEN_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import site_css_links  # noqa: E402

VISITOR_VOICE_INSTRUCTIONS = """\
- Audience: international travelers to China who need practical, trustworthy information.
- Voice: clear, direct, and human — like a careful travel writer or tips page, not marketing or “AI narration.”
- Avoid: filler, stock metaphors, and generic openers (e.g. “In today’s digital age”, “whether you’re a seasoned traveler”, “delve”, “unlock”, “tapestry”, “it’s important to note”).
- Do not use hand / finger / applause / “praying hands” emojis (e.g. 👉 👍 👏 🙌 🙏 ✍️) or similar emoji callouts; use plain punctuation instead.
- Prefer: short sentences, concrete detail, and actionable guidance (what to do, what to expect, what to watch for).
- Do not invent facts: no made-up venues, prices, laws, or opening hours.
- Keep every [SEGMENT_N]…[/SEGMENT_N] marker identical in numbering and spelling."""

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

try:
    import markdown as mdlib
except ImportError:
    mdlib = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _explicit_article_config_in_argv(argv: List[str]) -> bool:
    return any(a == "--article-config" or a.startswith("--article-config=") for a in argv)


def preparse_article_config_argv(argv: List[str]) -> Tuple[Path, List[str]]:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--article-config", default=str(_default_article_config_path()))
    ns, rest = ap.parse_known_args(argv)
    return Path(ns.article_config), rest


def load_article_job_yaml(path: Path) -> Dict:
    if yaml is None:
        raise RuntimeError("读取文章任务配置需要 PyYAML（pip install pyyaml）")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("文章任务 YAML 根节点必须是映射（键值表）")
    return data


def _resolve_job_path(job_dir: Path, p: str) -> Path:
    pp = Path(p)
    if pp.is_absolute():
        return pp.resolve()
    for base in (job_dir, _REPO_ROOT, Path.cwd()):
        cand = (base / pp).resolve()
        if cand.exists():
            return cand
    return (job_dir / pp).resolve()


def _translate_mod():
    import translate as tr

    return tr


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _has_latin_letters(s: str) -> bool:
    return any(c.isalpha() and ord(c) < 128 for c in s)


def should_translate_leaf(text: str, source_lang: str) -> bool:
    if not text or not text.strip():
        return False
    sl = (source_lang or "auto").lower()
    if sl in ("zh", "zh-cn", "zh_cn", "chinese"):
        return _has_cjk(text)
    if sl in ("en", "english"):
        return _has_latin_letters(text) and not _has_cjk(text)
    return _has_cjk(text) or _has_latin_letters(text)


def _skip_parent(tag: Optional[Tag]) -> bool:
    if tag is None:
        return True
    name = tag.name.lower() if tag.name else ""
    return name in ("script", "style", "noscript", "textarea", "code", "pre")


def _fmt_safe(s: str) -> str:
    """Escape braces so str.format on HTML_TEMPLATE does not interpret content."""
    return s.replace("{", "{{").replace("}", "}}")


def _parse_front_matter_block(text: str) -> Tuple[Dict[str, str], str]:
    """If text starts with YAML front matter, return (meta, body); else ({}, text)."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    raw_fm = m.group(1)
    body = text[m.end() :].strip()
    meta: Dict[str, str] = {}
    if yaml:
        try:
            loaded = yaml.safe_load(raw_fm)
            if isinstance(loaded, dict):
                meta = {str(k): "" if v is None else str(v) for k, v in loaded.items()}
        except Exception:
            logger.warning("YAML front matter 解析失败，按行解析")
    if not meta:
        for line in raw_fm.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def _title_from_first_heading(body: str) -> Tuple[str, str]:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            rest = "\n".join(lines[i + 1 :]).lstrip("\n")
            return title, rest
    return "", body


def parse_local_markdown(raw: str) -> Tuple[Dict[str, str], str]:
    meta, body = _parse_front_matter_block(raw.strip())
    if not meta.get("title"):
        t, body = _title_from_first_heading(body)
        if t:
            meta["title"] = t
    if not meta.get("title"):
        raise ValueError("Markdown 缺少标题：请在 front matter 中写 title:，或正文首行使用 # 标题")
    return meta, body


def parse_local_txt(raw: str) -> Tuple[Dict[str, str], str]:
    """首行标题 + 正文；若全文以 --- 开头则按 Markdown 方式解析 YAML front matter。"""
    t = raw.strip("\ufeff").strip()
    if not t:
        raise ValueError("TXT 文件为空")
    if t.startswith("---"):
        return parse_local_markdown(t)
    lines = t.splitlines()
    title = lines[0].strip()
    if not title:
        raise ValueError("TXT 首行不能为空（用作标题）")
    body = "\n".join(lines[1:]).lstrip("\n")
    return {"title": title}, body


def _strip_trailing_url_punct(url: str) -> str:
    u = url.rstrip(".,;:!?'\")」』，。；、）】")
    return u


def _linkify_split_text(s: str) -> List[Tuple[str, str]]:
    """拆成 ('text'|'url', 片段)，用于把正文里的裸 URL 变成超链接。"""
    out: List[Tuple[str, str]] = []
    i = 0
    for m in _PLAIN_URL_RE.finditer(s):
        if m.start() > i:
            out.append(("text", s[i : m.start()]))
        raw = m.group(0)
        u = _strip_trailing_url_punct(raw)
        if u.startswith(("http://", "https://")):
            out.append(("url", u))
        else:
            out.append(("text", raw))
        i = m.end()
    if i < len(s):
        out.append(("text", s[i:]))
    return out if out else [("text", s)]


def _linkify_nodes_from_pieces(pieces: List[Tuple[str, str]], soup: BeautifulSoup) -> List:
    bits: List = []
    for kind, chunk in pieces:
        if kind == "text":
            bits.append(NavigableString(chunk))
        else:
            a = soup.new_tag("a", href=chunk, target="_blank", rel="noopener noreferrer")
            a.append(NavigableString(chunk))
            bits.append(a)
    return bits


def _linkify_container(root: Tag, soup: BeautifulSoup) -> None:
    for text in list(root.find_all(string=True, recursive=True)):
        if any(text.find_parent(n) for n in _SKIP_LINKIFY_ANCESTOR_TAGS):
            continue
        parent = text.parent
        if parent is not None and parent.name in _SKIP_LINKIFY_PARENT_TAGS:
            continue
        raw = str(text)
        if "http://" not in raw and "https://" not in raw:
            continue
        pieces = _linkify_split_text(raw)
        if len(pieces) == 1 and pieces[0][0] == "text":
            continue
        text.replace_with(*_linkify_nodes_from_pieces(pieces, soup))


def linkify_plain_urls_in_html(html: str) -> str:
    """把正文中未加 <a> 的 http(s) 地址转为可点击链接（译稿里模型常输出纯文本 URL）。"""
    soup = BeautifulSoup(html, "html.parser")
    _linkify_container(soup, soup)
    return str(soup)


def linkify_html_fragment_only(fragment: str) -> str:
    """仅处理 Markdown 转出的 HTML 片段。"""
    soup = BeautifulSoup(f"<div>{fragment}</div>", "html.parser")
    div = soup.find("div")
    if div is None:
        return fragment
    _linkify_container(div, soup)
    inner = "".join(str(c) for c in div.children)
    return inner or fragment


def _inline_markdown_to_html(s: str) -> str:
    t = html.escape(s)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _fallback_markdown_to_html(body: str) -> str:
    """无 markdown 包时的轻量转换（标题、段落、无序列表）。"""
    lines = body.splitlines()
    out: List[str] = []
    i = 0
    para: List[str] = []

    def flush_para() -> None:
        nonlocal para
        if not para:
            return
        text = "\n".join(para).strip()
        if text:
            inner = _inline_markdown_to_html(text).replace("\n", "<br>\n")
            out.append(f"<p>{inner}</p>")
        para = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        hm = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if hm:
            flush_para()
            level = len(hm.group(1))
            tag = min(level + 1, 6)
            inner = _inline_markdown_to_html(hm.group(2).strip())
            out.append(f"<h{tag}>{inner}</h{tag}>")
            i += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            flush_para()
            items: List[str] = []
            while i < len(lines):
                s2 = lines[i].strip()
                if re.match(r"^[-*]\s+", s2):
                    items.append(_inline_markdown_to_html(re.sub(r"^[-*]\s+", "", s2)))
                    i += 1
                elif not s2:
                    i += 1
                    break
                else:
                    break
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue
        om = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if om:
            flush_para()
            items = [_inline_markdown_to_html(om.group(2))]
            i += 1
            while i < len(lines):
                s2 = lines[i].strip()
                m2 = re.match(r"^(\d+)\.\s+(.*)$", s2)
                if m2:
                    items.append(_inline_markdown_to_html(m2.group(2)))
                    i += 1
                elif not s2:
                    i += 1
                    break
                else:
                    break
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue
        para.append(line)
        i += 1
    flush_para()
    return "\n".join(out)


def markdown_body_to_html_fragment(body: str, *, force_fallback: bool = False) -> str:
    if force_fallback:
        frag = _fallback_markdown_to_html(body)
        return linkify_html_fragment_only(frag)
    if mdlib:
        try:
            frag = mdlib.markdown(body, extensions=["extra"])
        except Exception as e:
            logger.warning("markdown 包解析失败（%s），回退内置转换", e)
            frag = _fallback_markdown_to_html(body)
    else:
        logger.info("未安装 markdown 包，使用内置轻量 Markdown 转换（建议: pip install markdown）")
        frag = _fallback_markdown_to_html(body)
    return linkify_html_fragment_only(frag)


def effective_source_lang(source_lang: str, title: str, body: str) -> str:
    sl = (source_lang or "auto").lower()
    if sl != "auto":
        return source_lang
    sample = f"{title}\n{body}"
    if _has_cjk(sample):
        return "zh"
    if _has_latin_letters(sample):
        return "en"
    return "en"


def build_html_from_markdown(
    meta: Dict[str, str],
    body: str,
    source_lang: str,
    output_path: Path,
    *,
    force_fallback_body: bool = False,
) -> str:
    tr = _translate_mod()
    fragment = markdown_body_to_html_fragment(body, force_fallback=force_fallback_body)
    title = meta["title"]
    eff = effective_source_lang(source_lang, title, body)
    lang_code = eff.split("-")[0].lower() if eff else "en"
    lang_names = getattr(tr, "LANG_NAMES", {})
    lang_display = lang_names.get(lang_code, lang_names.get(eff.lower(), eff))
    fetched = meta.get("date") or time.strftime("%B %d, %Y", time.localtime())
    source_url = meta.get("source_url", "#")
    emoji = meta.get("featured_emoji") or "📰"
    featured_image = f'<div class="article-featured-placeholder">{html.escape(emoji)}</div>'
    tpl = getattr(tr, "HTML_TEMPLATE", None)
    if not tpl:
        raise RuntimeError("translate 模块缺少 HTML_TEMPLATE，无法从 Markdown 生成页面")

    _theme_raw = meta.get("theme")
    _guides_theme = (
        str(_theme_raw).strip() if _theme_raw is not None and str(_theme_raw).strip() else None
    )
    ctx = site_css_links.page_template_fields(
        output_path.resolve(),
        _REPO_ROOT,
        guides_theme=_guides_theme,
        blog_variant=meta.get("blog_variant"),
    )
    ctx_fmt = {k: _fmt_safe(v) for k, v in ctx.items()}

    # 正文 HTML 中可能含花括号，不能交给 str.format，用占位符再替换
    _slot = "__ATP_MD_BODY_SLOT__"
    html_page = tpl.format(
        title=_fmt_safe(title),
        source_url=_fmt_safe(source_url),
        fetched=_fmt_safe(fetched),
        lang=_fmt_safe(lang_code),
        lang_display=_fmt_safe(str(lang_display)),
        featured_image=featured_image,
        content=_slot,
        **ctx_fmt,
    )
    html_page = html_page.replace(_slot, fragment, 1)
    soup = BeautifulSoup(html_page, "html.parser")
    desc = meta.get("description")
    if desc:
        mtag = soup.find("meta", attrs={"name": lambda x: x and str(x).lower() == "description"})
        if mtag:
            mtag["content"] = desc
    return str(soup)


def _article_regions(soup: BeautifulSoup, scope: str) -> List[Tag]:
    scope = (scope or "article").lower()
    if scope == "full":
        body = soup.body
        return [body] if body else []

    regions: List[Tag] = []
    main = soup.select_one(".article-main") or soup.find("article")
    if main:
        for sub in main.select(".article-header, .article-content"):
            regions.append(sub)
    if not regions:
        for sel in (".article-header", ".article-content"):
            regions.extend(soup.select(sel))
    if not regions:
        one = soup.select_one(".article-content") or soup.find("main")
        if one:
            regions.append(one)
    if not regions and soup.body:
        regions.append(soup.body)
    return [r for r in regions if r is not None]


def collect_text_groups(
    roots: List[Tag],
) -> List[Tuple[NavigableString, str, List[Tuple[NavigableString, str]]]]:
    """按父节点合并相邻文本节点，返回 (代表节点, 合并串, 原始节点列表)。"""
    parent_groups: dict[int, List[Tuple[NavigableString, str]]] = {}

    for root in roots:
        for element in root.descendants:
            if not isinstance(element, NavigableString):
                continue
            parent = element.parent
            if _skip_parent(parent):
                continue
            original = str(element)
            if not original:
                continue
            pid = id(parent)
            parent_groups.setdefault(pid, []).append((element, original))

    out: List[Tuple[NavigableString, str, List[Tuple[NavigableString, str]]]] = []
    for nodes in parent_groups.values():
        if len(nodes) == 1:
            out.append((nodes[0][0], nodes[0][1], nodes))
        else:
            merged = "".join(t for _, t in nodes)
            out.append((nodes[0][0], merged, nodes))
    return out


def filter_by_source(
    groups: List[Tuple[NavigableString, str, List[Tuple[NavigableString, str]]]],
    source_lang: str,
) -> List[Tuple[NavigableString, str, List[Tuple[NavigableString, str]]]]:
    return [(a, b, c) for a, b, c in groups if should_translate_leaf(b, source_lang)]


SEGMENT_RE = re.compile(r"\[SEGMENT_(\d+)\](.*?)\[/SEGMENT_\1\]", re.DOTALL)


async def translate_marked_segments(translator, segments: List[str]) -> List[str]:
    if not segments:
        return []
    parts = []
    for i, text in enumerate(segments):
        parts.append(f"[SEGMENT_{i}]")
        parts.append(text)
        parts.append(f"[/SEGMENT_{i}]")
    combined = "".join(parts)

    if hasattr(translator, "_translate_batch"):
        raw = await translator._translate_batch(combined)
    else:
        raw = await translator.translate(combined)

    found = {int(k): v for k, v in SEGMENT_RE.findall(raw)}
    out: List[str] = []
    for i in range(len(segments)):
        if i in found:
            out.append(found[i])
        else:
            logger.warning("段落 %s 未在译文中找到标记，保留原文", i)
            out.append(segments[i])
    return out


async def translate_html_file(
    html: str,
    translator,
    source_lang: str,
    scope: str,
) -> str:
    soup = BeautifulSoup(html, "html.parser")

    segments: List[str] = []
    head_setters: List[Callable[[str], None]] = []

    head = soup.find("head")
    if head:
        title_tag = head.find("title")
        if title_tag:
            ttxt = title_tag.get_text()
            if should_translate_leaf(ttxt, source_lang):
                segments.append(ttxt)

                def _set_title(val: str, tt=title_tag) -> None:
                    tt.clear()
                    tt.append(val)

                head_setters.append(_set_title)

        meta_desc = head.find("meta", attrs={"name": lambda x: x and str(x).lower() == "description"})
        if meta_desc and meta_desc.get("content"):
            c = meta_desc["content"]
            if should_translate_leaf(c, source_lang):
                segments.append(c)

                def _set_meta(val: str, m=meta_desc) -> None:
                    m["content"] = val

                head_setters.append(_set_meta)

    roots = _article_regions(soup, scope)
    groups = filter_by_source(collect_text_groups(roots), source_lang)
    body_texts = [g[1] for g in groups]
    segments.extend(body_texts)

    if not segments:
        logger.warning("没有需要翻译的文本，输出原文件")
        return html

    translated = await translate_marked_segments(translator, segments)
    translated = [strip_markdown_emphasis_artifacts(strip_llm_hand_emoji(t)) for t in translated]

    hi = 0
    for fn in head_setters:
        fn(translated[hi])
        hi += 1

    body_translated = translated[hi:]

    for idx, (rep, _merged, lst) in enumerate(groups):
        new_text = body_translated[idx] if idx < len(body_translated) else _merged
        ph = f"___ATP{idx:05d}___"
        rep.replace_with(ph)
        for other, _ in lst[1:]:
            if other.parent:
                other.extract()

    out_html = str(soup)
    for idx in range(len(groups)):
        ph = f"___ATP{idx:05d}___"
        new_text = body_translated[idx] if idx < len(body_translated) else groups[idx][1]
        out_html = out_html.replace(ph, new_text)

    soup2 = BeautifulSoup(out_html, "html.parser")
    html_el = soup2.find("html")
    if html_el:
        tgt = translator.config.target_lang or "en"
        html_el["lang"] = tgt.split("-")[0]
        if tgt.lower().startswith("ar"):
            html_el["dir"] = "rtl"
        elif html_el.get("dir") == "rtl":
            del html_el["dir"]

    return linkify_plain_urls_in_html(str(soup2))


def load_config(path: Optional[str], source: str, target: str, backend: Optional[str]):
    tr = _translate_mod()
    if path and Path(path).exists():
        cfg = tr.Config.from_yaml(path)
    else:
        cfg = tr.Config()
    cfg.source_lang = source
    cfg.target_lang = target
    if backend:
        cfg.backend = backend
    return cfg


def main() -> None:
    argv = sys.argv[1:]
    ac_path, rest = preparse_article_config_argv(argv)
    explicit_job_cfg = _explicit_article_config_in_argv(argv)
    if explicit_job_cfg and not ac_path.is_file():
        logger.error("文章任务配置不存在: %s", ac_path)
        sys.exit(1)

    recipe: Dict = {}
    job_dir = _REPO_ROOT
    if ac_path.is_file():
        try:
            recipe = load_article_job_yaml(ac_path)
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("%s", e)
            sys.exit(1)
        job_dir = ac_path.parent

    parser = argparse.ArgumentParser(
        description="将 Markdown、.txt 或站内 .html 译为目标语言完整网页（含 i18n 壳层；复用 url-translate）；可 deploy",
        epilog=(
            f"任务参数可写在 {_DEFAULT_JOB_YAML.name}（默认路径 tools/pagegen/），"
            "用 --article-config 指定其它文件；命令行优先于 YAML。"
        ),
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="输入：.md / .txt / 站内结构 .html（可与 YAML 的 input 二选一，命令行优先）",
    )
    parser.add_argument("-o", "--output", default=None, help="输出 HTML（默认同目录 {stem}.{target}.html）")
    parser.add_argument(
        "--source",
        "-s",
        default=None,
        help="源语言: zh | en | auto（默认 auto 或见文章 YAML）",
    )
    parser.add_argument(
        "--target",
        "-t",
        default=None,
        help="目标语言代码，如 en, zh, ja, ko（可与 YAML 的 target 二选一，命令行优先）",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="translate 后端 YAML，默认 tools/url-translate/config.yaml 或文章 YAML 的 translate_config",
    )
    parser.add_argument("--backend", "-b", default=None, help="覆盖配置中的 backend")
    parser.add_argument(
        "--scope",
        choices=("article", "full"),
        default=None,
        help="article：正文区；full：整页 body（默认 article 或见 YAML）",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="减少日志")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="写盘成功后调用 tools/deploy/deploy.py 部署该 HTML",
    )
    parser.add_argument(
        "--deploy-auto",
        action="store_true",
        help="与 --deploy 合用：传给 deploy.py --auto，自动判断 blog/guides",
    )
    parser.add_argument(
        "--deploy-target",
        choices=("blog", "guides"),
        help="与 --deploy 合用：指定 deploy.py --target（与 --deploy-auto 互斥时以后者为准）",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="启用 LLM 改写模式（写入 config.rewrite_mode；需 deepseek 等后端，simple 无效）",
    )
    parser.add_argument(
        "--visitor-voice",
        action="store_true",
        help="改写时面向外国游客润色：去 AI 腔、具体务实；隐含打开 rewrite_mode，并附加文风说明",
    )
    args = parser.parse_args(rest)

    if args.quiet or bool(recipe.get("quiet")):
        logging.getLogger().setLevel(logging.WARNING)

    raw_input = args.input or recipe.get("input")
    if not raw_input:
        logger.error("请指定输入：-i/--input，或在文章 YAML 中设置 input:")
        sys.exit(1)
    in_path = _resolve_job_path(job_dir, str(raw_input)).resolve()
    if not in_path.exists():
        logger.error("输入文件不存在: %s", in_path)
        sys.exit(1)

    target = args.target or recipe.get("target")
    if not target:
        logger.error("请指定目标语言：-t/--target，或在文章 YAML 中设置 target:")
        sys.exit(1)

    source = args.source if args.source is not None else str(recipe.get("source", "auto"))
    scope = args.scope or str(recipe.get("scope", "article"))
    if scope not in ("article", "full"):
        scope = "article"

    translate_cfg = args.config or recipe.get("translate_config")
    if not translate_cfg:
        translate_cfg = str(_URL_TRANSLATE / "config.yaml")
    tc_path = _resolve_job_path(job_dir, str(translate_cfg))
    cfg_path = str(tc_path) if tc_path.exists() else None
    if not tc_path.exists():
        logger.warning("未找到 translate 配置: %s，将使用内存默认 Config", tc_path)

    backend = args.backend or recipe.get("backend")

    deploy = bool(recipe.get("deploy", False)) or args.deploy
    deploy_auto = bool(recipe.get("deploy_auto", False)) or args.deploy_auto
    deploy_target_cli = args.deploy_target
    deploy_target_yaml = recipe.get("deploy_target")
    want_rewrite = bool(recipe.get("rewrite", False)) or args.rewrite
    want_visitor = bool(recipe.get("visitor_voice", False)) or args.visitor_voice
    extra_from_yaml = recipe.get("rewrite_extra_instructions")

    if args.deploy_auto and args.deploy_target:
        logger.warning("同时指定了 --deploy-auto 与 --deploy-target，将使用 --deploy-auto")

    cfg = load_config(cfg_path, source, str(target), str(backend) if backend else None)

    if want_visitor or want_rewrite:
        cfg.rewrite_mode = True
    if extra_from_yaml:
        cfg.rewrite_extra_instructions = str(extra_from_yaml).strip()
    elif want_visitor:
        cfg.rewrite_extra_instructions = VISITOR_VOICE_INSTRUCTIONS

    tr = _translate_mod()
    if args.quiet or bool(recipe.get("quiet")):
        tr.logger.setLevel(logging.WARNING)

    if (want_rewrite or want_visitor) and cfg.backend.lower() == "simple":
        logger.warning("当前 backend 为 simple，不支持 LLM 改写；请改用 deepseek/openai 等")

    translator = tr.create_translator(cfg)
    suffix = in_path.suffix.lower()

    out_raw = args.output or recipe.get("output")
    if out_raw:
        out_path = _resolve_job_path(job_dir, str(out_raw)).resolve()
    else:
        out_path = in_path.parent / f"{in_path.stem}.{target}.html"
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if suffix == ".md":
            meta, md_body = parse_local_markdown(in_path.read_text(encoding="utf-8"))
            html = build_html_from_markdown(meta, md_body, source, out_path)
        elif suffix == ".txt":
            meta, txt_body = parse_local_txt(in_path.read_text(encoding="utf-8"))
            html = build_html_from_markdown(
                meta, txt_body, source, out_path, force_fallback_body=True
            )
        else:
            html = in_path.read_text(encoding="utf-8")
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    try:
        out_html = asyncio.run(translate_html_file(html, translator, source, scope))
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
    out_path.write_text(out_html, encoding="utf-8")
    logger.info("已写入: %s", out_path)

    if deploy:
        if not _DEPLOY_SCRIPT.is_file():
            logger.error("未找到部署脚本: %s", _DEPLOY_SCRIPT)
            sys.exit(1)
        cmd = [sys.executable, str(_DEPLOY_SCRIPT), "--file", str(out_path)]
        if deploy_auto:
            cmd.append("--auto")
        elif deploy_target_cli:
            cmd.extend(["--target", deploy_target_cli])
        elif deploy_target_yaml in ("blog", "guides"):
            cmd.extend(["--target", str(deploy_target_yaml)])
        else:
            cmd.extend(["--target", "blog"])
        logger.info("运行部署: %s", " ".join(cmd))
        proc = subprocess.run(cmd, cwd=str(_REPO_ROOT))
        if proc.returncode != 0:
            sys.exit(proc.returncode)


if __name__ == "__main__":
    main()

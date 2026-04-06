"""Shared CSS <link> lines and nav/breadcrumb hrefs from the target HTML path under the site root."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

# guides/* 文章：与站内既有页一致的主题（可被 page_template_fields 的 guides_theme 覆盖）
_GUIDE_ARTICLE_THEMES = {
    "visa-guide.html": "visa",
    "high-speed-rail.html": "transport",
    "essential-apps.html": "green",
}


def path_prefix_to_site_root(output_file: Path, site_root: Path) -> str:
    """Prefix of ../ from the HTML file's directory to site root (empty if file is at root)."""
    try:
        parent_rel = output_file.resolve().parent.relative_to(site_root.resolve())
    except ValueError:
        return ""
    return "../" * len(parent_rel.parts)


def css_link_block(prefix: str, *sheet_basenames: str) -> str:
    lines = [
        f'    <link rel="stylesheet" href="{prefix}css/{name}.css"/>' for name in sheet_basenames
    ]
    return "\n".join(lines)


def _theme_class(theme: str) -> str:
    t = (theme or "orange").lower().strip()
    return {
        "green": "theme-green",
        "visa": "theme-visa",
        "transport": "theme-transport",
        "orange": "theme-orange",
    }.get(t, "theme-orange")


def page_template_fields(
    output_path: Path,
    site_root: Path,
    *,
    guides_theme: Optional[str] = None,
    blog_variant: Optional[str] = None,
) -> Dict[str, str]:
    """
    Values for translate.HTML_TEMPLATE str.format(...).

    guides_theme: front matter theme for guides/* (green|visa|transport|orange).
        If None, uses _GUIDE_ARTICLE_THEMES by filename when applicable, else orange.
    blog_variant: optional \"wechat\" for blog/* body class page-blog-wechat.
    """
    pt = path_prefix_to_site_root(output_path, site_root)
    try:
        rel = output_path.resolve().relative_to(site_root.resolve())
    except ValueError:
        rel = None
    parts = rel.parts if rel else ()
    top = parts[0].lower() if parts else ""
    filename = output_path.name.lower()

    in_blog = top == "blog"
    in_guides = top == "guides"
    is_blog_index = in_blog and filename == "index.html"
    is_guides_index = in_guides and filename == "index.html"
    in_blog_story = in_blog and not is_blog_index

    if is_blog_index:
        css_links = css_link_block(pt, "main", "blog")
        body_class = "page-blog-index"
        article_class = "article-main"
    elif in_blog_story:
        css_links = css_link_block(pt, "main", "guides", "blog")
        bv = (blog_variant or "").lower().strip()
        if bv == "wechat":
            body_class = "page-article page-blog-wechat"
        else:
            body_class = "page-article page-blog-story"
        article_class = "article-main"
    elif is_guides_index:
        css_links = css_link_block(pt, "main", "guides")
        body_class = "page-guides-index"
        article_class = "article-main"
    elif in_guides:
        css_links = css_link_block(pt, "main", "guides")
        body_class = "page-article"
        gt = (
            guides_theme
            if guides_theme is not None
            else _GUIDE_ARTICLE_THEMES.get(output_path.name.lower(), "orange")
        )
        article_class = f"article-main {_theme_class(gt)}"
    else:
        css_links = css_link_block(pt, "main", "guides")
        body_class = "page-article"
        g_fallback = guides_theme if guides_theme is not None else "orange"
        article_class = f"article-main {_theme_class(g_fallback)}"

    idx = f"{pt}index.html"

    if in_guides:
        href_nav_guides = f"{pt}index.html#guides"
        href_nav_stories = f"{pt}blog/index.html"
        href_nav_visa = "index.html#visa"
        href_nav_practical = "index.html"
        href_bc_section = "index.html"
        bc_section_label = "Travel Guides"
    elif in_blog:
        href_nav_guides = f"{pt}index.html#guides"
        href_nav_stories = "index.html"
        href_nav_visa = f"{pt}guides/index.html#visa"
        href_nav_practical = f"{pt}guides/index.html"
        href_bc_section = "index.html"
        bc_section_label = "Travel Stories"
    else:
        href_nav_guides = f"{pt}index.html#guides"
        href_nav_stories = f"{pt}blog/index.html"
        href_nav_visa = f"{pt}index.html#visa"
        href_nav_practical = idx
        href_bc_section = f"{pt}blog/index.html"
        bc_section_label = "Travel Stories"

    href_home = idx
    href_footer_home = idx

    return {
        "css_links": css_links,
        "body_class": body_class,
        "article_class": article_class,
        "href_home": href_home,
        "href_footer_home": href_footer_home,
        "href_nav_guides": href_nav_guides,
        "href_nav_stories": href_nav_stories,
        "href_nav_visa": href_nav_visa,
        "href_nav_practical": href_nav_practical,
        "href_bc_home": href_home,
        "href_bc_section": href_bc_section,
        "bc_section_label": bc_section_label,
        "href_widget_guides": f"{pt}index.html#guides",
        "href_widget_visa": f"{pt}index.html#visa",
        "href_widget_culture": f"{pt}index.html#culture",
        "href_widget_blog": f"{pt}blog/index.html",
    }


def ensure_site_css_links(soup, file_path: Path, site_root: Path) -> bool:
    """
    If <head> has no link to css/main.css, insert the same stylesheet set as page_template_fields
    would use for this path. Returns True if links were added.
    """
    from bs4 import BeautifulSoup

    head = soup.head
    if not head:
        return False
    for link in head.find_all("link", rel="stylesheet"):
        href = (link.get("href") or "").replace("\\", "/")
        if "css/main.css" in href or href.endswith("/main.css"):
            return False

    fields = page_template_fields(file_path, site_root)
    frag = BeautifulSoup(f"<head>{fields['css_links']}</head>", "html.parser")
    frag_head = frag.find("head")
    if not frag_head:
        return False
    inserted = False
    for link in reversed(list(frag_head.find_all("link"))):
        head.insert(0, link)
        inserted = True
    return inserted


def in_guides_folder(path: Path, site_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(site_root.resolve())
    except ValueError:
        return False
    return len(rel.parts) > 0 and rel.parts[0].lower() == "guides"

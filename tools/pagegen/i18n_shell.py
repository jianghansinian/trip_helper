"""与 guides/essential-apps 等页一致的语言选择器 + 底部 i18n 脚本（路径前缀随页面深度变化）。"""

from __future__ import annotations


def language_selector_li_html() -> str:
    """插入到 <ul class="nav-menu"> 末尾的 <li>（含语言下拉）。"""
    return """<li class="language-selector">
                    <button type="button" class="language-btn" onclick="toggleLanguageDropdown()">
                        🌐 <span id="current-lang-name">English</span> ▼
                    </button>
                    <div class="language-dropdown" id="language-dropdown">
                        <div class="language-option active" onclick="changeLanguage('en')">🇬🇧 English</div>
                        <div class="language-option" onclick="changeLanguage('ko')">🇰🇷 한국어</div>
                        <div class="language-option" onclick="changeLanguage('ja')">🇯🇵 日本語</div>
                        <div class="language-option" onclick="changeLanguage('ru')">🇷🇺 Русский</div>
                        <div class="language-option" onclick="changeLanguage('de')">🇩🇪 Deutsch</div>
                        <div class="language-option" onclick="changeLanguage('fr')">🇫🇷 Français</div>
                        <div class="language-option" onclick="changeLanguage('es')">🇪🇸 Español</div>
                        <div class="language-option" onclick="changeLanguage('it')">🇮🇹 Italiano</div>
                        <div class="language-option" onclick="changeLanguage('ar')">🇸🇦 العربية</div>
                    </div>
                </li>"""


def body_scripts_html(path_prefix_to_site_root: str) -> str:
    """
    path_prefix_to_site_root: site_css_links.path_prefix_to_site_root 的返回值（'' 或 '../' 等）。
    与 guides/essential-apps.html 底部一致；逻辑在 js/common-i18n-scripts.js。
    """
    p = path_prefix_to_site_root
    return f"""    <script src="{p}js/translations.js" defer></script>
    <script src="{p}js/i18n.js" defer></script>
    <script src="{p}js/common-i18n-scripts.js" defer></script>
    <script src="{p}js/article-content-i18n.js" defer></script>"""

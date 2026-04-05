#!/usr/bin/env python3
"""
自动为所有HTML页面添加多语言支持

功能：
1. 扫描所有HTML文件
2. 自动添加语言选择器
3. 自动添加翻译脚本引用
4. 标记常见的UI元素

用法：
    python3 tools/i18n/add-i18n-support.py
    python3 tools/i18n/add-i18n-support.py --dir blog  # 只处理blog目录
    python3 tools/i18n/add-i18n-support.py --file index.html  # 只处理单个文件
"""

import os
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup, Comment
from typing import List, Optional

# 语言选择器HTML（插入到导航菜单）
LANGUAGE_SELECTOR_HTML = '''<li class="language-selector">
                    <button class="language-btn" onclick="toggleLanguageDropdown()">
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
                </li>'''

# 语言选择器CSS（插入到style标签）
LANGUAGE_SELECTOR_CSS = '''
        /* Language Selector */
        .language-selector {
            position: relative;
            margin-left: 1rem;
        }

        .language-btn {
            background: transparent;
            border: 2px solid #c41e3a;
            color: #c41e3a;
            padding: 0.5rem 1rem;
            border-radius: 25px;
            cursor: pointer;
            font-size: 0.9rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            transition: all 0.3s;
        }

        .language-btn:hover {
            background: #c41e3a;
            color: white;
        }

        .language-dropdown {
            position: absolute;
            top: 100%;
            right: 0;
            margin-top: 0.5rem;
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            min-width: 180px;
            display: none;
            z-index: 1000;
            overflow: hidden;
        }

        .language-dropdown.show {
            display: block;
        }

        .language-option {
            padding: 0.75rem 1rem;
            cursor: pointer;
            transition: background 0.2s;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .language-option:hover {
            background: #f5f5f5;
        }

        .language-option.active {
            background: #fff3e0;
            color: #c41e3a;
            font-weight: 600;
        }

        body[dir="rtl"] .language-dropdown {
            right: auto;
            left: 0;
        }'''

# JavaScript代码（插入到script标签前）
I18N_SCRIPT_HTML = '''    <script src="js/translations.js"></script>
    <script src="js/i18n.js"></script>
    <script src="js/article-i18n.js"></script>
    <script>
        // 语言选择器功能
        function toggleLanguageDropdown() {
            const dropdown = document.getElementById('language-dropdown');
            if (dropdown) dropdown.classList.toggle('show');
        }

        // 点击外部关闭下拉菜单
        document.addEventListener('click', function(event) {
            const selector = document.querySelector('.language-selector');
            const dropdown = document.getElementById('language-dropdown');
            if (dropdown && selector && !selector.contains(event.target)) {
                dropdown.classList.remove('show');
            }
        });

        // 切换语言
        function changeLanguage(lang) {
            if (typeof i18n !== 'undefined') {
                i18n.setLanguage(lang);
            }
            
            // 更新当前语言显示
            const langNames = {
                'en': 'English',
                'ko': '한국어',
                'ja': '日本語',
                'ru': 'Русский',
                'de': 'Deutsch',
                'fr': 'Français',
                'es': 'Español',
                'it': 'Italiano',
                'ar': 'العربية'
            };
            const langNameEl = document.getElementById('current-lang-name');
            if (langNameEl) {
                langNameEl.textContent = langNames[lang] || 'English';
            }
            
            // 更新活动选项
            document.querySelectorAll('.language-option').forEach(opt => {
                opt.classList.remove('active');
                if (opt.getAttribute('onclick') === `changeLanguage('${lang}')`) {
                    opt.classList.add('active');
                }
            });
            
            // 关闭下拉菜单
            const dropdown = document.getElementById('language-dropdown');
            if (dropdown) dropdown.classList.remove('show');

            // 翻译文章内容
            if (typeof articleTranslator !== 'undefined') {
                articleTranslator.translatePage(lang);
            }
        }

        // 初始化
        document.addEventListener('DOMContentLoaded', function() {
            if (typeof i18n !== 'undefined') {
                const langNames = {
                    'en': 'English',
                    'ko': '한국어',
                    'ja': '日本語',
                    'ru': 'Русский',
                    'de': 'Deutsch',
                    'fr': 'Français',
                    'es': 'Español',
                    'it': 'Italiano',
                    'ar': 'العربية'
                };
                const currentLang = i18n.getCurrentLanguage();
                const langNameEl = document.getElementById('current-lang-name');
                if (langNameEl) {
                    langNameEl.textContent = langNames[currentLang] || 'English';
                }

                // 设置活动语言选项
                document.querySelectorAll('.language-option').forEach(opt => {
                    opt.classList.remove('active');
                    if (opt.getAttribute('onclick') === `changeLanguage('${currentLang}')`) {
                        opt.classList.add('active');
                    }
                });
            }
        });
    </script>'''


def has_i18n_support(html_content: str) -> bool:
    """检查HTML是否已经有i18n支持"""
    return 'js/i18n.js' in html_content or 'language-selector' in html_content


def add_language_selector(soup: BeautifulSoup) -> bool:
    """添加语言选择器到导航菜单"""
    # 查找导航菜单
    nav_menu = soup.find('ul', class_='nav-menu')
    if not nav_menu:
        return False
    
    # 检查是否已经有语言选择器
    if soup.find(class_='language-selector'):
        return False
    
    # 解析语言选择器HTML
    lang_selector = BeautifulSoup(LANGUAGE_SELECTOR_HTML, 'html.parser')
    nav_menu.append(lang_selector)
    return True


def add_language_css(soup: BeautifulSoup) -> bool:
    """添加语言选择器的CSS"""
    # 查找style标签
    style_tag = soup.find('style')
    if not style_tag:
        # 创建style标签
        style_tag = soup.new_tag('style')
        soup.head.insert(0, style_tag)
    
    # 检查是否已经有语言选择器CSS
    if 'language-selector' in style_tag.string:
        return False
    
    # 添加CSS
    if style_tag.string:
        style_tag.string += LANGUAGE_SELECTOR_CSS
    else:
        style_tag.string = LANGUAGE_SELECTOR_CSS
    return True


def add_i18n_scripts(soup: BeautifulSoup) -> bool:
    """添加i18n相关脚本"""
    # 检查是否已经有i18n脚本
    if soup.find('script', src=re.compile('js/i18n.js')):
        return False
    
    # 查找body结束标签前的script标签，或者body结束标签
    body = soup.find('body')
    if not body:
        return False
    
    # 查找最后一个script标签或body结束位置
    scripts = body.find_all('script', recursive=False)
    if scripts:
        # 在最后一个script后插入
        scripts[-1].insert_after(BeautifulSoup(I18N_SCRIPT_HTML, 'html.parser'))
    else:
        # 在body结束前插入
        body.append(BeautifulSoup(I18N_SCRIPT_HTML, 'html.parser'))
    
    return True


def update_nav_menu_alignment(soup: BeautifulSoup) -> bool:
    """更新导航菜单样式，确保语言选择器正确对齐"""
    style_tag = soup.find('style')
    if not style_tag:
        return False
    
    # 检查是否已经有.nav-menu的align-items设置
    if 'align-items: center' in style_tag.string:
        # 更新为包含align-items: center
        pattern = r'\.nav-menu\s*\{[^}]*\}'
        match = re.search(pattern, style_tag.string, re.DOTALL)
        if match:
            nav_menu_css = match.group(0)
            if 'align-items' not in nav_menu_css:
                # 在.nav-menu样式中添加align-items
                new_css = re.sub(r'(\{)([^}]*)(\})', 
                               r'\1\2\nalign-items: center;\n\3', 
                               nav_menu_css, count=1)
                style_tag.string = style_tag.string.replace(nav_menu_css, new_css)
                return True
    return False


def process_html_file(file_path: Path) -> dict:
    """处理单个HTML文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # 检查是否已经有i18n支持
        if has_i18n_support(html_content):
            return {
                'file': str(file_path),
                'status': 'skipped',
                'reason': 'Already has i18n support'
            }
        
        soup = BeautifulSoup(html_content, 'html.parser')
        changes = []
        
        # 添加语言选择器
        if add_language_selector(soup):
            changes.append('Added language selector')
        
        # 添加CSS
        if add_language_css(soup):
            changes.append('Added language selector CSS')
        
        # 更新导航菜单样式
        if update_nav_menu_alignment(soup):
            changes.append('Updated nav menu alignment')
        
        # 添加脚本
        if add_i18n_scripts(soup):
            changes.append('Added i18n scripts')
        
        if changes:
            # 保存文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(str(soup))
            return {
                'file': str(file_path),
                'status': 'updated',
                'changes': changes
            }
        else:
            return {
                'file': str(file_path),
                'status': 'no_changes',
                'reason': 'No changes needed'
            }
    
    except Exception as e:
        return {
            'file': str(file_path),
            'status': 'error',
            'error': str(e)
        }


def find_html_files(directory: Path) -> List[Path]:
    """查找所有HTML文件"""
    html_files = []
    
    skip_parts = {'node_modules', '.git', '__pycache__'}
    
    for file_path in directory.rglob('*.html'):
        parts = file_path.parts
        if skip_parts & set(parts):
            continue
        if 'url-translate' in parts and 'translated_articles' in parts:
            continue
        html_files.append(file_path)
    
    return html_files


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='为HTML页面添加多语言支持')
    parser.add_argument('--dir', help='要处理的目录（默认：当前目录）')
    parser.add_argument('--file', help='要处理的单个文件')
    parser.add_argument('--dry-run', action='store_true', help='仅显示将要进行的更改，不实际修改文件')
    
    args = parser.parse_args()
    
    # 确定要处理的文件
    base_dir = Path(__file__).parent.parent
    html_files = []
    
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = base_dir / file_path
        if file_path.exists():
            html_files = [file_path]
        else:
            print(f"❌ 文件不存在: {file_path}")
            sys.exit(1)
    elif args.dir:
        dir_path = Path(args.dir)
        if not dir_path.is_absolute():
            dir_path = base_dir / dir_path
        if dir_path.exists() and dir_path.is_dir():
            html_files = find_html_files(dir_path)
        else:
            print(f"❌ 目录不存在: {dir_path}")
            sys.exit(1)
    else:
        # 默认处理根目录下的所有HTML文件
        html_files = find_html_files(base_dir)
    
    if not html_files:
        print("❌ 没有找到HTML文件")
        sys.exit(1)
    
    print(f"📁 找到 {len(html_files)} 个HTML文件\n")
    
    if args.dry_run:
        print("🔍 预览模式（不会实际修改文件）\n")
    
    results = []
    for file_path in html_files:
        if args.dry_run:
            result = process_html_file(file_path)
            if result['status'] == 'updated':
                print(f"✅ {file_path.name}: {', '.join(result.get('changes', []))}")
            elif result['status'] == 'skipped':
                print(f"⏭️  {file_path.name}: {result.get('reason', '')}")
        else:
            result = process_html_file(file_path)
            results.append(result)
    
    if not args.dry_run:
        print("\n📊 处理结果:")
        updated = sum(1 for r in results if r['status'] == 'updated')
        skipped = sum(1 for r in results if r['status'] == 'skipped')
        errors = sum(1 for r in results if r['status'] == 'error')
        
        print(f"✅ 更新: {updated}")
        print(f"⏭️  跳过: {skipped}")
        if errors > 0:
            print(f"❌ 错误: {errors}")
            for r in results:
                if r['status'] == 'error':
                    print(f"   - {r['file']}: {r.get('error', '')}")


if __name__ == '__main__':
    main()


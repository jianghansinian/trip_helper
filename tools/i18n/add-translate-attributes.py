#!/usr/bin/env python3
"""
为现有文章添加 data-translate="true" 属性
自动识别需要翻译的内容（标题、副标题、正文段落等）

用法：
    # 为单篇文章添加翻译标记
    python3 tools/i18n/add-translate-attributes.py --article blog/sichuan_hotpot.html

    # 为目录下所有文章添加翻译标记
    python3 tools/i18n/add-translate-attributes.py --dir blog

    # 只标记标题和元数据，不标记正文
    python3 tools/i18n/add-translate-attributes.py --dir blog --meta-only
"""

import os
import sys
import argparse
from pathlib import Path
from bs4 import BeautifulSoup

# 需要添加翻译标记的选择器
TRANSLATABLE_SELECTORS = {
    'title': ['h1.article-title', 'h1', '.article-title'],
    'subtitle': ['.article-subtitle', 'p.article-subtitle'],
    'meta_description': ['meta[name="description"]'],
    'content_paragraphs': ['.article-content p', 'article p', 'main p'],
    'headings': ['.article-content h2', '.article-content h3', 'article h2', 'article h3'],
    'breadcrumb': ['.breadcrumb span:last-child']
}

# 不需要翻译的选择器（排除）
EXCLUDE_SELECTORS = [
    '.article-meta',
    '.meta-item',
    '.article-meta-compact',
    'footer',
    'nav',
    'script',
    'style',
    '.ad-slot',
    '.widget',
    '.sidebar'
]


class ArticleTranslatorMarker:
    """为文章添加翻译标记"""
    
    def __init__(self, meta_only: bool = False):
        self.meta_only = meta_only
    
    def should_translate(self, element) -> bool:
        """判断元素是否需要翻译"""
        # 检查是否在排除列表中
        for exclude_selector in EXCLUDE_SELECTORS:
            if element.find_parent(exclude_selector):
                return False
        
        # 检查是否已经有翻译标记
        if element.get('data-translate'):
            return False
        
        # 检查文本内容
        text = element.get_text(strip=True)
        if not text or len(text) < 3:
            return False
        
        # 排除纯数字、日期等
        if text.replace('.', '').replace(',', '').replace(':', '').replace('-', '').isdigit():
            return False
        
        return True
    
    def mark_article(self, html_file: Path) -> bool:
        """为文章添加翻译标记"""
        print(f"\n📄 处理: {html_file.name}")
        
        try:
            # 读取HTML
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            soup = BeautifulSoup(html_content, 'html.parser')
            marked_count = 0
            
            # 标记标题
            for selector in TRANSLATABLE_SELECTORS['title']:
                elements = soup.select(selector)
                for elem in elements:
                    if self.should_translate(elem):
                        elem['data-translate'] = 'true'
                        marked_count += 1
                        print(f"  ✓ 标记标题: {elem.get_text(strip=True)[:50]}...")
            
            # 标记副标题
            for selector in TRANSLATABLE_SELECTORS['subtitle']:
                elements = soup.select(selector)
                for elem in elements:
                    if self.should_translate(elem):
                        elem['data-translate'] = 'true'
                        marked_count += 1
                        print(f"  ✓ 标记副标题: {elem.get_text(strip=True)[:50]}...")
            
            # 标记meta description
            meta_desc = soup.find('meta', {'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                meta_desc['data-translate'] = 'true'
                marked_count += 1
                print(f"  ✓ 标记meta描述")
            
            # 如果不是meta_only模式，标记正文
            if not self.meta_only:
                # 标记正文段落
                for selector in TRANSLATABLE_SELECTORS['content_paragraphs']:
                    elements = soup.select(selector)
                    for elem in elements:
                        if self.should_translate(elem):
                            elem['data-translate'] = 'true'
                            marked_count += 1
                
                # 标记标题（h2, h3）
                for selector in TRANSLATABLE_SELECTORS['headings']:
                    elements = soup.select(selector)
                    for elem in elements:
                        if self.should_translate(elem):
                            elem['data-translate'] = 'true'
                            marked_count += 1
                
                print(f"  ✓ 标记了 {len(soup.select('.article-content p[data-translate="true"]'))} 个段落")
                print(f"  ✓ 标记了 {len(soup.select('.article-content h2[data-translate="true"], .article-content h3[data-translate="true"]'))} 个标题")
            
            # 保存文件
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(str(soup))
            
            print(f"  ✅ 完成！共标记 {marked_count} 个元素")
            return True
            
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            return False
    
    def add_translation_script(self, html_file: Path) -> bool:
        """在文章页面添加翻译脚本引用"""
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # 检查是否已经添加了脚本
            if 'article-content-i18n.js' in html_content:
                return False
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 查找body标签
            body = soup.find('body')
            if not body:
                return False
            
            # 检查是否已经有script标签
            scripts = body.find_all('script')
            has_translation_script = any('article-content-i18n.js' in str(s) for s in scripts)
            
            if not has_translation_script:
                # 添加翻译脚本
                script_tag = soup.new_tag('script', src='../js/article-content-i18n.js')
                body.append(script_tag)
                
                # 保存文件
                with open(html_file, 'w', encoding='utf-8') as f:
                    f.write(str(soup))
                
                print(f"  ✓ 已添加翻译脚本引用")
                return True
            
            return False
            
        except Exception as e:
            print(f"  ⚠️  添加脚本时出错: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description='为文章添加翻译标记')
    parser.add_argument('--article', type=str, help='要处理的文章文件路径')
    parser.add_argument('--dir', type=str, help='要处理的目录')
    parser.add_argument('--meta-only', action='store_true',
                       help='只标记元数据（标题、描述），不标记正文')
    parser.add_argument('--add-script', action='store_true',
                       help='同时添加翻译脚本引用')
    
    args = parser.parse_args()
    
    if not args.article and not args.dir:
        parser.print_help()
        return
    
    marker = ArticleTranslatorMarker(meta_only=args.meta_only)
    
    # 处理单篇文章
    if args.article:
        article_file = Path(args.article)
        if not article_file.exists():
            print(f"❌ 文件不存在: {article_file}")
            return
        
        marker.mark_article(article_file)
        if args.add_script:
            marker.add_translation_script(article_file)
    
    # 处理目录
    elif args.dir:
        dir_path = Path(args.dir)
        if not dir_path.exists():
            print(f"❌ 目录不存在: {dir_path}")
            return
        
        html_files = [f for f in dir_path.glob('*.html') if f.name != 'index.html']
        print(f"📁 找到 {len(html_files)} 篇文章")
        
        for html_file in html_files:
            marker.mark_article(html_file)
            if args.add_script:
                marker.add_translation_script(html_file)
    
    print("\n✅ 处理完成！")


if __name__ == '__main__':
    main()


#!/usr/bin/env python3
"""
多语言文章翻译工具
为现有文章生成多语言版本

用法：
    # 翻译单篇文章到所有语言
    python3 tools/i18n/multi_lang_translator.py --article blog/sichuan_hotpot.html

    # 翻译目录下所有文章
    python3 tools/i18n/multi_lang_translator.py --dir blog --langs ko,ja,ru,de,fr,es,ar

    # 只翻译标题和元数据（快速模式）
    python3 tools/i18n/multi_lang_translator.py --article blog/sichuan_hotpot.html --meta-only
"""

import os
import sys
import re
import argparse
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Dict, List, Optional

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    from translate import TranslatorBackend, Config
    HAS_TRANSLATE = True
except:
    HAS_TRANSLATE = False
    print("⚠️  警告: 无法导入翻译模块，将使用占位符")

# 支持的语言
SUPPORTED_LANGS = {
    'ko': 'Korean',
    'ja': 'Japanese', 
    'ru': 'Russian',
    'de': 'German',
    'fr': 'French',
    'es': 'Spanish',
    'ar': 'Arabic'
}

# 语言代码映射（用于翻译API）
LANG_CODE_MAP = {
    'ko': 'ko',
    'ja': 'ja',
    'ru': 'ru',
    'de': 'de',
    'fr': 'fr',
    'es': 'es',
    'ar': 'ar'
}


class ArticleMultiLangTranslator:
    """文章多语言翻译器"""
    
    def __init__(self, backend: str = 'googletrans', api_key: Optional[str] = None):
        self.backend = backend
        self.api_key = api_key
        self.translator = None
        
        if HAS_TRANSLATE:
            config = Config()
            config.backend = backend
            if api_key:
                if backend == 'deepl':
                    config.deepl_api_key = api_key
                elif backend == 'openai':
                    config.openai_api_key = api_key
            self.translator = TranslatorBackend(config)
    
    def translate_text(self, text: str, target_lang: str) -> str:
        """翻译文本"""
        if not self.translator:
            return f"[{target_lang}] {text}"  # 占位符
        
        try:
            lang_code = LANG_CODE_MAP.get(target_lang, target_lang)
            # 这里需要根据实际的翻译后端调用
            # 示例：使用googletrans
            if self.backend == 'googletrans':
                from googletrans import Translator
                translator = Translator()
                result = translator.translate(text, dest=lang_code)
                return result.text
            else:
                # 其他后端...
                return text
        except Exception as e:
            print(f"⚠️  翻译失败 ({target_lang}): {e}")
            return text
    
    def extract_translatable_content(self, soup: BeautifulSoup) -> Dict:
        """提取需要翻译的内容"""
        content = {
            'title': '',
            'subtitle': '',
            'description': '',
            'meta_description': '',
            'breadcrumb': '',
            'content_paragraphs': []
        }
        
        # 提取标题
        title_elem = soup.find('h1', class_='article-title')
        if title_elem:
            content['title'] = title_elem.get_text(strip=True)
        
        # 提取副标题
        subtitle_elem = soup.find('p', class_='article-subtitle')
        if subtitle_elem:
            content['subtitle'] = subtitle_elem.get_text(strip=True)
        
        # 提取meta description
        meta_desc = soup.find('meta', {'name': 'description'})
        if meta_desc:
            content['meta_description'] = meta_desc.get('content', '')
        
        # 提取正文段落
        article_content = soup.find('div', class_='article-content')
        if article_content:
            paragraphs = article_content.find_all('p')
            content['content_paragraphs'] = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
        
        return content
    
    def translate_article(self, html_file: Path, target_langs: List[str], meta_only: bool = False) -> Dict[str, Path]:
        """翻译文章到多种语言"""
        print(f"\n📄 处理文章: {html_file.name}")
        
        # 读取原始HTML
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 提取需要翻译的内容
        content = self.extract_translatable_content(soup)
        
        translated_files = {}
        
        for lang in target_langs:
            print(f"  🌐 翻译到 {SUPPORTED_LANGS[lang]}...")
            
            # 创建新的soup副本
            lang_soup = BeautifulSoup(html_content, 'html.parser')
            
            # 翻译标题
            if content['title']:
                title_elem = lang_soup.find('h1', class_='article-title')
                if title_elem:
                    translated_title = self.translate_text(content['title'], lang)
                    title_elem.string = translated_title
                    print(f"    ✓ 标题: {translated_title[:50]}...")
            
            # 翻译副标题
            if content['subtitle']:
                subtitle_elem = lang_soup.find('p', class_='article-subtitle')
                if subtitle_elem:
                    translated_subtitle = self.translate_text(content['subtitle'], lang)
                    subtitle_elem.string = translated_subtitle
            
            # 翻译meta description
            if content['meta_description']:
                meta_desc = lang_soup.find('meta', {'name': 'description'})
                if meta_desc:
                    translated_desc = self.translate_text(content['meta_description'], lang)
                    meta_desc['content'] = translated_desc
            
            # 翻译正文（如果不是meta_only模式）
            if not meta_only and content['content_paragraphs']:
                article_content = lang_soup.find('div', class_='article-content')
                if article_content:
                    paragraphs = article_content.find_all('p')
                    for i, p in enumerate(paragraphs):
                        if i < len(content['content_paragraphs']):
                            translated_text = self.translate_text(content['content_paragraphs'][i], lang)
                            p.string = translated_text
                    print(f"    ✓ 已翻译 {len(paragraphs)} 个段落")
            
            # 更新HTML lang属性
            html_tag = lang_soup.find('html')
            if html_tag:
                html_tag['lang'] = lang
                if lang == 'ar':
                    html_tag['dir'] = 'rtl'
            
            # 保存翻译后的文件
            output_file = html_file.parent / f"{html_file.stem}.{lang}.html"
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(str(lang_soup))
            
            translated_files[lang] = output_file
            print(f"    ✅ 已保存: {output_file.name}")
        
        return translated_files


def main():
    parser = argparse.ArgumentParser(description='为文章生成多语言版本')
    parser.add_argument('--article', type=str, help='要翻译的文章文件路径')
    parser.add_argument('--dir', type=str, help='要翻译的目录')
    parser.add_argument('--langs', type=str, default='ko,ja,ru,de,fr,es,ar',
                       help='要翻译的语言（逗号分隔）')
    parser.add_argument('--backend', type=str, default='googletrans',
                       choices=['googletrans', 'deepl', 'openai'],
                       help='翻译后端')
    parser.add_argument('--api-key', type=str, help='API密钥（如果需要）')
    parser.add_argument('--meta-only', action='store_true',
                       help='只翻译元数据（标题、描述），不翻译正文')
    
    args = parser.parse_args()
    
    if not args.article and not args.dir:
        parser.print_help()
        return
    
    # 解析目标语言
    target_langs = [lang.strip() for lang in args.langs.split(',')]
    invalid_langs = [lang for lang in target_langs if lang not in SUPPORTED_LANGS]
    if invalid_langs:
        print(f"❌ 不支持的语言: {invalid_langs}")
        return
    
    # 创建翻译器
    translator = ArticleMultiLangTranslator(backend=args.backend, api_key=args.api_key)
    
    # 处理单篇文章
    if args.article:
        article_file = Path(args.article)
        if not article_file.exists():
            print(f"❌ 文件不存在: {article_file}")
            return
        
        translator.translate_article(article_file, target_langs, args.meta_only)
    
    # 处理目录
    elif args.dir:
        dir_path = Path(args.dir)
        if not dir_path.exists():
            print(f"❌ 目录不存在: {dir_path}")
            return
        
        html_files = [f for f in dir_path.glob('*.html') if f.name != 'index.html']
        print(f"📁 找到 {len(html_files)} 篇文章")
        
        for html_file in html_files:
            try:
                translator.translate_article(html_file, target_langs, args.meta_only)
            except Exception as e:
                print(f"❌ 处理 {html_file.name} 时出错: {e}")
                continue
    
    print("\n✅ 翻译完成！")


if __name__ == '__main__':
    main()


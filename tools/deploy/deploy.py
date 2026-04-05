#!/usr/bin/env python3
"""
自动部署脚本：将翻译后的文章部署到blog或guides目录，并更新主页和索引页

功能：
1. 扫描translated_articles目录中的HTML文件
2. 提取文章元数据（标题、描述、日期等）
3. 根据配置或内容判断是blog还是guides
4. 将文件移动到对应目录
5. 更新index.html、blog/index.html和guides/index.html中的文章列表
6. 保持最新的文章在主页显示
7. 删除已部署的文章并从索引页中移除

用法：
    # 方式1：自动判断blog或guides（推荐）
    python3 tools/deploy/deploy.py --auto

    # 方式2：指定部署到blog目录
    python3 tools/deploy/deploy.py --target blog

    # 方式3：指定部署到guides目录
    python3 tools/deploy/deploy.py --target guides

    # 方式4：部署单个文件
    python3 tools/deploy/deploy.py --file tools/url-translate/translated_articles/article.html --target blog

    # 方式5：指定源目录
    python3 tools/deploy/deploy.py --source-dir tools/url-translate/translated_articles --auto

    # 方式6：删除文章
    python3 tools/deploy/deploy.py --delete blog/article.html
    python3 tools/deploy/deploy.py --delete guides/article.html
    python3 tools/deploy/deploy.py --delete article.html --target blog
"""

import os
import re
import sys
import argparse
import shutil
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Tuple
import json

# 配置
_REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = _REPO_ROOT / 'blog'
GUIDES_DIR = _REPO_ROOT / 'guides'
INDEX_HTML = _REPO_ROOT / 'index.html'
BLOG_INDEX = BLOG_DIR / 'index.html'
GUIDES_INDEX = GUIDES_DIR / 'index.html'
TRANSLATED_DIR = _REPO_ROOT / 'tools' / 'url-translate' / 'translated_articles'

# Blog和Guides的关键词（用于自动分类）
BLOG_KEYWORDS = ['story', 'experience', 'adventure', 'journey', 'trip', 'travel', 'personal', 
                 'narrative', 'tale', 'moment', 'encounter', 'memory', '回忆', '故事', '经历']
GUIDES_KEYWORDS = ['guide', 'how to', 'tutorial', 'tips', 'advice', 'information', 'complete',
                   'essential', 'visa', 'transport', 'app', 'vpn', 'food', 'ordering',
                   '攻略', '指南', '如何', '教程', '信息']

# 主页显示的文章数量
MAX_HOMEPAGE_STORIES = 5
MAX_HOMEPAGE_GUIDES = 5


class ArticleMetadata:
    """文章元数据"""
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.title = ""
        self.description = ""
        self.date = datetime.now().strftime('%B %d, %Y')
        self.read_time = "10 min read"
        self.location = ""
        self.category = "TRAVEL STORY"
        self.icon = "📖"
        self.content_preview = ""
        self.source_url = ""
        
    def extract_from_html(self) -> bool:
        """从HTML文件中提取元数据"""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 提取标题
            title_tag = soup.find('title')
            if title_tag:
                self.title = title_tag.get_text().replace(' - Travel-China.Help', '').strip()
                # 清理标题中的特殊字符
                self.title = re.sub(r'\*\*', '', self.title)  # 移除markdown格式
            
            # 从article-title类中提取标题（更准确）
            article_title = soup.find(class_='article-title')
            if article_title:
                self.title = article_title.get_text().strip()
            
            # 提取描述
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                self.description = meta_desc.get('content').strip()
            
            # 提取日期
            meta_date = soup.find(class_='meta-item')
            if meta_date:
                date_text = meta_date.get_text()
                # 尝试提取日期
                date_match = re.search(r'(\w+ \d{1,2}, \d{4})', date_text)
                if date_match:
                    self.date = date_match.group(1)
            
            # 提取内容预览（前200个字符）
            content_div = soup.find(class_='article-content')
            if content_div:
                text = content_div.get_text(strip=True)
                self.content_preview = text[:200] + '...' if len(text) > 200 else text
                # 估算阅读时间（假设每分钟200字）
                word_count = len(text.split())
                self.read_time = f"{max(3, word_count // 200)} min read"
            
            # 提取源URL
            source_info = soup.find(class_='source-info')
            if source_info:
                source_link = source_info.find('a')
                if source_link:
                    self.source_url = source_link.get('href', '')
            
            # 如果没有描述，使用内容预览
            if not self.description:
                self.description = self.content_preview[:150] + '...' if len(self.content_preview) > 150 else self.content_preview
            
            return True
        except Exception as e:
            print(f"❌ 提取元数据失败 {self.file_path}: {e}")
            return False
    
    def determine_category(self) -> str:
        """根据标题和内容判断是blog还是guides"""
        title_lower = self.title.lower()
        desc_lower = self.description.lower()
        content_lower = self.content_preview.lower()
        
        # 检查guides关键词
        for keyword in GUIDES_KEYWORDS:
            if keyword in title_lower or keyword in desc_lower or keyword in content_lower:
                return 'guides'
        
        # 检查blog关键词
        for keyword in BLOG_KEYWORDS:
            if keyword in title_lower or keyword in desc_lower or keyword in content_lower:
                return 'blog'
        
        # 默认：如果包含"guide"、"how"、"tutorial"等，归为guides
        if any(word in title_lower for word in ['guide', 'how to', 'tutorial', 'tips', 'visa', 'app']):
            return 'guides'
        
        # 否则归为blog
        return 'blog'
    
    def get_icon(self) -> str:
        """根据标题内容选择合适的图标"""
        title_lower = self.title.lower()
        
        icon_map = {
            'visa': '🛂', 'passport': '🛂',
            'train': '🚄', 'rail': '🚄', 'transport': '🚄',
            'app': '📱', 'wechat': '📱', 'alipay': '📱',
            'vpn': '🌐', 'internet': '🌐',
            'food': '🍜', 'restaurant': '🍜', 'dining': '🍜', 'hotpot': '🌶️',
            'mountain': '⛰️', 'hiking': '🥾', 'climb': '⛰️',
            'city': '🏛️', 'beijing': '🏛️', 'shanghai': '🌃', 'chengdu': '🐼',
            'story': '📖', 'experience': '❤️', 'adventure': '🥾',
            'funny': '😂', 'humor': '😂',
            'culture': '🎭', 'cultural': '🎭',
        }
        
        for keyword, icon in icon_map.items():
            if keyword in title_lower:
                return icon
        
        return '📖'  # 默认图标


def safe_filename(title: str) -> str:
    """生成安全的文件名"""
    # 移除特殊字符
    filename = re.sub(r'[<>:"/\\|?*]', '', title)
    filename = re.sub(r'\s+', '_', filename)
    filename = filename.strip('_')
    # 限制长度
    if len(filename) > 100:
        filename = filename[:100]
    return filename or 'article'


def extract_article_section(html_content: str, section_class: str) -> List[str]:
    """从HTML中提取文章列表部分"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找文章列表容器
    article_list = soup.find(class_='article-list')
    if not article_list:
        return []
    
    # 提取所有文章项
    items = article_list.find_all(class_='article-list-item', recursive=False)
    return [str(item) for item in items]


def insert_article_to_list(html_content: str, article_html: str, max_items: int = None) -> str:
    """将新文章插入到文章列表的开头"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找文章列表容器
    article_list = soup.find(class_='article-list')
    if not article_list:
        return html_content
    
    # 解析新文章HTML
    new_article = BeautifulSoup(article_html, 'html.parser')
    new_item = new_article.find(class_='article-list-item')
    if not new_item:
        return html_content
    
    # 获取现有文章项
    existing_items = article_list.find_all(class_='article-list-item', recursive=False)
    
    # 移除所有现有项
    for item in existing_items:
        item.decompose()
    
    # 插入新文章到开头
    article_list.insert(0, new_item)
    
    # 重新插入现有文章（限制数量）
    if max_items:
        for item in existing_items[:max_items - 1]:
            article_list.append(item)
    else:
        for item in existing_items:
            article_list.append(item)
    
    return str(soup)


def generate_article_list_item(article: ArticleMetadata, target_dir: str, from_index: str = 'root') -> str:
    """生成文章列表项的HTML
    
    Args:
        article: 文章元数据
        target_dir: 目标目录 (blog/guides)
        from_index: 从哪个索引页调用 ('root', 'blog', 'guides')
    """
    filename = safe_filename(article.title) + '.html'
    
    # 根据调用位置确定相对路径
    if from_index == 'root':
        # 从index.html调用，需要完整路径
        relative_path = f"{target_dir}/{filename}"
    else:
        # 从blog/index.html或guides/index.html调用，只需要文件名
        relative_path = filename
    
    # 根据类别选择标签样式
    tag_class = "tag-story"
    if 'guide' in article.title.lower() or 'visa' in article.title.lower():
        tag_class = "tag-guide"
    elif 'food' in article.title.lower() or 'hotpot' in article.title.lower():
        tag_class = "tag-food"
    elif 'adventure' in article.title.lower() or 'hiking' in article.title.lower():
        tag_class = "tag-adventure"
    
    return f'''            <div class="article-list-item" onclick="window.location.href='{relative_path}'">
                <div class="article-icon">{article.get_icon()}</div>
                <div class="article-info">
                    <span class="article-tag {tag_class}">{article.category}</span>
                    <h3>{article.title}</h3>
                    <p>{article.description}</p>
                    <div class="article-meta-compact">
                        <span>📅 {article.date}</span>
                        <span>⏱️ {article.read_time}</span>
                        {f'<span>📍 {article.location}</span>' if article.location else ''}
                    </div>
                </div>
            </div>'''


def update_index_html(article: ArticleMetadata, target_dir: str, section_id: str):
    """更新index.html中的文章列表 - 主页每个栏目最多显示5篇文章"""
    if not INDEX_HTML.exists():
        print(f"⚠️  {INDEX_HTML} 不存在，跳过更新")
        return
    
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找对应的section
    section = soup.find('section', id=section_id)
    if not section:
        print(f"⚠️  未找到section #{section_id}")
        return
    
    # 查找文章列表
    article_list = section.find(class_='article-list')
    if not article_list:
        print(f"⚠️  未找到文章列表")
        return
    
    # 获取现有文章项并保存它们的HTML字符串（在清除之前）
    existing_items = article_list.find_all(class_='article-list-item', recursive=False)
    existing_items_html = [str(item) for item in existing_items]
    
    # 生成新文章项（从index.html调用，需要完整路径）
    article_html = generate_article_list_item(article, target_dir, from_index='root')
    
    # 清除所有内容（包括文章项和文本节点）
    article_list.clear()
    
    # 插入新文章到开头
    new_item = BeautifulSoup(article_html, 'html.parser')
    article_list.append(new_item)
    
    # 重新插入现有文章（限制数量：主页最多显示5篇）
    max_items = MAX_HOMEPAGE_STORIES if section_id == 'stories' else MAX_HOMEPAGE_GUIDES
    for item_html in existing_items_html[:max_items - 1]:
        existing_item = BeautifulSoup(item_html, 'html.parser')
        article_list.append(existing_item)
    
    # 保存
    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print(f"✅ 已更新 {INDEX_HTML} 的 {section_id} 部分（显示最新 {max_items} 篇）")


def update_blog_index(article: ArticleMetadata):
    """更新blog/index.html - 子页面必须显示所有文章"""
    if not BLOG_INDEX.exists():
        print(f"⚠️  {BLOG_INDEX} 不存在，跳过更新")
        return
    
    with open(BLOG_INDEX, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找文章列表
    article_list = soup.find(class_='article-list')
    if not article_list:
        print(f"⚠️  未找到文章列表")
        return
    
    # 获取现有文章项并保存它们的HTML字符串（在清除之前）
    existing_items = article_list.find_all(class_='article-list-item', recursive=False)
    existing_items_html = [str(item) for item in existing_items]
    
    # 生成新文章项（从blog/index.html调用，只需要文件名）
    article_html = generate_article_list_item(article, 'blog', from_index='blog')
    
    # 清除所有内容（包括文章项和文本节点）
    article_list.clear()
    
    # 插入新文章到开头
    new_item = BeautifulSoup(article_html, 'html.parser')
    article_list.append(new_item)
    
    # 重新插入所有现有文章（子页面显示所有文章，不限制数量）
    for item_html in existing_items_html:
        existing_item = BeautifulSoup(item_html, 'html.parser')
        article_list.append(existing_item)
    
    # 保存
    with open(BLOG_INDEX, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print(f"✅ 已更新 {BLOG_INDEX}（显示所有文章）")


def update_guides_index(article: ArticleMetadata):
    """更新guides/index.html - 子页面必须显示所有文章"""
    if not GUIDES_INDEX.exists():
        print(f"⚠️  {GUIDES_INDEX} 不存在，跳过更新")
        return
    
    with open(GUIDES_INDEX, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 根据文章内容判断应该放在哪个section
    # 这里简化处理，放在第一个合适的section
    sections = soup.find_all('section')
    target_section = None
    
    title_lower = article.title.lower()
    if 'visa' in title_lower:
        target_section = soup.find('section', id='visa')
    elif any(word in title_lower for word in ['train', 'rail', 'transport', 'metro', 'didi']):
        target_section = soup.find('section', id='transport')
    elif any(word in title_lower for word in ['app', 'vpn', 'internet', 'wechat', 'alipay']):
        target_section = soup.find('section', id='tech')
    elif any(word in title_lower for word in ['food', 'dining', 'restaurant', 'ordering']):
        target_section = soup.find('section', id='food')
    elif any(word in title_lower for word in ['city', 'beijing', 'shanghai', 'chengdu']):
        target_section = soup.find('section', id='cities')
    
    # 如果没找到特定section，使用第一个section
    if not target_section and sections:
        target_section = sections[0]
    
    if not target_section:
        print(f"⚠️  未找到合适的section")
        return
    
    # 查找文章列表
    article_list = target_section.find(class_='article-list')
    if not article_list:
        print(f"⚠️  未找到文章列表")
        return
    
    # 获取现有文章项并保存它们的HTML字符串（在清除之前）
    existing_items = article_list.find_all(class_='article-list-item', recursive=False)
    existing_items_html = [str(item) for item in existing_items]
    
    # 生成新文章项（从guides/index.html调用，只需要文件名）
    article_html = generate_article_list_item(article, 'guides', from_index='guides')
    
    # 清除所有内容（包括文章项和文本节点）
    article_list.clear()
    
    # 插入新文章到开头
    new_item = BeautifulSoup(article_html, 'html.parser')
    article_list.append(new_item)
    
    # 重新插入所有现有文章（子页面显示所有文章，不限制数量）
    for item_html in existing_items_html:
        existing_item = BeautifulSoup(item_html, 'html.parser')
        article_list.append(existing_item)
    
    # 保存
    with open(GUIDES_INDEX, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print(f"✅ 已更新 {GUIDES_INDEX}（显示所有文章）")


def fix_article_paths(html_file: Path, target_dir: str):
    """修复文章中的相对路径"""
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    soup = BeautifulSoup(content, 'html.parser')
    
    # 修复导航链接
    # blog和guides目录中的文章都需要../返回根目录
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        if not href or href.startswith('http') or href.startswith('#'):
            continue  # 跳过外部链接和锚点
        
        # 修复index.html链接
        if href == 'index.html' or href.endswith('/index.html'):
            link['href'] = '../index.html'
        # 修复blog/index.html和guides/index.html链接
        elif href == 'blog/index.html':
            link['href'] = '../blog/index.html'
        elif href == 'guides/index.html':
            link['href'] = '../guides/index.html'
        # 修复其他相对路径
        elif href.startswith('blog/') and not href.startswith('../blog/'):
            link['href'] = '../' + href
        elif href.startswith('guides/') and not href.startswith('../guides/'):
            link['href'] = '../' + href
    
    # 修复logo链接
    logo = soup.find(class_='logo')
    if logo and logo.get('href'):
        if logo['href'] == 'index.html':
            logo['href'] = '../index.html'
    
    # 保存
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print(f"✅ 已修复路径: {html_file.name}")


def deploy_article(source_file: Path, target_dir: str, auto_detect: bool = False) -> bool:
    """部署单篇文章"""
    try:
        # 提取元数据
        article = ArticleMetadata(source_file)
        if not article.extract_from_html():
            return False
        
        # 自动判断目标目录
        if auto_detect:
            target_dir = article.determine_category()
        
        # 确定目标目录
        if target_dir == 'blog':
            target_path = BLOG_DIR
            section_id = 'stories'
        elif target_dir == 'guides':
            target_path = GUIDES_DIR
            section_id = 'guides'
        else:
            print(f"❌ 无效的目标目录: {target_dir}")
            return False
        
        # 确保目标目录存在
        target_path.mkdir(parents=True, exist_ok=True)
        
        # 生成目标文件名
        target_filename = safe_filename(article.title) + '.html'
        target_file = target_path / target_filename
        
        # 如果文件已存在，询问是否覆盖
        if target_file.exists():
            print(f"⚠️  文件已存在: {target_file}")
            response = input("是否覆盖? (y/n): ").strip().lower()
            if response != 'y':
                print("⏭️  跳过此文件")
                return False
        
        # 复制文件
        shutil.copy2(source_file, target_file)
        print(f"✅ 已复制文件: {target_file}")
        
        # 修复文章中的路径
        fix_article_paths(target_file, target_dir)
        
        # 更新索引页
        if target_dir == 'blog':
            update_blog_index(article)
        else:
            update_guides_index(article)
        
        # 更新主页
        update_index_html(article, target_dir, section_id)
        
        print(f"✅ 成功部署: {article.title}")
        return True
        
    except Exception as e:
        print(f"❌ 部署失败 {source_file}: {e}")
        import traceback
        traceback.print_exc()
        return False


def rebuild_index(target_dir: str):
    """从文件系统扫描所有文章并重建索引"""
    if target_dir == 'blog':
        target_path = BLOG_DIR
        index_file = BLOG_INDEX
    elif target_dir == 'guides':
        target_path = GUIDES_DIR
        index_file = GUIDES_INDEX
    else:
        print(f"❌ 无效的目标目录: {target_dir}")
        return
    
    if not target_path.exists():
        print(f"❌ 目标目录不存在: {target_path}")
        return
    
    if not index_file.exists():
        print(f"❌ 索引文件不存在: {index_file}")
        return
    
    # 扫描所有HTML文件（排除index.html）
    article_files = [f for f in target_path.glob('*.html') if f.name != 'index.html']
    
    if not article_files:
        print(f"⚠️  未找到文章文件: {target_path}")
        return
    
    print(f"📁 找到 {len(article_files)} 篇文章，开始重建索引...")
    
    # 提取所有文章的元数据
    articles = []
    for article_file in article_files:
        article = ArticleMetadata(article_file)
        if article.extract_from_html():
            articles.append(article)
            print(f"  ✓ {article.title}")
        else:
            print(f"  ✗ 跳过 {article_file.name}（无法提取元数据）")
    
    if not articles:
        print("❌ 没有有效的文章可以添加到索引")
        return
    
    # 按日期排序（最新的在前）
    articles.sort(key=lambda x: x.date, reverse=True)
    
    # 读取索引文件
    with open(index_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    if target_dir == 'blog':
        # blog/index.html 只有一个 article-list
        article_list = soup.find(class_='article-list')
        if article_list:
            # 清除所有内容（包括文章项和文本节点）
            article_list.clear()
            
            # 添加所有文章
            for article in articles:
                article_html = generate_article_list_item(article, 'blog', from_index='blog')
                article_item = BeautifulSoup(article_html, 'html.parser')
                article_list.append(article_item)
            
            print(f"✅ 已重建 {index_file}，包含 {len(articles)} 篇文章")
    else:
        # guides/index.html 有多个 section，每个 section 有自己的 article-list
        # 这里简化处理：将所有文章添加到第一个合适的 section
        sections = soup.find_all('section')
        if sections:
            # 为每篇文章找到合适的 section
            for article in articles:
                title_lower = article.title.lower()
                target_section = None
                
                if 'visa' in title_lower:
                    target_section = soup.find('section', id='visa')
                elif any(word in title_lower for word in ['train', 'rail', 'transport', 'metro', 'didi']):
                    target_section = soup.find('section', id='transport')
                elif any(word in title_lower for word in ['app', 'vpn', 'internet', 'wechat', 'alipay']):
                    target_section = soup.find('section', id='tech')
                elif any(word in title_lower for word in ['food', 'dining', 'restaurant', 'ordering']):
                    target_section = soup.find('section', id='food')
                elif any(word in title_lower for word in ['city', 'beijing', 'shanghai', 'chengdu']):
                    target_section = soup.find('section', id='cities')
                
                if not target_section and sections:
                    target_section = sections[0]
                
                if target_section:
                    article_list = target_section.find(class_='article-list')
                    if article_list:
                        # 检查是否已存在（避免重复）
                        existing_titles = [item.find('h3').get_text() if item.find('h3') else '' 
                                          for item in article_list.find_all(class_='article-list-item', recursive=False)]
                        if article.title not in existing_titles:
                            article_html = generate_article_list_item(article, 'guides', from_index='guides')
                            article_item = BeautifulSoup(article_html, 'html.parser')
                            article_list.append(article_item)
            
            print(f"✅ 已重建 {index_file}，包含 {len(articles)} 篇文章")
    
    # 保存
    with open(index_file, 'w', encoding='utf-8') as f:
        f.write(str(soup))


def rebuild_homepage():
    """重建主页面的索引（从blog和guides目录扫描最新文章）"""
    if not INDEX_HTML.exists():
        print(f"❌ 主页面不存在: {INDEX_HTML}")
        return
    
    print("🔄 开始重建主页面索引...")
    
    # 扫描 blog 目录的文章
    blog_articles = []
    if BLOG_DIR.exists():
        blog_files = [f for f in BLOG_DIR.glob('*.html') if f.name != 'index.html']
        print(f"\n📁 扫描 blog 目录，找到 {len(blog_files)} 篇文章")
        for article_file in blog_files:
            article = ArticleMetadata(article_file)
            if article.extract_from_html():
                blog_articles.append(article)
    
    # 扫描 guides 目录的文章
    guides_articles = []
    if GUIDES_DIR.exists():
        guides_files = [f for f in GUIDES_DIR.glob('*.html') if f.name != 'index.html']
        print(f"📁 扫描 guides 目录，找到 {len(guides_files)} 篇文章")
        for article_file in guides_files:
            article = ArticleMetadata(article_file)
            if article.extract_from_html():
                guides_articles.append(article)
    
    # 按日期排序（最新的在前）
    blog_articles.sort(key=lambda x: x.date, reverse=True)
    guides_articles.sort(key=lambda x: x.date, reverse=True)
    
    # 读取主页面
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 更新 stories section
    stories_section = soup.find('section', id='stories')
    if stories_section:
        article_list = stories_section.find(class_='article-list')
        if article_list:
            # 清除所有内容（包括文章项和文本节点）
            article_list.clear()
            
            # 添加最新的5篇 blog 文章
            for article in blog_articles[:MAX_HOMEPAGE_STORIES]:
                article_html = generate_article_list_item(article, 'blog', from_index='root')
                article_item = BeautifulSoup(article_html, 'html.parser')
                article_list.append(article_item)
            
            print(f"✅ 已更新 stories section，显示 {min(len(blog_articles), MAX_HOMEPAGE_STORIES)} 篇文章")
    
    # 更新 guides section
    guides_section = soup.find('section', id='guides')
    if guides_section:
        article_list = guides_section.find(class_='article-list')
        if article_list:
            # 清除所有内容（包括文章项和文本节点）
            article_list.clear()
            
            # 添加最新的5篇 guides 文章
            for article in guides_articles[:MAX_HOMEPAGE_GUIDES]:
                article_html = generate_article_list_item(article, 'guides', from_index='root')
                article_item = BeautifulSoup(article_html, 'html.parser')
                article_list.append(article_item)
            
            print(f"✅ 已更新 guides section，显示 {min(len(guides_articles), MAX_HOMEPAGE_GUIDES)} 篇文章")
    
    # 保存
    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    
    print(f"\n✅ 已重建主页面索引")


def deploy_all(source_dir: Path, target_dir: str = None, auto_detect: bool = False):
    """部署所有文章"""
    if not source_dir.exists():
        print(f"❌ 源目录不存在: {source_dir}")
        return
    
    # 查找所有HTML文件
    html_files = list(source_dir.glob('*.html'))
    if not html_files:
        print(f"⚠️  未找到HTML文件: {source_dir}")
        return
    
    print(f"📁 找到 {len(html_files)} 个HTML文件")
    
    success_count = 0
    for html_file in html_files:
        print(f"\n📄 处理: {html_file.name}")
        if deploy_article(html_file, target_dir or 'blog', auto_detect):
            success_count += 1
    
    print(f"\n{'='*50}")
    print(f"✅ 成功部署: {success_count}/{len(html_files)}")
    print(f"{'='*50}")


def remove_article_from_index(index_file: Path, article_filename: str, article_title: str = None):
    """从索引页中移除指定文章
    
    Args:
        index_file: 索引文件路径
        article_filename: 文章文件名（用于匹配链接）
        article_title: 文章标题（可选，用于更精确匹配）
    """
    if not index_file.exists():
        return False
    
    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, 'html.parser')
        removed = False
        
        # 查找所有文章列表（可能多个section）
        article_lists = soup.find_all(class_='article-list')
        
        for article_list in article_lists:
            items = article_list.find_all(class_='article-list-item', recursive=False)
            for item in items:
                # 检查onclick属性（文章项使用onclick跳转）
                onclick = item.get('onclick', '') if hasattr(item, 'get') else ''
                
                # 匹配文件名（onclick格式: window.location.href='filename.html' 或 'filename.html'）
                if article_filename in onclick:
                    item.decompose()
                    removed = True
                    continue
                
                # 检查内部链接
                link = item.find('a')
                if link:
                    href = link.get('href', '')
                    if article_filename in href:
                        item.decompose()
                        removed = True
                        continue
                
                # 如果提供了标题，也通过标题匹配
                if article_title:
                    h3 = item.find('h3')
                    if h3:
                        item_title = h3.get_text().strip()
                        # 精确匹配或包含匹配
                        if article_title == item_title or article_title in item_title:
                            item.decompose()
                            removed = True
                            continue
        
        if removed:
            with open(index_file, 'w', encoding='utf-8') as f:
                f.write(str(soup))
            return True
        
        return False
    except Exception as e:
        print(f"⚠️  从 {index_file} 移除文章时出错: {e}")
        return False


def remove_article_from_homepage(article_filename: str, target_dir: str, article_title: str = None):
    """从主页中移除指定文章"""
    if not INDEX_HTML.exists():
        return False
    
    try:
        with open(INDEX_HTML, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        soup = BeautifulSoup(html_content, 'html.parser')
        removed = False
        
        # 确定要检查的section
        section_id = 'stories' if target_dir == 'blog' else 'guides'
        section = soup.find('section', id=section_id)
        
        if section:
            article_list = section.find(class_='article-list')
            if article_list:
                items = article_list.find_all(class_='article-list-item', recursive=False)
                for item in items:
                    # 检查onclick属性（主页使用完整路径 blog/filename.html 或 guides/filename.html）
                    onclick = item.get('onclick', '') if hasattr(item, 'get') else ''
                    
                    # 匹配完整路径
                    full_path = f"{target_dir}/{article_filename}"
                    if full_path in onclick:
                        item.decompose()
                        removed = True
                        continue
                    
                    # 检查内部链接
                    link = item.find('a')
                    if link:
                        href = link.get('href', '')
                        if full_path in href:
                            item.decompose()
                            removed = True
                            continue
                    
                    # 如果提供了标题，也通过标题匹配
                    if article_title:
                        h3 = item.find('h3')
                        if h3:
                            item_title = h3.get_text().strip()
                            # 精确匹配或包含匹配
                            if article_title == item_title or article_title in item_title:
                                item.decompose()
                                removed = True
                                continue
        
        if removed:
            with open(INDEX_HTML, 'w', encoding='utf-8') as f:
                f.write(str(soup))
            return True
        
        return False
    except Exception as e:
        print(f"⚠️  从主页移除文章时出错: {e}")
        return False


def delete_article(article_path: str, target_dir: str = None) -> bool:
    """删除已部署的文章
    
    Args:
        article_path: 文章文件路径（可以是完整路径或文件名）
        target_dir: 目标目录（blog/guides），如果未指定则自动检测
    """
    try:
        # 解析文件路径
        article_file = Path(article_path)
        
        # 如果路径是相对路径且不包含目录，尝试在blog和guides中查找
        if not article_file.is_absolute() and '/' not in article_path and '\\' not in article_path:
            if target_dir:
                # 如果指定了目标目录，在该目录中查找
                if target_dir == 'blog':
                    article_file = BLOG_DIR / article_path
                elif target_dir == 'guides':
                    article_file = GUIDES_DIR / article_path
                else:
                    print(f"❌ 无效的目标目录: {target_dir}")
                    return False
            else:
                # 自动检测：先在blog中查找，再在guides中查找
                blog_file = BLOG_DIR / article_path
                guides_file = GUIDES_DIR / article_path
                
                if blog_file.exists() and guides_file.exists():
                    print(f"⚠️  在blog和guides目录中都找到了文件: {article_path}")
                    print("请使用 --target 参数指定目录，或使用完整路径")
                    return False
                elif blog_file.exists():
                    article_file = blog_file
                    target_dir = 'blog'
                elif guides_file.exists():
                    article_file = guides_file
                    target_dir = 'guides'
                else:
                    print(f"❌ 未找到文件: {article_path}")
                    print(f"   在 {BLOG_DIR} 和 {GUIDES_DIR} 中都没有找到")
                    return False
        else:
            # 完整路径，确定目标目录
            article_file = Path(article_path)
            if not article_file.exists():
                print(f"❌ 文件不存在: {article_file}")
                return False
            
            # 从路径判断目标目录
            if not target_dir:
                if 'blog' in str(article_file):
                    target_dir = 'blog'
                elif 'guides' in str(article_file):
                    target_dir = 'guides'
                else:
                    print(f"⚠️  无法从路径判断目标目录，请使用 --target 参数")
                    return False
        
        # 确认文件存在
        if not article_file.exists():
            print(f"❌ 文件不存在: {article_file}")
            return False
        
        # 提取文章信息（用于从索引中移除）
        article = ArticleMetadata(article_file)
        article.extract_from_html()  # 尝试提取，失败也不影响删除
        
        article_filename = article_file.name
        article_title = article.title if article.title else None
        
        # 确认删除
        print(f"📄 准备删除文章: {article_file}")
        if article_title:
            print(f"   标题: {article_title}")
        print(f"   目录: {target_dir}")
        
        response = input("确认删除? (y/n): ").strip().lower()
        if response != 'y':
            print("⏭️  取消删除")
            return False
        
        # 从索引页中移除
        if target_dir == 'blog':
            if remove_article_from_index(BLOG_INDEX, article_filename, article_title):
                print(f"✅ 已从 {BLOG_INDEX} 中移除")
        else:
            if remove_article_from_index(GUIDES_INDEX, article_filename, article_title):
                print(f"✅ 已从 {GUIDES_INDEX} 中移除")
        
        # 从主页中移除
        if remove_article_from_homepage(article_filename, target_dir, article_title):
            print(f"✅ 已从主页中移除")
        
        # 删除文件
        article_file.unlink()
        print(f"✅ 已删除文件: {article_file}")
        
        print(f"✅ 成功删除文章: {article_title or article_filename}")
        return True
        
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(description='自动部署翻译后的文章')
    parser.add_argument('--source-dir', '-s', 
                       default=str(TRANSLATED_DIR),
                       help='源目录（包含翻译后的HTML文件）')
    parser.add_argument('--target', '-t', 
                       choices=['blog', 'guides'],
                       help='目标目录（blog或guides）')
    parser.add_argument('--auto', '-a', 
                       action='store_true',
                       help='自动判断是blog还是guides')
    parser.add_argument('--file', '-f',
                       help='部署单个文件（而不是整个目录）')
    parser.add_argument('--rebuild', '-r',
                       choices=['blog', 'guides', 'homepage'],
                       help='从文件系统重建索引（blog、guides或homepage）')
    parser.add_argument('--delete', '-d',
                       help='删除已部署的文章（指定文件路径或文件名）')
    
    args = parser.parse_args()
    
    source_dir = Path(args.source_dir)
    
    # 如果指定了删除
    if args.delete:
        delete_article(args.delete, args.target)
        return
    
    # 如果指定了重建索引
    if args.rebuild:
        if args.rebuild == 'homepage':
            rebuild_homepage()
        else:
            rebuild_index(args.rebuild)
        return
    
    # 如果指定了单个文件
    if args.file:
        source_file = Path(args.file)
        if not source_file.exists():
            print(f"❌ 文件不存在: {source_file}")
            sys.exit(1)
        
        target_dir = args.target or 'blog'
        auto_detect = args.auto
        deploy_article(source_file, target_dir, auto_detect)
    else:
        # 部署整个目录
        if args.auto:
            deploy_all(source_dir, auto_detect=True)
        elif args.target:
            deploy_all(source_dir, target_dir=args.target)
        else:
            print("❌ 请指定 --target (blog/guides) 或使用 --auto 自动判断")
            sys.exit(1)


if __name__ == '__main__':
    main()
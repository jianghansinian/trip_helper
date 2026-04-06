"""
Enhanced MCP-like batch article fetcher + translator -> HTML

Improvements over original:
  - Better article extraction with trafilatura/readability fallback
  - Smarter text-only translation with HTML reconstruction
  - Progress tracking with tqdm
  - Retry mechanism with exponential backoff
  - Caching to avoid re-fetching
  - Config file support (YAML)
  - Better error handling and logging
  - robots.txt respect (basic)

Usage:
  python enhanced_translator.py --config config.yaml
  OR
  python enhanced_translator.py --input urls.txt --lang zh --backend openai

Config file example (config.yaml):
  urls_file: urls.txt
  output_dir: output
  target_lang: zh
  backend: openai  # googletrans|deepl|openai
  deepl_api_key: YOUR_KEY
  openai_api_key: YOUR_KEY
  max_concurrency: 6
  timeout: 30
  use_cache: true
"""

import os
import re
import sys
import asyncio
import aiohttp
import hashlib
import argparse
import time
import json
import logging
import random
import warnings
from urllib.parse import urljoin, urlparse
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import site_css_links  # noqa: E402

# Suppress SSL warnings (common with self-signed certificates in corporate environments)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# Optional imports
try:
    from tqdm.asyncio import tqdm
except:
    tqdm = None

try:
    import yaml
except:
    yaml = None

try:
    from trafilatura import extract, fetch_url
    HAS_TRAFILATURA = True
except:
    HAS_TRAFILATURA = False

try:
    from readability import Document
    HAS_READABILITY = True
except:
    HAS_READABILITY = False

try:
    from googletrans import Translator as GoogleTranslator
except:
    GoogleTranslator = None

# 勿在模块导入时加载 argostranslate：它会拉取 spaCy 并尝试下载 xx_sent_ud_sm，
# 在自签证书/代理环境下可能长时间卡住或反复失败。需要 argos 后端时再懒加载。
HAS_ARGOS = False

try:
    from deep_translator import GoogleTranslator as DeepGoogleTranslator
    from deep_translator import MyMemoryTranslator
    HAS_DEEP_TRANSLATOR = True
except:
    HAS_DEEP_TRANSLATOR = False

# 浏览器自动化（可选）
try:
    import sys
    browser_fetcher_path = Path(__file__).parent / 'browser_fetcher.py'
    if browser_fetcher_path.exists():
        sys.path.insert(0, str(Path(__file__).parent))
        from browser_fetcher import BrowserFetcher, fetch_with_browser
        HAS_BROWSER_FETCHER = True
    else:
        HAS_BROWSER_FETCHER = False
except ImportError:
    HAS_BROWSER_FETCHER = False
    BrowserFetcher = None
    fetch_with_browser = None

# Simple fallback translator using basic HTTP requests
class SimpleTranslator:
    """Simple translator using public APIs without complex dependencies"""
    def __init__(self, source_lang='auto', target_lang='en', service='lingva'):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.service = service
    
    def translate(self, text: str) -> str:
        """Use different public translation services"""
        import requests
        
        # Expanded language map
        lang_map = {
            'zh': 'zh', 'zh-CN': 'zh', 'zh-TW': 'zh',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'es': 'es',
            'fr': 'fr',
            'de': 'de',
            'auto': 'auto'
        }
        source = lang_map.get(self.source_lang, 'auto')
        target = lang_map.get(self.target_lang, 'en')
        
        if self.service == 'lingva':
            # Lingva Translate - free Google Translate proxy
            url = f"https://lingva.ml/api/v1/{source}/{target}/{requests.utils.quote(text)}"
            response = requests.get(url, timeout=30, verify=False)
            if response.status_code == 200:
                return response.json()['translation']
        
        elif self.service == 'mymemory':
            # MyMemory Translation API
            url = "https://api.mymemory.translated.net/get"
            langpair = f'{source}|{target}' if source != 'auto' else f'auto|{target}'
            params = {
                'q': text[:500],  # Limit length
                'langpair': langpair
            }
            response = requests.get(url, params=params, timeout=30, verify=False)
            if response.status_code == 200:
                data = response.json()
                if data.get('responseData'):
                    return data['responseData']['translatedText']
        
        elif self.service == 'simplytranslate':
            # SimplyTranslate - another free option
            url = "https://simplytranslate.org/api/translate"
            params = {
                'from': source,
                'to': target,
                'text': text,
                'engine': 'google'
            }
            response = requests.get(url, params=params, timeout=30, verify=False)
            if response.status_code == 200:
                return response.json()['translated_text']
        
        raise Exception(f"Translation failed with service: {self.service}")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Repo-relative hint (translate.py lives in tools/url-translate/)
_CHROMEDRIVER_INSTALL_HINT = "tools/browser/install_chromedriver.sh"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

# ----------------------------- Custom Exceptions -----------------------------
class VerificationPageError(RuntimeError):
    """验证页面错误，不应该重试"""
    pass


# ----------------------------- Config -----------------------------
@dataclass
class Config:
    urls_file: str = 'urls.txt'
    output_dir: str = 'output'
    source_lang: str = 'auto'  # NEW: Source language (auto for auto-detection)
    target_lang: str = 'zh'
    backend: str = 'simple'  # Changed default to simple (most reliable)
    deepl_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None  # NEW: DeepSeek API key
    proxy: Optional[str] = None  # NEW: Proxy URL (e.g., http://127.0.0.1:7890)
    rewrite_mode: bool = False  # NEW: Enable content rewriting and optimization
    rewrite_extra_instructions: str = ""  # Appended to LLM system prompt when rewrite_mode (e.g. audience/tone)
    use_browser: bool = False  # NEW: 使用浏览器自动化（用于SPA页面）
    browser_type: str = 'playwright'  # NEW: 浏览器类型 'playwright' 或 'selenium'
    browser_headless: bool = True  # NEW: 浏览器无头模式
    browser_wait_time: int = 3  # NEW: 等待JavaScript渲染的时间（秒）
    max_concurrency: int = 6
    timeout: int = 30
    chunk_size: int = 3000  # Chunk size for splitting long text
    use_cache: bool = True
    max_retries: int = 3
    user_agent: str = "ArticleTranslator/2.0 (+https://github.com/yourrepo)"

    @classmethod
    def from_yaml(cls, path: str):
        if not yaml:
            raise RuntimeError("PyYAML not installed")
        config_path = Path(path).resolve()
        base = config_path.parent
        with open(config_path) as f:
            data = yaml.safe_load(f)
        if 'urls_file' in data and data['urls_file']:
            uf = Path(data['urls_file'])
            if not uf.is_absolute():
                data['urls_file'] = str((base / uf).resolve())
        if 'output_dir' in data and data['output_dir']:
            od = Path(data['output_dir'])
            if not od.is_absolute():
                data['output_dir'] = str((base / od).resolve())
        
        # Ensure API keys are strings, not tuples or None
        if 'deepseek_api_key' in data and data['deepseek_api_key']:
            data['deepseek_api_key'] = str(data['deepseek_api_key']).strip()
        if 'openai_api_key' in data and data['openai_api_key']:
            data['openai_api_key'] = str(data['openai_api_key']).strip()
        if 'deepl_api_key' in data and data['deepl_api_key']:
            data['deepl_api_key'] = str(data['deepl_api_key']).strip()
        
        # Set defaults if not present
        if 'rewrite_mode' not in data:
            data['rewrite_mode'] = False
        if 'rewrite_extra_instructions' not in data:
            data['rewrite_extra_instructions'] = ""
        if 'use_browser' not in data:
            data['use_browser'] = False
        if 'browser_type' not in data:
            data['browser_type'] = 'playwright'
        if 'browser_headless' not in data:
            data['browser_headless'] = True
        if 'browser_wait_time' not in data:
            data['browser_wait_time'] = 3

        if not data.get('deepseek_api_key'):
            sk = os.environ.get('DEEPSEEK_API_KEY')
            if sk:
                data['deepseek_api_key'] = str(sk).strip()
        if not data.get('openai_api_key'):
            ok = os.environ.get('OPENAI_API_KEY')
            if ok:
                data['openai_api_key'] = str(ok).strip()
        if not data.get('deepl_api_key'):
            dk = os.environ.get('DEEPL_API_KEY')
            if dk:
                data['deepl_api_key'] = str(dk).strip()
            
        return cls(**data)

    @classmethod
    def from_args(cls, args):
        # Safely get API keys from environment
        deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
        openai_key = os.environ.get('OPENAI_API_KEY')
        deepl_key = os.environ.get('DEEPL_API_KEY')
        
        # Ensure they are strings if they exist
        if deepseek_key:
            deepseek_key = str(deepseek_key).strip()
        if openai_key:
            openai_key = str(openai_key).strip()
        if deepl_key:
            deepl_key = str(deepl_key).strip()
        
        return cls(
            urls_file=args.input,
            output_dir=args.outdir,
            source_lang=args.source,
            target_lang=args.lang,
            backend=args.backend,
            deepl_api_key=deepl_key,
            openai_api_key=openai_key,
            deepseek_api_key=deepseek_key,
            proxy=args.proxy or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'),
            rewrite_mode=args.rewrite,
            use_browser=getattr(args, 'browser', False),
            browser_type=getattr(args, 'browser_type', 'playwright'),
            browser_headless=getattr(args, 'browser_headless', True),
            browser_wait_time=getattr(args, 'browser_wait', 3),
            max_concurrency=args.concurrency,
            timeout=args.timeout,
            use_cache=args.cache
        )


# ----------------------------- Utilities -----------------------------
def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', '', s)
    s = s.strip().replace(' ', '_')
    return s[:120] or hashlib.sha1(s.encode()).hexdigest()[:10]


class RetrySession:
    def __init__(self, session: aiohttp.ClientSession, config: Config):
        self.session = session
        self.config = config
        self.sem = asyncio.Semaphore(config.max_concurrency)

    async def get(self, url: str, **kwargs) -> str:
        headers = kwargs.pop('headers', {})
        headers['User-Agent'] = self.config.user_agent
        
        for attempt in range(self.config.max_retries):
            try:
                async with self.sem:
                    async with self.session.get(
                        url, 
                        headers=headers, 
                        timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                        **kwargs
                    ) as resp:
                        resp.raise_for_status()
                        return await resp.text()
            except Exception as e:
                if attempt == self.config.max_retries - 1:
                    logger.error(f"Failed to fetch {url} after {self.config.max_retries} attempts: {e}")
                    raise
                wait = 2 ** attempt
                logger.warning(f"Retry {attempt + 1}/{self.config.max_retries} for {url} after {wait}s")
                await asyncio.sleep(wait)

class EnhancedRetrySession:
    """Enhanced session with anti-scraping features"""
    def __init__(self, session: aiohttp.ClientSession, config: Config):
        self.session = session
        self.config = config
        self.sem = asyncio.Semaphore(config.max_concurrency)

    def _get_headers(self, url: str) -> Dict[str, str]:
        """Generate headers with anti-scraping features"""
        parsed = urlparse(url)
        domain = parsed.netloc
        
        headers = {
            'User-Agent': self.config.user_agent or random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        }
        
        # Add Referer for better success rate
        if parsed.path and parsed.path != '/':
            headers['Referer'] = f"{parsed.scheme}://{domain}/"
        
        # Site-specific headers
        if 'mafengwo.cn' in domain:
            headers['Referer'] = 'https://www.mafengwo.cn/'
            headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
            headers['Accept-Language'] = 'zh-CN,zh;q=0.9,en;q=0.8'
            headers['Accept-Encoding'] = 'gzip, deflate, br'
            headers['Connection'] = 'keep-alive'
            headers['Upgrade-Insecure-Requests'] = '1'
            headers['Sec-Fetch-Dest'] = 'document'
            headers['Sec-Fetch-Mode'] = 'navigate'
            headers['Sec-Fetch-Site'] = 'same-origin'
            headers['Sec-Fetch-User'] = '?1'
            headers['Cache-Control'] = 'max-age=0'
            # 移除X-Requested-With（这会让服务器认为这是AJAX请求，可能触发验证）
        elif '8264.com' in domain:
            headers['Referer'] = 'https://www.8264.com/'
        
        return headers

    async def get(self, url: str, **kwargs) -> str:
        """Enhanced get with retry and anti-scraping"""
        # 如果配置了使用浏览器自动化，直接使用浏览器
        if getattr(self.config, 'use_browser', False) and HAS_BROWSER_FETCHER:
            try:
                logger.info(f"🌐 使用浏览器自动化抓取: {url}")
                content = await fetch_with_browser(
                    url,
                    browser_type=getattr(self.config, 'browser_type', 'playwright'),
                    headless=getattr(self.config, 'browser_headless', True),
                    timeout=self.config.timeout,
                    proxy=self.config.proxy,
                    wait_time=getattr(self.config, 'browser_wait_time', 3)
                )
                logger.info(f"✓ 浏览器自动化成功获取内容 ({len(content)} 字符)")
                return content
            except RuntimeError as e:
                # 浏览器初始化失败，提供详细错误信息
                error_msg = str(e)
                logger.error(f"❌ 浏览器自动化失败: {error_msg}")
                
                # 如果是SPA页面，提供替代方案
                if 'ctrip.com' in url or 'mafengwo.cn' in url:
                    logger.warning("")
                    logger.warning("💡 替代方案：")
                    logger.warning("  1. 尝试桌面版URL（更容易抓取）")
                    if 'm.ctrip.com' in url:
                        desktop_url = url.replace('m.ctrip.com', 'www.ctrip.com')
                        logger.warning(f"     桌面版: {desktop_url}")
                    logger.warning(f"  2. 手动安装ChromeDriver: bash {_CHROMEDRIVER_INSTALL_HINT}")
                    logger.warning("  3. 或暂时不使用浏览器自动化")
                    logger.warning("")
                
                logger.warning("⚠️  回退到普通HTTP请求（可能无法获取SPA页面内容）...")
                # 继续使用普通HTTP请求（虽然可能失败，但至少尝试）
            except Exception as e:
                logger.error(f"❌ 浏览器自动化失败: {e}")
                logger.warning("⚠️  回退到普通HTTP请求...")
                # 继续使用普通HTTP请求
        
        headers = self._get_headers(url)
        headers.update(kwargs.pop('headers', {}))
        
        cookies = getattr(self.config, 'cookies', None) or {}
        
        last_error = None
        suspicious_count = 0
        proxy_failed = False  # 标记代理是否失败
        
        for attempt in range(self.config.max_retries):
            try:
                async with self.sem:
                    # Add random delay to avoid rate limiting
                    if attempt > 0:
                        await asyncio.sleep(random.uniform(2, 5))
                    
                    # 使用代理（如果配置了且之前没有失败）
                    # 注意：如果代理失败，proxy_failed会被设置，后续重试将不使用代理
                    proxy_url = None if proxy_failed else (self.config.proxy if self.config.proxy else None)
                    
                    async with self.session.get(
                        url,
                        headers=headers,
                        cookies=cookies,
                        timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                        allow_redirects=True,
                        ssl=False,  # Some sites have SSL issues
                        proxy=proxy_url,  # 使用代理
                        **kwargs
                    ) as resp:
                        # Handle different status codes
                        if resp.status == 403:
                            logger.warning(f"403 Forbidden for {url}, rotating User-Agent...")
                            headers['User-Agent'] = random.choice(USER_AGENTS)
                            if attempt == self.config.max_retries - 1:
                                raise RuntimeError(f"403 Forbidden after {self.config.max_retries} attempts")
                            continue
                        elif resp.status == 429:
                            wait = 2 ** (attempt + 2)
                            logger.warning(f"429 Rate limited, waiting {wait}s...")
                            await asyncio.sleep(wait)
                            if attempt == self.config.max_retries - 1:
                                raise RuntimeError(f"Rate limited after {self.config.max_retries} attempts")
                            continue
                        
                        resp.raise_for_status()
                        content = await resp.text()
                        
                        # 改进内容验证：对于马蜂窝等网站，需要检测验证页面
                        # 检查是否是验证页面或空内容
                        is_blocked = False
                        block_keywords = ['验证', 'captcha', 'blocked', 'access denied', 'forbidden']
                        
                        # 对于马蜂窝，检查特定的验证页面特征
                        if 'mafengwo.cn' in url:
                            # 马蜂窝验证页面的特征：
                            # 1. 内容很短（<500字符）
                            # 2. 包含probe.js验证脚本
                            # 3. 包含验证相关关键词
                            content_lower = content.lower()
                            
                            # 检测probe.js验证脚本（马蜂窝的反爬虫机制）
                            # 如果检测到probe.js，立即抛出异常，不继续处理
                            if '/probe.js' in content or 'probe.js' in content_lower:
                                # 根据代理状态提供不同的解决方案
                                if proxy_failed:
                                    error_msg = (
                                        f"马蜂窝网站返回了JavaScript验证页面（probe.js）。\n"
                                        f"这是反爬虫机制，需要JavaScript执行才能访问。\n"
                                        f"当前状态：代理不可用，直接连接也被拦截。\n\n"
                                        f"解决方案：\n"
                                        f"  1. 配置可用的代理（WSL环境需要使用Windows主机IP，不是127.0.0.1）\n"
                                        f"     获取Windows IP: ip route | grep default | awk '{{print $3}}'\n"
                                        f"     然后在config.yaml中使用: proxy: http://<Windows_IP>:7890\n"
                                        f"  2. 使用浏览器自动化工具（Selenium/Playwright）\n"
                                        f"  3. 手动访问页面，保存HTML到文件，然后使用本地文件处理\n"
                                        f"  4. 尝试其他网站（如8264.com）进行测试"
                                    )
                                else:
                                    error_msg = (
                                        f"马蜂窝网站返回了JavaScript验证页面（probe.js）。\n"
                                        f"这是反爬虫机制，需要JavaScript执行才能访问。\n"
                                        f"解决方案：\n"
                                        f"  1. 在config.yaml中设置代理: proxy: http://127.0.0.1:7890\n"
                                        f"  2. 使用浏览器自动化工具（Selenium/Playwright）\n"
                                        f"  3. 手动访问页面并复制内容到文件"
                                    )
                                logger.error(f"❌ {error_msg}")
                                raise VerificationPageError(error_msg)
                            
                            # 检测验证关键词
                            elif any(keyword in content_lower for keyword in ['验证码', '人机验证', '安全验证', 'captcha']):
                                is_blocked = True
                            
                            # 如果内容太短（可能是验证页面）
                            elif len(content) < 500:
                                # 检查是否包含body标签但内容很少（验证页面的特征）
                                if '<body' in content and '</body>' in content:
                                    body_content = content.split('<body')[1].split('</body>')[0] if '</body>' in content else ''
                                    if len(body_content.strip()) < 50:  # body内容很少
                                        is_blocked = True
                                        logger.warning(f"⚠️  Detected suspicious short content with empty body")
                        else:
                            # 其他网站的检测
                            if len(content) < 500:
                                is_blocked = True
                            elif any(keyword in content.lower() for keyword in block_keywords):
                                is_blocked = True
                        
                        if is_blocked:
                            # probe.js已经在上面检测并抛出异常了，这里不会执行到
                            suspicious_count += 1
                            logger.warning(f"Suspicious content detected for {url} (attempt {attempt + 1}, suspicious count: {suspicious_count})")
                            
                            # 如果多次检测到可疑内容，尝试更长的等待时间
                            wait_time = 3 + (suspicious_count * 2)
                            await asyncio.sleep(wait_time)
                            
                            # 如果是最后一次尝试，仍然返回内容（让extractor处理）
                            if attempt == self.config.max_retries - 1:
                                logger.warning(f"⚠️  Reached max retries, returning content anyway (may be blocked page)")
                                if not content or len(content) < 100:
                                    raise RuntimeError(
                                        f"内容太短或为空（{len(content) if content else 0}字符），可能是验证页面。\n"
                                        f"HTML预览: {content[:200] if content else 'None'}"
                                    )
                                return content
                            continue
                        
                        # 内容验证通过
                        if not content:
                            raise RuntimeError("Received empty content")
                        
                        return content
                        
            except asyncio.TimeoutError:
                last_error = f"Timeout after {self.config.timeout}s"
                logger.warning(f"Timeout for {url} (attempt {attempt + 1}/{self.config.max_retries})")
                if attempt == self.config.max_retries - 1:
                    raise RuntimeError(f"Timeout after {self.config.max_retries} attempts: {last_error}")
            except aiohttp.ClientProxyConnectionError as e:
                # 代理连接错误
                last_error = str(e)
                proxy_failed = True
                
                proxy_info = ""
                if '127.0.0.1' in str(self.config.proxy) or 'localhost' in str(self.config.proxy):
                    proxy_info = (
                        f"\n注意：在WSL环境中，127.0.0.1可能无法访问Windows主机的代理。\n"
                        f"可以尝试：\n"
                        f"  1. 使用Windows主机的IP地址（运行：ip route | grep default | awk '{{print $3}}'）\n"
                        f"  2. 或者从Windows主机获取IP：ipconfig（Windows）然后使用该IP\n"
                        f"  3. 或者移除proxy配置，直接连接（可能遇到验证页面）"
                    )
                
                if attempt == 0:
                    # 第一次失败，尝试不使用代理
                    logger.warning(f"⚠️  代理连接失败，尝试不使用代理继续...")
                    logger.warning(f"   错误: {last_error}")
                    if proxy_info:
                        logger.warning(f"   {proxy_info}")
                    # 标记代理失败，下次重试时不使用代理
                    proxy_failed = True
                    continue
                else:
                    # 重试后仍然失败
                    error_msg = (
                        f"无法连接到代理服务器 {self.config.proxy}\n"
                        f"请检查：\n"
                        f"  1. 代理服务器是否正在运行\n"
                        f"  2. 代理地址和端口是否正确\n"
                        f"  3. 防火墙是否阻止了连接{proxy_info}\n"
                        f"  4. 如果不需要代理，可以在config.yaml中移除proxy配置"
                    )
                    logger.error(f"❌ 代理连接失败: {error_msg}")
                    if attempt == self.config.max_retries - 1:
                        raise RuntimeError(f"代理连接失败: {error_msg}\n原始错误: {last_error}")
            except aiohttp.ClientError as e:
                last_error = str(e)
                logger.warning(f"Client error for {url}: {e} (attempt {attempt + 1}/{self.config.max_retries})")
                if attempt == self.config.max_retries - 1:
                    raise RuntimeError(f"Client error after {self.config.max_retries} attempts: {last_error}")
            except VerificationPageError as e:
                # 验证页面错误，不应该重试，直接抛出
                logger.error(f"❌ {e}")
                raise  # 直接抛出，不重试
            except RuntimeError as e:
                last_error = str(e)
                logger.error(f"Runtime error for {url}: {e}")
                if attempt == self.config.max_retries - 1:
                    raise
            except Exception as e:
                last_error = str(e)
                logger.error(f"Unexpected error for {url}: {e}")
                if attempt == self.config.max_retries - 1:
                    raise
            
            wait = 2 ** attempt
            logger.warning(f"Retry {attempt + 1}/{self.config.max_retries} for {url} after {wait}s")
            await asyncio.sleep(wait)
        
        # 如果所有重试都失败，抛出异常（确保不会返回None）
        raise RuntimeError(f"Failed to fetch {url} after {self.config.max_retries} attempts. Last error: {last_error}")


# ----------------------------- Article Extraction -----------------------------
class ArticleExtractor:
    def __init__(self, config: Config):
        self.config = config
    
    def _clean_html_keep_formatting(self, soup: BeautifulSoup, url: str, max_images: int = 2) -> BeautifulSoup:
        """Clean HTML while preserving formatting and keeping up to max_images images"""
        # Remove unwanted tags that don't affect content structure
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        
        # Handle images: keep up to max_images
        images = soup.find_all('img')
        if len(images) > max_images:
            # Keep first max_images images, remove the rest
            for img in images[max_images:]:
                img.decompose()
            logger.debug(f"保留 {max_images} 张图片，移除了 {len(images) - max_images} 张")
        elif len(images) > 0:
            logger.debug(f"保留 {len(images)} 张图片")
        
        # Ensure image URLs are absolute
        base_url = urlparse(url)
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-original')
            if src:
                # Convert relative URLs to absolute
                if src.startswith('//'):
                    img['src'] = base_url.scheme + ':' + src
                elif src.startswith('/'):
                    img['src'] = f"{base_url.scheme}://{base_url.netloc}{src}"
                elif not src.startswith('http'):
                    img['src'] = urljoin(url, src)
                # Add loading attribute for better performance
                img['loading'] = 'lazy'
                img['style'] = 'max-width: 100%; height: auto; border-radius: 8px; margin: 1.5rem 0;'
        
        return soup

    def extract(self, html: str, url: str) -> Dict:
        """Try multiple extraction strategies in order of quality"""
        
        # Strategy 1: trafilatura (best)
        if HAS_TRAFILATURA:
            try:
                result = self._extract_trafilatura(html, url)
                if result and len(result.get('text', '')) > 200:
                    logger.debug(f"Extracted with trafilatura: {url}")
                    return result
            except Exception as e:
                logger.debug(f"Trafilatura failed for {url}: {e}")

        # Strategy 2: readability
        if HAS_READABILITY:
            try:
                result = self._extract_readability(html, url)
                if result and len(result.get('text', '')) > 200:
                    logger.debug(f"Extracted with readability: {url}")
                    return result
            except Exception as e:
                logger.debug(f"Readability failed for {url}: {e}")

        # Strategy 3: fallback to BeautifulSoup
        logger.debug(f"Using BeautifulSoup fallback for {url}")
        return self._extract_bs4(html, url)

    def _extract_trafilatura(self, html: str, url: str) -> Dict:
        text = extract(html, include_comments=False, include_tables=True, include_images=False)
        if not text:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup, url)
        
        # Try to extract HTML content as well (for better formatting preservation)
        # Use BeautifulSoup to find content, similar to _extract_bs4
        # But we'll keep it simple - just try to find the main content area
        content = None
        for selector in ['article', 'main', '.article-content', '.post-content', '.entry-content', '.content', '.view-content']:
            found = soup.select_one(selector)
            if found:
                text_len = len(found.get_text(strip=True))
                if text_len > 200:
                    content = found
                    break
        
        # If no specific selector found, try to find the largest content area
        if not content:
            candidates = soup.find_all(['div', 'section'], recursive=True)
            if candidates:
                valid_candidates = [c for c in candidates if len(c.get_text(strip=True)) > 200]
                if valid_candidates:
                    content = max(valid_candidates, key=lambda x: len(x.get_text(strip=True)))
        
        html_content = None
        if content:
            # Clean and format the HTML content
            content_soup = BeautifulSoup(str(content), 'html.parser')
            cleaned_content = self._clean_html_keep_formatting(content_soup, url, max_images=2)
            html_content = str(cleaned_content)
        
        return {
            'title': title,
            'text': text,
            'lead_image': None,  # Disabled
            'html': html_content  # Try to extract HTML if possible
        }

    def _extract_readability(self, html: str, url: str) -> Dict:
        doc = Document(html)
        title = doc.title()
        content_html = doc.summary()
        
        soup = BeautifulSoup(content_html, 'html.parser')
        # 使用_clean_html_keep_formatting保留格式和最多2张图片
        cleaned_soup = self._clean_html_keep_formatting(soup, url, max_images=2)
        
        text = cleaned_soup.get_text(separator='\n', strip=True)
        cleaned_html = str(cleaned_soup)
        # lead_image removed
        
        return {
            'title': title,
            'text': text,
            'html': cleaned_html,
            'lead_image': None  # Disabled
        }

    def _extract_bs4(self, html: str, url: str) -> Dict:
        # 验证HTML内容
        if not html:
            raise ValueError(f"HTML content is None or empty for {url}")
        
        if not isinstance(html, str):
            raise TypeError(f"HTML content must be a string, got {type(html)} for {url}")
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            raise ValueError(f"Failed to parse HTML for {url}: {e}")
        
        title = self._get_title(soup, url)
        
        # 保存原始body用于调试
        original_body = soup.body
        
        # 检测是否是SPA（单页应用）页面
        # SPA页面的特征：包含React/Next.js/Vue等框架标记，但实际内容很少
        is_spa = False
        spa_indicators = [
            '__next', '__NEXT_DATA__', 'react-root', 'vue-app', 
            'ng-app', '精彩即将呈现', 'Loading...', 'loading'
        ]
        body_text_preview = soup.body.get_text(strip=True)[:200] if soup.body else ''
        
        if any(indicator in html.lower() or indicator in body_text_preview for indicator in spa_indicators):
            # 检查是否内容很少但HTML很大（SPA的特征）
            if len(html) > 10000 and len(body_text_preview) < 200:
                is_spa = True
                logger.warning(f"⚠️  检测到SPA（单页应用）页面，内容可能通过JavaScript动态加载")
        
        # Try site-specific selectors
        domain = urlparse(url).netloc
        content = None
        selector_used = None
        
        # 携程网站特殊处理
        if 'ctrip.com' in domain:
            # 携程移动端是React/Next.js应用，尝试提取可能的JSON数据
            logger.debug("尝试从携程页面提取内容...")
            
            # 尝试查找__NEXT_DATA__（Next.js的数据）
            next_data_script = soup.find('script', id='__NEXT_DATA__')
            if next_data_script and next_data_script.string:
                try:
                    next_data = json.loads(next_data_script.string)
                    # 尝试从数据中提取内容
                    logger.debug("找到__NEXT_DATA__，尝试提取内容...")
                    # 递归查找可能的文章内容
                    def find_text_in_dict(d, depth=0):
                        if depth > 5:  # 限制深度
                            return None
                        if isinstance(d, dict):
                            for k, v in d.items():
                                if any(keyword in k.lower() for keyword in ['content', 'text', 'article', 'body', 'description']):
                                    if isinstance(v, str) and len(v) > 200:
                                        return v
                                result = find_text_in_dict(v, depth+1)
                                if result:
                                    return result
                        elif isinstance(d, list):
                            for item in d:
                                result = find_text_in_dict(item, depth+1)
                                if result:
                                    return result
                        return None
                    
                    extracted_text = find_text_in_dict(next_data)
                    if extracted_text and len(extracted_text) > 200:
                        logger.info("✓ 从__NEXT_DATA__中提取到内容")
                        return {
                            'title': title,
                            'text': extracted_text,
                            'html': None,
                            'lead_image': None
                        }
                except Exception as e:
                    logger.debug(f"解析__NEXT_DATA__失败: {e}")
            
            # 尝试查找包含内容的script标签
            scripts = soup.find_all('script', type='application/json')
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    # 尝试提取文章内容
                    if isinstance(data, dict):
                        # 递归查找可能的文本内容
                        def find_text_in_dict(d, depth=0):
                            if depth > 5:  # 限制深度
                                return None
                            if isinstance(d, dict):
                                for k, v in d.items():
                                    if 'content' in k.lower() or 'text' in k.lower() or 'article' in k.lower():
                                        if isinstance(v, str) and len(v) > 100:
                                            return v
                                    result = find_text_in_dict(v, depth+1)
                                    if result:
                                        return result
                            elif isinstance(d, list):
                                for item in d:
                                    result = find_text_in_dict(item, depth+1)
                                    if result:
                                        return result
                            return None
                        
                        extracted_text = find_text_in_dict(data)
                        if extracted_text and len(extracted_text) > 200:
                            logger.info("✓ 从JSON数据中提取到内容")
                            return {
                                'title': title,
                                'text': extracted_text,
                                'html': None,
                                'lead_image': None
                            }
                except:
                    continue
            
            # 如果无法从JSON提取，尝试查找可能的容器
            selectors = [
                '#app', '#root', '[class*="content"]', '[class*="article"]',
                '[class*="detail"]', '[class*="main"]', 'main', 'article'
            ]
            for selector in selectors:
                found = soup.select_one(selector)
                if found:
                    text = found.get_text(strip=True)
                    if len(text) > 200:
                        content = found
                        selector_used = f"ctrip-selector: {selector}"
                        break
        
        if 'mafengwo.cn' in domain:
            # 马蜂窝的多种可能选择器（按优先级）
            selectors = [
                ('._j_content_box', '马蜂窝内容框'),
                ('.view_con', '马蜂窝视图容器'),
                ('.post-view', '马蜂窝文章视图'),
                ('#_j_article_content', '马蜂窝文章内容ID'),
                ('.post-content', '马蜂窝文章内容'),
                ('.poi-detail', '马蜂窝POI详情'),
                ('.article', '马蜂窝文章'),
                ('.content', '通用内容'),
            ]
            
            for selector, desc in selectors:
                try:
                    found = soup.select_one(selector)
                    if found:
                        text_len = len(found.get_text(strip=True))
                        if text_len > 100:  # 确保有足够内容
                            content = found
                            selector_used = f"{selector} ({desc})"
                            logger.debug(f"✓ Found content using {selector_used}, length: {text_len}")
                            break
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            # 如果还是没找到，尝试查找包含"游记"、"攻略"等关键词的div
            if not content:
                logger.debug("Trying keyword-based search for 马蜂窝...")
                for div in soup.find_all('div', class_=True):
                    class_str = ' '.join(div.get('class', []))
                    text_preview = div.get_text(strip=True)[:100]
                    if len(div.get_text(strip=True)) > 500:  # 足够长的内容
                        # 检查是否包含文章相关关键词
                        if any(keyword in text_preview for keyword in ['游记', '攻略', '旅行', '景点', '酒店']):
                            content = div
                            selector_used = f"keyword-based: {class_str}"
                            logger.debug(f"✓ Found content using {selector_used}")
                            break
                            
        elif '8264.com' in domain:
            content = soup.select_one('.detail-con') or soup.select_one('.article-content')
            selector_used = '.detail-con or .article-content'
        elif 'ctnews.com.cn' in domain:
            content = soup.select_one('.content') or soup.select_one('.article')
            selector_used = '.content or .article'
        
        # Fallback to common selectors
        if not content:
            for selector in ['article', 'main', '.article-content', '.post-content', '.entry-content', '.content']:
                found = soup.select_one(selector)
                if found:
                    text_len = len(found.get_text(strip=True))
                    if text_len > 100:
                        content = found
                        selector_used = selector
                        logger.debug(f"✓ Found content using fallback selector: {selector}")
                        break
        
        # Last resort: find the div with most text
        if not content:
            logger.debug("Using last resort: finding div with most text...")
            candidates = soup.find_all(['div', 'section'], recursive=True)
            if candidates:
                # 过滤掉太小的候选
                valid_candidates = [c for c in candidates if len(c.get_text(strip=True)) > 200]
                if valid_candidates:
                    content = max(valid_candidates, key=lambda x: len(x.get_text(strip=True)))
                    selector_used = "max-text-div"
                    logger.debug(f"✓ Found content using max-text strategy, length: {len(content.get_text(strip=True))}")
                else:
                    content = max(candidates, key=lambda x: len(x.get_text(strip=True)))
                    selector_used = "max-text-div (all)"
            else:
                content = soup.body or soup
                selector_used = "body or root"
        
        if not content:
            raise ValueError(f"Could not find content in HTML for {url}")
        
        # 创建content的副本用于清理（保留原始格式和最多2张图片）
        content_soup = BeautifulSoup(str(content), 'html.parser')
        # 清理HTML但保留格式和最多2张图片
        cleaned_content = self._clean_html_keep_formatting(content_soup, url, max_images=2)
        
        # 提取清理后的文本和HTML
        text = cleaned_content.get_text(separator='\n', strip=True)
        html_fragment = str(cleaned_content)
        
        # 记录提取信息
        if selector_used:
            logger.debug(f"Content extracted using: {selector_used}, text length: {len(text)}")
            # 统计保留的图片数量
            images_count = len(cleaned_content.find_all('img'))
            if images_count > 0:
                logger.debug(f"保留 {images_count} 张图片")
        
        # 如果提取的文本太短，记录更多调试信息
        if len(text) < 100:
            logger.warning(f"⚠ Extracted text is very short ({len(text)} chars) for {url}")
            logger.warning(f"   Selector used: {selector_used}")
            logger.warning(f"   Title: {title}")
            logger.warning(f"   Text preview: {text[:200]}")
            
            # 检查是否是SPA页面
            if is_spa or 'ctrip.com' in domain:
                logger.error(f"❌ 这是SPA（单页应用）页面，内容通过JavaScript动态加载")
                logger.error(f"   直接抓取HTML无法获取实际内容，需要：")
                logger.error(f"   1. 使用浏览器自动化工具（Selenium/Playwright）")
                logger.error(f"   2. 使用API接口（如果有）")
                logger.error(f"   3. 尝试桌面版URL（将m.ctrip.com改为www.ctrip.com）")
                raise ValueError(
                    f"无法从SPA页面提取内容。\n"
                    f"携程移动端页面（m.ctrip.com）是单页应用，内容通过JavaScript动态加载。\n"
                    f"解决方案：\n"
                    f"  1. 尝试桌面版URL：将 m.ctrip.com 改为 www.ctrip.com\n"
                    f"  2. 使用浏览器自动化工具（Selenium/Playwright）\n"
                    f"  3. 使用API接口（如果有）"
                )
            
            # 检查是否是验证页面
            if original_body:
                body_text = original_body.get_text(strip=True)
                if '验证' in body_text or 'captcha' in body_text.lower():
                    logger.error(f"❌ Likely verification page detected in body text")
        
        return {
            'title': title,
            'text': text,
            'html': html_fragment,
            'lead_image': None
        }
    def _get_title(self, soup: BeautifulSoup, url: str) -> str:
        # Try og:title, twitter:title, then <title>
        for meta in soup.find_all('meta'):
            prop = meta.get('property', '').lower()
            name = meta.get('name', '').lower()
            if prop in ['og:title', 'twitter:title'] or name in ['og:title', 'twitter:title']:
                content = meta.get('content', '').strip()
                if content:
                    return content
        
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        
        return urlparse(url).netloc

    def _get_lead_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        # Try og:image first
        for meta in soup.find_all('meta'):
            prop = meta.get('property', '').lower()
            if prop == 'og:image':
                img_url = meta.get('content', '').strip()
                if img_url:
                    return urljoin(base_url, img_url)
        
        # Then first img tag
        img = soup.find('img')
        if img and img.get('src'):
            return urljoin(base_url, img['src'])
        
        return None


# ----------------------------- Translation -----------------------------
class TranslatorBackend:
    def __init__(self, config: Config):
        self.config = config

    async def translate(self, text: str) -> str:
        raise NotImplementedError

    async def translate_html(self, html_content: str) -> str:
        """
        Translate HTML content while preserving structure, tags, and attributes (including styles).
        Only translates text nodes containing Chinese characters, other characters are preserved as-is.
        Uses batch translation with numbered placeholders to minimize API calls and ensure reliable splitting.
        Improved: Groups text nodes by their parent element to avoid splitting related content.
        """
        if not html_content or not html_content.strip():
            return html_content
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            from bs4 import NavigableString, Tag
            
            def contains_chinese(text: str) -> bool:
                """Check if text contains Chinese characters (including single characters like 一、二、三)"""
                # Don't strip here - we want to check the original text
                for char in text:
                    # Check for Chinese characters (CJK Unified Ideographs)
                    if '\u4e00' <= char <= '\u9fff':
                        return True
                return False
            
            # Strategy: Group text nodes by their parent element to avoid splitting related content
            # This handles cases where HTML structure splits content (e.g., <li>序号<p>内容</p></li>)
            parent_text_groups = {}  # Maps parent element to list of (text_node, text) tuples
            
            for element in soup.descendants:
                if isinstance(element, NavigableString):
                    # Check if parent is script/style/noscript/img (skip these)
                    parent = element.parent
                    if parent and parent.name not in ['script', 'style', 'noscript', 'img']:
                        original_text = str(element)  # Don't strip - preserve original
                        # Only translate if text contains Chinese characters (even single chars)
                        if original_text and contains_chinese(original_text):
                            # Group by parent element
                            parent_id = id(parent)
                            if parent_id not in parent_text_groups:
                                parent_text_groups[parent_id] = []
                            parent_text_groups[parent_id].append((element, original_text))
            
            if not parent_text_groups:
                # No Chinese text to translate
                logger.info("📄 No Chinese text found in HTML, skipping translation")
                return html_content
            
            # Collect text nodes to translate, grouped by parent
            text_nodes_to_translate = []
            for parent_id, text_nodes in parent_text_groups.items():
                # If parent has multiple text nodes, we'll merge them
                # Otherwise, translate individually
                if len(text_nodes) > 1:
                    # Merge all text nodes from the same parent
                    merged_text = ''.join(text for _, text in text_nodes)
                    # Use the first node as the representative (we'll replace it with merged translation)
                    text_nodes_to_translate.append((text_nodes[0][0], merged_text, text_nodes))
                else:
                    # Single text node, translate individually
                    text_node, text = text_nodes[0]
                    text_nodes_to_translate.append((text_node, text, [text_nodes[0]]))
            
            logger.info(f"📄 Found {len(text_nodes_to_translate)} text node groups with Chinese characters")
            
            # Strategy: Use numbered placeholder-based batch translation
            # 1. Replace Chinese text nodes with numbered placeholders (more reliable)
            # 2. Extract all Chinese text
            # 3. Translate all text in one batch with numbered markers
            # 4. Replace placeholders with translated text
            
            placeholder_map = {}  # Maps placeholder to (original_text, text_nodes_list)
            placeholder_counter = 0
            
            # Step 1: Replace Chinese text nodes with numbered placeholders
            for text_node, original_text, text_nodes_list in text_nodes_to_translate:
                # Use a more unique placeholder format with counter
                placeholder = f"___TPL{placeholder_counter:05d}___"
                placeholder_counter += 1
                placeholder_map[placeholder] = (original_text, text_nodes_list)
                text_node.replace_with(placeholder)
                
                # Remove other text nodes from the same parent (if merged)
                if len(text_nodes_list) > 1:
                    for other_node, _ in text_nodes_list[1:]:
                        if other_node.parent:  # Check if node still exists
                            other_node.extract()
            
            # Step 2: Extract all Chinese text for batch translation
            texts_to_translate = [text_info[0] for text_info in placeholder_map.values()]
            
            # Step 3: Translate all text in one batch
            # Use numbered markers in the combined text for reliable splitting
            # Format: [SEGMENT_0]...text...[SEGMENT_1]...text...
            combined_parts = []
            for i, text in enumerate(texts_to_translate):
                combined_parts.append(f"[SEGMENT_{i}]")
                combined_parts.append(text)
                combined_parts.append(f"[/SEGMENT_{i}]")
            
            combined_text = "".join(combined_parts)
            
            logger.info(f"📄 Translating {len(texts_to_translate)} text segments in one batch ({len(combined_text)} chars total)")
            
            try:
                # Translate the entire combined text directly without chunking
                # Use a custom translation method that bypasses chunking for batch HTML translation
                if hasattr(self, '_translate_batch'):
                    translated_combined = await self._translate_batch(combined_text)
                else:
                    # Fallback to regular translate if _translate_batch is not available
                    logger.info("📄 Using regular translate method (batch method not available)")
                    translated_combined = await self.translate(combined_text)
                
                # Extract translated segments using regex to find [SEGMENT_N]...[/SEGMENT_N] markers
                import re
                segment_pattern = re.compile(r'\[SEGMENT_(\d+)\](.*?)\[/SEGMENT_\1\]', re.DOTALL)
                matches = segment_pattern.findall(translated_combined)
                
                # Create a dictionary of segment index to translated text
                translated_dict = {}
                for segment_idx_str, translated_text in matches:
                    segment_idx = int(segment_idx_str)
                    # Don't strip - preserve whitespace to avoid losing brackets
                    translated_dict[segment_idx] = translated_text
                
                # Build translated_texts list in order
                translated_texts = []
                for i in range(len(texts_to_translate)):
                    if i in translated_dict:
                        translated_texts.append(translated_dict[i])
                    else:
                        # Segment not found in translation, keep original
                        logger.warning(f"Segment {i} not found in translated output, keeping original")
                        translated_texts.append(texts_to_translate[i])
                
                if len(translated_texts) != len(texts_to_translate):
                    raise ValueError(f"Translation segment count mismatch: {len(translated_texts)} != {len(texts_to_translate)}")
                
            except Exception as e:
                logger.warning(f"Batch translation with markers failed: {e}, falling back to separator method")
                # Fallback: use a more unique separator
                separator = "|||TRANSLATION_SEPARATOR_XYZ|||"
                combined_text = separator.join(texts_to_translate)
                
                try:
                    # Try to use _translate_batch if available (for DeepSeek), otherwise use translate
                    if hasattr(self, '_translate_batch'):
                        # For batch method, we need to add markers back for consistent format
                        marked_text = "".join([f"[SEGMENT_{i}]{text}[/SEGMENT_{i}]" for i, text in enumerate(texts_to_translate)])
                        translated_combined = await self._translate_batch(marked_text)
                        # Extract using markers
                        import re
                        segment_pattern = re.compile(r'\[SEGMENT_(\d+)\](.*?)\[/SEGMENT_\1\]', re.DOTALL)
                        matches = segment_pattern.findall(translated_combined)
                        translated_dict = {int(idx): text for idx, text in matches}
                        translated_texts = [translated_dict.get(i, texts_to_translate[i]) for i in range(len(texts_to_translate))]
                    else:
                        translated_combined = await self.translate(combined_text)
                        translated_texts = translated_combined.split(separator)
                    
                    if len(translated_texts) != len(texts_to_translate):
                        raise ValueError(f"Separator split failed: {len(translated_texts)} != {len(texts_to_translate)}")
                except Exception as e2:
                    logger.error(f"All batch translation methods failed: {e2}, keeping original HTML")
                    return html_content
            
            # Step 4: Replace placeholders with translated text
            # NOTE: We do NOT clean Markdown symbols to ensure content integrity
            # Markdown symbols (**, __, etc.) in the output are preserved to avoid accidental content loss
            
            result_html = str(soup)
            for i, (placeholder, (original_text, text_nodes_list)) in enumerate(placeholder_map.items()):
                if i < len(translated_texts):
                    translated_text = translated_texts[i]
                    # Do NOT clean Markdown - preserve all content to avoid accidental deletion
                    # Preserve whitespace structure if original had leading/trailing whitespace
                    # Check if original had leading/trailing whitespace
                    if original_text != original_text.strip():
                        # Preserve original whitespace structure
                        leading_ws = len(original_text) - len(original_text.lstrip())
                        trailing_ws = len(original_text) - len(original_text.rstrip())
                        if leading_ws > 0:
                            translated_text = ' ' * leading_ws + translated_text
                        if trailing_ws > 0:
                            translated_text = translated_text + ' ' * trailing_ws
                else:
                    translated_text = original_text  # Fallback to original
                
                result_html = result_html.replace(placeholder, translated_text)
            
            logger.info(f"✓ HTML translation completed ({len(texts_to_translate)} segments translated in 1 batch)")
            return result_html
            
        except Exception as e:
            logger.error(f"Error translating HTML: {e}, returning original HTML")
            import traceback
            logger.error(traceback.format_exc())
            return html_content

    def _chunk_text(self, text: str) -> List[str]:
        """Smart chunking by paragraphs"""
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = []
        current_size = 0
        
        for para in paragraphs:
            para_size = len(para)
            if current_size + para_size > self.config.chunk_size and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [para]
                current_size = para_size
            else:
                current_chunk.append(para)
                current_size += para_size
        
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))
        
        return chunks




class SimpleBackend(TranslatorBackend):
    """Simple translator using free public services - no API key needed"""
    def __init__(self, config: Config):
        super().__init__(config)
        # Try multiple services as fallback
        self.services = ['lingva', 'mymemory', 'simplytranslate']
        self.current_service = 0
        logger.info(f"✓ Simple Translator ready: {config.source_lang} → {config.target_lang}")

    async def translate(self, text: str) -> str:
        import warnings
        import urllib3
        # Disable SSL warnings
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        warnings.filterwarnings('ignore', message='Unverified HTTPS request')
        
        loop = asyncio.get_running_loop()
        chunks = self._chunk_text(text)
        
        async def _translate_chunk(chunk: str) -> str:
            # Add delay to respect rate limits
            await asyncio.sleep(1)
            
            # Try different services until one works
            for service in self.services:
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda s=service: SimpleTranslator(
                            self.config.source_lang, 
                            self.config.target_lang, 
                            s
                        ).translate(chunk)
                    )
                    logger.debug(f"✓ Translated with {service}")
                    return result
                except Exception as e:
                    logger.debug(f"Service {service} failed: {e}")
                    continue
            
            # If all services fail, return original
            logger.warning(f"All translation services failed, using original text")
            return chunk
        
        results = await asyncio.gather(*[_translate_chunk(c) for c in chunks], return_exceptions=True)
        
        translated_parts = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Chunk {i} failed: {result}")
                translated_parts.append(chunks[i])
            else:
                translated_parts.append(result)
        
        return '\n\n'.join(translated_parts)


class DeepSeekBackend(TranslatorBackend):
    """DeepSeek API - OpenAI compatible interface"""
    def __init__(self, config: Config):
        super().__init__(config)
        if not config.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        self.api_key = config.deepseek_api_key
        self.url = 'https://api.deepseek.com/v1/chat/completions'  # DeepSeek API endpoint
        
        # Auto-detect proxy from environment or config
        self.proxy = config.proxy or os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        
        # Language name mapping for better prompts
        lang_names = {
            'zh': 'Chinese', 'zh-CN': 'Simplified Chinese',
            'en': 'English', 'ja': 'Japanese', 'ko': 'Korean',
            'es': 'Spanish', 'fr': 'French', 'de': 'German'
        }
        self.source_name = lang_names.get(config.source_lang, config.source_lang)
        self.target_name = lang_names.get(config.target_lang, config.target_lang)
        
        if self.proxy:
            logger.info(f"✓ DeepSeek Translator ready (via proxy {self.proxy}): {self.source_name} → {self.target_name}")
        else:
            logger.info(f"✓ DeepSeek Translator ready: {self.source_name} → {self.target_name}")

    def _merge_rewrite_extras(self, system_text: str) -> str:
        """Append rewrite_extra_instructions when rewrite_mode (used by article_to_target_page, etc.)."""
        if not self.config.rewrite_mode:
            return system_text
        extra = (getattr(self.config, "rewrite_extra_instructions", None) or "").strip()
        if not extra:
            return system_text
        return f"{system_text}\n\nAdditional style requirements:\n{extra}"

    async def _translate_batch(self, text: str) -> str:
        """
        Translate text in a single batch without chunking.
        Used for HTML batch translation to minimize API calls.
        """
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        # Create connector with proxy support
        connector = None
        if self.proxy:
            connector = aiohttp.TCPConnector()
        
        rewrite_mode = self.config.rewrite_mode
        if rewrite_mode:
            if self.config.source_lang == 'auto':
                system_content = f"""You are a professional content writer and editor. Your task is to:
1. Translate the content into {self.target_name}
2. Rewrite and refine the content to make it more engaging and well-structured
3. Organize content into clear paragraphs with logical flow
4. Improve clarity, coherence, and readability
5. Keep the core message and key information intact
6. Use a professional yet accessible tone
7. CRITICAL: Preserve all [SEGMENT_N] and [/SEGMENT_N] markers exactly as they appear

Output ONLY the rewritten content in {self.target_name} with markers preserved, using clear paragraph breaks (double newlines between paragraphs)."""
                user_prompt = "Please rewrite and optimize the following content:\n\n{text}"
            else:
                system_content = f"""You are a professional content writer and editor. Your task is to:
1. Translate the {self.source_name} content into {self.target_name}
2. Rewrite and refine the content to make it more engaging and well-structured
3. Organize content into clear paragraphs with logical flow
4. Improve clarity, coherence, and readability
5. Keep the core message and key information intact
6. Use a professional yet accessible tone
7. CRITICAL: Preserve all [SEGMENT_N] and [/SEGMENT_N] markers exactly as they appear

Output ONLY the rewritten content in {self.target_name} with markers preserved, using clear paragraph breaks (double newlines between paragraphs)."""
                user_prompt = "Please rewrite and optimize the following content:\n\n{text}"
        else:
            if self.config.source_lang == 'auto':
                system_content = f"""You are a professional translator. Your task is to:
1. Translate the text into {self.target_name} accurately while preserving the original meaning and tone
2. CRITICAL: Preserve all [SEGMENT_N] and [/SEGMENT_N] markers exactly as they appear
3. Do NOT use Markdown formatting symbols (**, __, *, _)
4. NUMBERING RULES - VERY IMPORTANT:
   - If the original has Chinese numbers in parentheses like (一), (二), (三), translate them to (One), (Two), (Three)
   - If the original has Chinese numbers like 一、二、三 (without parentheses), translate to One, Two, Three
   - NEVER translate Chinese numbers (一、二、三) to Arabic digits (1, 2, 3)
   - NEVER add lettered formats like (1 a), (2 b), (1 1.)
   - NEVER duplicate numbering - if original is (一), output should be (One), NOT (1 1.) or (1 a)
5. Do NOT add extra formatting, numbering, or duplicate existing numbering

Output ONLY the plain translated text with markers preserved, nothing else."""
                user_prompt = "Translate the following text:\n\n{text}"
            else:
                system_content = f"""You are a professional translator. Your task is to:
1. Translate the {self.source_name} text into {self.target_name} accurately while preserving the original meaning and tone
2. CRITICAL: Preserve all [SEGMENT_N] and [/SEGMENT_N] markers exactly as they appear
3. Do NOT use Markdown formatting symbols (**, __, *, _)
4. NUMBERING RULES - VERY IMPORTANT:
   - If the original has Chinese numbers in parentheses like (一), (二), (三), translate them to (One), (Two), (Three)
   - If the original has Chinese numbers like 一、二、三 (without parentheses), translate to One, Two, Three
   - NEVER translate Chinese numbers (一、二、三) to Arabic digits (1, 2, 3)
   - NEVER add lettered formats like (1 a), (2 b), (1 1.)
   - NEVER duplicate numbering - if original is (一), output should be (One), NOT (1 1.) or (1 a)
5. Do NOT add extra formatting, numbering, or duplicate existing numbering

Output ONLY the plain translated text with markers preserved, nothing else."""
                user_prompt = "Translate the following text:\n\n{text}"
        
        system_content = self._merge_rewrite_extras(system_content)

        # Calculate timeout based on text size
        base_timeout = 300 if rewrite_mode else 180
        size_bonus = max(len(text) // 100, 0)
        timeout = min(base_timeout + size_bonus, 600)  # Cap at 10 minutes for large batches
        
        # Adjust max_tokens
        estimated_tokens = len(text) // 3
        max_tokens = min(int(estimated_tokens * 2), 16000)  # Allow more tokens for batch translation
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt.format(text=text)}
            ],
            "temperature": 0.7 if rewrite_mode else 0.3,
            "max_tokens": max_tokens
        }
        
        proxy_url = self.proxy if self.proxy else None
        
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                self.url,
                headers=headers,
                json=payload,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as r:
                r.raise_for_status()
                js = await r.json()
                
                if 'choices' not in js or not js['choices']:
                    raise RuntimeError(f"Unexpected API response: {js}")
                
                translated = js['choices'][0]['message']['content'].strip()
                if not translated:
                    raise RuntimeError("Empty response from API")
                
                return translated

    async def translate(self, text: str) -> str:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        
        chunks = self._chunk_text(text)
        out = []
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        # Create connector with proxy support
        connector = None
        if self.proxy:
            connector = aiohttp.TCPConnector()
        
        async with aiohttp.ClientSession(connector=connector) as session:
            for idx, chunk in enumerate(chunks, 1):
                # Build instruction based on source language and rewrite mode
                rewrite_mode = self.config.rewrite_mode
                if rewrite_mode:
                    if self.config.source_lang == 'auto':
                        system_prompt = f"""You are a professional content writer and editor. Your task is to:
1. Translate the content into {self.target_name}
2. Rewrite and refine the content to make it more engaging and well-structured
3. Organize content into clear paragraphs with logical flow
4. Improve clarity, coherence, and readability
5. Keep the core message and key information intact
6. Use a professional yet accessible tone

Output ONLY the rewritten content in {self.target_name}, with clear paragraph breaks (use double newlines between paragraphs)."""
                    else:
                        system_prompt = f"""You are a professional content writer and editor. Your task is to:
1. Translate the {self.source_name} content into {self.target_name}
2. Rewrite and refine the content to make it more engaging and well-structured
3. Organize content into clear paragraphs with logical flow
4. Improve clarity, coherence, and readability
5. Keep the core message and key information intact
6. Use a professional yet accessible tone

Output ONLY the rewritten content in {self.target_name}, with clear paragraph breaks (use double newlines between paragraphs)."""
                    user_prompt = f"Please rewrite and optimize the following content:\n\n{chunk}"
                else:
                    if self.config.source_lang == 'auto':
                        system_prompt = f"""You are a professional translator. Your task is to:
1. Translate the text into {self.target_name} accurately while preserving the original meaning and tone
2. Do NOT use Markdown formatting symbols (**, __, *, _)
3. NUMBERING RULES - VERY IMPORTANT:
   - If the original has Chinese numbers in parentheses like (一), (二), (三), translate them to (One), (Two), (Three)
   - If the original has Chinese numbers like 一、二、三 (without parentheses), translate to One, Two, Three
   - NEVER translate Chinese numbers (一、二、三) to Arabic digits (1, 2, 3)
   - NEVER add lettered formats like (1 a), (2 b), (1 1.)
   - NEVER duplicate numbering - if original is (一), output should be (One), NOT (1 1.) or (1 a)
4. Do NOT add extra formatting, numbering, or duplicate existing numbering

Output ONLY the plain translated text, nothing else."""
                    else:
                        system_prompt = f"""You are a professional translator. Your task is to:
1. Translate the {self.source_name} text into {self.target_name} accurately while preserving the original meaning and tone
2. Do NOT use Markdown formatting symbols (**, __, *, _)
3. NUMBERING RULES - VERY IMPORTANT:
   - If the original has Chinese numbers in parentheses like (一), (二), (三), translate them to (One), (Two), (Three)
   - If the original has Chinese numbers like 一、二、三 (without parentheses), translate to One, Two, Three
   - NEVER translate Chinese numbers (一、二、三) to Arabic digits (1, 2, 3)
   - NEVER add lettered formats like (1 a), (2 b), (1 1.)
   - NEVER duplicate numbering - if original is (一), output should be (One), NOT (1 1.) or (1 a)
4. Do NOT add extra formatting, numbering, or duplicate existing numbering

Output ONLY the plain translated text, nothing else."""
                    user_prompt = f"Translate to {self.target_name}:\n\n{chunk}"
                
                system_prompt = self._merge_rewrite_extras(system_prompt)

                # Calculate dynamic timeout based on chunk size and mode
                # Base timeout: 60s for translation, 120s for rewrite
                # Add extra time based on chunk size (roughly 1s per 100 chars)
                base_timeout = 180 if rewrite_mode else 90
                size_bonus = max(len(chunk) // 100, 0)
                chunk_timeout = min(base_timeout + size_bonus, 300)  # Cap at 5 minutes
                
                # Adjust max_tokens based on chunk size
                estimated_tokens = len(chunk) // 3  # Rough estimate: 3 chars per token
                max_tokens = min(int(estimated_tokens * 1.5), 8000)  # Allow 50% more for output, cap at 8k
                
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7 if rewrite_mode else 0.3,
                    "max_tokens": max_tokens
                }
                
                # Use proxy if configured
                proxy_url = self.proxy if self.proxy else None
                
                # Retry logic for each chunk
                max_retries = 2
                last_error = None
                
                for retry in range(max_retries + 1):
                    try:
                        logger.info(f"🔄 Processing chunk {idx}/{len(chunks)} (size: {len(chunk)} chars, timeout: {chunk_timeout}s, retry: {retry})")
                        
                        async with session.post(
                            self.url, 
                            headers=headers, 
                            json=payload, 
                            proxy=proxy_url,
                            timeout=aiohttp.ClientTimeout(total=chunk_timeout)
                        ) as r:
                            r.raise_for_status()
                            js = await r.json()
                            
                            if 'choices' not in js or not js['choices']:
                                raise RuntimeError(f"Unexpected API response: {js}")
                            
                            txt = js['choices'][0]['message']['content'].strip()
                            if not txt:
                                raise RuntimeError("Empty response from API")
                            
                            out.append(txt)
                            logger.info(f"✓ Chunk {idx}/{len(chunks)} completed ({len(txt)} chars)")
                            break  # Success, exit retry loop
                            
                    except asyncio.TimeoutError:
                        last_error = f"Timeout after {chunk_timeout}s"
                        if retry < max_retries:
                            wait_time = (retry + 1) * 5
                            logger.warning(f"⏱ Chunk {idx} timeout, retrying in {wait_time}s... (attempt {retry + 1}/{max_retries + 1})")
                            await asyncio.sleep(wait_time)
                            # Increase timeout for retry
                            chunk_timeout = min(chunk_timeout + 60, 300)
                        else:
                            logger.error(f"❌ Chunk {idx} failed after {max_retries + 1} attempts: {last_error}")
                            raise RuntimeError(
                                f"DeepSeek API timeout after {max_retries + 1} attempts. "
                                f"Chunk size: {len(chunk)} chars. "
                                f"Try reducing chunk_size (current: {self.config.chunk_size}) or check your network connection."
                            )
                    except aiohttp.ClientResponseError as e:
                        last_error = f"HTTP {e.status}: {e.message}"
                        if e.status == 429:  # Rate limit
                            wait_time = (retry + 1) * 10
                            logger.warning(f"⚠ Rate limited, waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        elif e.status >= 500 and retry < max_retries:  # Server error, retry
                            wait_time = (retry + 1) * 5
                            logger.warning(f"⚠ Server error {e.status}, retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise RuntimeError(f"DeepSeek API HTTP error: {last_error}")
                    except Exception as e:
                        last_error = str(e)
                        if "Cannot connect" in str(e) or "ClientConnectorError" in str(e):
                            if retry < max_retries:
                                wait_time = (retry + 1) * 5
                                logger.warning(f"⚠ Connection error, retrying in {wait_time}s...")
                                await asyncio.sleep(wait_time)
                                continue
                            raise RuntimeError(
                                f"Cannot connect to DeepSeek API after {max_retries + 1} attempts. "
                                f"Check your network connection or set proxy: proxy: http://127.0.0.1:7890"
                            )
                        elif retry < max_retries:
                            wait_time = (retry + 1) * 5
                            logger.warning(f"⚠ Error: {e}, retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise RuntimeError(f"DeepSeek translate error: {last_error}")
                else:
                    # All retries exhausted
                    raise RuntimeError(f"Failed to translate chunk {idx} after {max_retries + 1} attempts: {last_error}")
        
        return '\n\n'.join(out)


def create_translator(config: Config) -> TranslatorBackend:
    backend = config.backend.lower()
    if backend == 'simple':
        return SimpleBackend(config)
    elif backend == 'mymemory':
        return DeepTranslatorBackend(config, service='mymemory')
    elif backend == 'google':
        return DeepTranslatorBackend(config, service='google')
    elif backend == 'argos':
        raise ValueError(
            "backend 'argos' is not implemented in this checkout (and eager argostranslate import "
            "was removed to avoid spaCy model downloads blocking import). Use deepseek, openai, or simple."
        )
    elif backend == 'googletrans':
        return GoogletransBackend(config)
    elif backend == 'deepl':
        return DeepLBackend(config)
    elif backend == 'deepseek':
        return DeepSeekBackend(config)
    elif backend == 'openai':
        return OpenAIBackend(config)
    else:
        raise ValueError(
            f"Unknown backend: {backend}. "
            f"Choose from: simple, mymemory, google, argos, googletrans, deepl, deepseek, openai"
        )


# ----------------------------- Cache -----------------------------
class Cache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str) -> Optional[Dict]:
        key = self._get_key(url)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    return json.load(f)
            except:
                return None
        return None

    def set(self, url: str, data: Dict):
        key = self._get_key(url)
        cache_file = self.cache_dir / f"{key}.json"
        with open(cache_file, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------- HTML Builder -----------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Travel-China.Help</title>
    <meta name="description" content="{title}">
{css_links}
</head>
<body class="{body_class}">
    <header>
        <div class="header-top">🌍 Your trusted source for China travel information since 2024</div>
        <nav class="container">
            <a href="{href_home}" class="logo">Travel-China.Help</a>
            <ul class="nav-menu">
                <li><a href="{href_nav_guides}">Travel Guides</a></li>
                <li><a href="{href_nav_stories}">Travel Stories</a></li>
                <li><a href="{href_nav_visa}">Visa &amp; Entry</a></li>
                <li><a href="{href_nav_practical}">Practical Info</a></li>
            </ul>
        </nav>
    </header>

    <div class="container">
        <div class="breadcrumb">
            <a href="{href_bc_home}">Home</a><span>›</span>
            <a href="{href_bc_section}">{bc_section_label}</a><span>›</span>
            <span style="color: #333;">{title}</span>
        </div>
    </div>

    <div class="container">
        <div class="article-layout">
            <article class="{article_class}">
                {featured_image}
                
                <div class="article-header">
                    <span class="article-category">TRANSLATED ARTICLE</span>
                    <h1 class="article-title">{title}</h1>
                    
                    <div class="article-meta">
                        <div class="meta-item"><span>📅</span><span>{fetched}</span></div>
                        <div class="meta-item"><span>🌐</span><span>Translated: {lang_display}</span></div>
                        <div class="meta-item"><span>📄</span><span><a href="{source_url}" target="_blank" style="color: #c41e3a;">View Original</a></span></div>
                    </div>
                </div>

                <div class="article-content">
                    {content}
                </div>
            </article>

            <aside class="sidebar">
                <div class="widget">
                    <h3>🔥 Popular Articles</h3>
                    <ul class="widget-list">
                        <li><a href="{href_widget_guides}">China Travel Guides</a></li>
                        <li><a href="{href_widget_visa}">Visa Information</a></li>
                        <li><a href="{href_widget_culture}">Chinese Culture</a></li>
                        <li><a href="{href_widget_blog}">Travel Stories</a></li>
                    </ul>
                </div>
            </aside>
        </div>
    </div>

    <footer>
        <div class="container">
            <p>&copy; 2024 Travel-China.Help | <a href="{href_footer_home}">Home</a> | <a href="{source_url}" target="_blank">Original Article</a></p>
            <p style="font-size: 0.85rem; margin-top: 0.5rem; color: #999;">Automated translation service for China travel content</p>
        </div>
    </footer>
</body>
</html>
"""

LANG_NAMES = {
    'zh': 'Chinese (中文)',
    'zh-CN': 'Simplified Chinese (简体中文)',
    'zh-TW': 'Traditional Chinese (繁體中文)',
    'en': 'English',
    'ja': 'Japanese (日本語)',
    'ko': 'Korean (한국어)',
    'es': 'Spanish (Español)',
    'fr': 'French (Français)',
    'de': 'German (Deutsch)',
    'auto': 'Auto-detected'
}

def build_html(
    article: Dict,
    translated_title: str,
    translated_text: str,
    config: Config,
    translated_html: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> str:
    # No featured image - always use placeholder
    featured_image = '<div class="article-featured-placeholder">📰</div>'
    
    # If translated HTML is provided, add CSS to preserve original formatting
    # We'll add inline styles to preserve formatting from the original HTML
    
    # If translated HTML is provided, use it directly (preserves original formatting and images)
    if translated_html and translated_html.strip():
        content_html = translated_html
    else:
        # Convert plain text to HTML with proper paragraph handling
        # Split by double newlines (paragraph breaks)
        paragraphs = translated_text.split('\n\n')
        content_parts = []
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Check if it's a heading (starts with # or is all caps)
            if para.startswith('#'):
                # Markdown-style heading
                heading_text = para.lstrip('#').strip()
                level = len(para) - len(para.lstrip('#'))
                if level <= 1:
                    content_parts.append(f'<h2>{heading_text}</h2>')
                else:
                    content_parts.append(f'<h3>{heading_text}</h3>')
            elif para.isupper() and len(para) < 100:
                # All caps short text = heading
                content_parts.append(f'<h3>{para}</h3>')
            elif para.startswith('- ') or para.startswith('* '):
                # List item - collect consecutive list items
                list_items = [para.lstrip('- ').lstrip('* ').strip()]
                content_parts.append(f'<ul><li>{list_items[0]}</li></ul>')
            elif para.startswith(tuple(f'{i}.' for i in range(1, 10))):
                # Numbered list
                list_item = para.split('.', 1)[1].strip()
                content_parts.append(f'<ol><li>{list_item}</li></ol>')
            else:
                # Regular paragraph - handle single line breaks within paragraph
                para_html = para.replace('\n', '<br>')
                content_parts.append(f'<p>{para_html}</p>')
        
        content_html = '\n'.join(content_parts)
    
    target_lang_display = LANG_NAMES.get(config.target_lang, config.target_lang)
    out = output_path if output_path is not None else _REPO_ROOT / "_translate_output_placeholder.html"
    ctx = site_css_links.page_template_fields(out.resolve(), _REPO_ROOT)

    return HTML_TEMPLATE.format(
        title=translated_title,
        source_url=article['url'],
        fetched=time.strftime('%B %d, %Y', time.localtime()),
        lang=config.target_lang,
        lang_display=target_lang_display,
        featured_image=featured_image,
        content=content_html,
        **ctx,
    )


# ----------------------------- Main Pipeline -----------------------------
async def process_url(
    url: str, 
    session: EnhancedRetrySession, 
    extractor: ArticleExtractor,
    translator: TranslatorBackend,
    config: Config,
    cache: Optional[Cache]
) -> bool:
    """Process single URL, return True if successful"""
    try:
        # Check cache
        if config.use_cache and cache:
            cached = cache.get(url)
            if cached:
                logger.info(f"✓ Using cached: {url}")
                return True
        
        # Fetch HTML
        logger.info(f"⬇ Fetching: {url}")
        html = await session.get(url)
        
        # 验证HTML内容
        if not html:
            logger.error(f"❌ Received empty HTML from {url}")
            return False
        
        if not isinstance(html, str):
            logger.error(f"❌ Invalid HTML type from {url}: {type(html)}")
            return False
        
        # Extract article
        article = extractor.extract(html, url)
        article['url'] = url
        
        # 检查提取的内容
        extracted_text = article.get('text', '')
        extracted_title = article.get('title', '')
        extracted_html = article.get('html')
        
        logger.info(f"📝 Extracted title: {extracted_title[:100]}")
        logger.info(f"📝 Extracted text length: {len(extracted_text)} chars")
        logger.info(f"📝 Extracted HTML length: {len(extracted_html) if extracted_html else 0} chars")
        if extracted_html:
            logger.debug(f"📝 Extracted HTML preview: {extracted_html[:200]}")
        else:
            logger.warning(f"⚠ No HTML content extracted, only plain text available")
        
        if not extracted_text or len(extracted_text) < 100:
            logger.warning(f"⚠ Insufficient content extracted from {url}")
            logger.warning(f"   Title: {extracted_title}")
            logger.warning(f"   Text preview: {extracted_text[:200]}")
            logger.warning(f"   HTML length: {len(html)} chars")
            
            # 检查是否是验证页面
            if '验证' in html or 'captcha' in html.lower() or len(html) < 5000:
                logger.error(f"❌ Likely blocked/verification page. HTML length: {len(html)}")
                logger.error(f"   HTML preview: {html[:500]}")
            
            # 保存HTML到文件以便调试
            try:
                debug_dir = Path(config.output_dir) / '.debug'
                debug_dir.mkdir(parents=True, exist_ok=True)
                debug_file = debug_dir / f"failed_extract_{safe_filename(url)}.html"
                debug_file.write_text(html, encoding='utf-8')
                logger.info(f"💾 Saved HTML to {debug_file} for debugging")
            except Exception as e:
                logger.debug(f"Failed to save debug HTML: {e}")
            
            return False
        
        logger.info(f"✅ Successfully extracted {len(extracted_text)} chars from {url}")
        
        # Translate/rewrite title
        mode_text = "Rewriting" if config.rewrite_mode else "Translating"
        logger.info(f"🔤 {mode_text} title: {article['title']}")
        translated_title = await translator.translate(article['title'])
        
        # Translate/rewrite content
        logger.info(f"🌐 {mode_text} content: {url}")
        translated_content = None
        translated_html = None
        
        # Check if we have HTML content to preserve formatting
        original_html = article.get('html')
        logger.debug(f"Extracted HTML length: {len(original_html) if original_html else 0}")
        if original_html and original_html.strip():
            # Use HTML translation to preserve structure and images
            logger.info(f"📄 Translating HTML content (preserving structure and images, {len(original_html)} chars)")
            try:
                translated_html = await translator.translate_html(original_html)
                logger.info(f"✓ HTML translation completed ({len(translated_html) if translated_html else 0} chars)")
                # Also translate text for fallback
                translated_content = await translator.translate(article['text'])
            except Exception as e:
                logger.warning(f"HTML translation failed: {e}, falling back to text translation")
                logger.warning(f"Exception details: {type(e).__name__}: {str(e)}")
                import traceback
                logger.debug(traceback.format_exc())
                translated_content = await translator.translate(article['text'])
                translated_html = None
        else:
            # Fallback to plain text translation
            logger.info(f"⚠ No HTML content found, using plain text translation")
            translated_content = await translator.translate(article['text'])
            translated_html = None
        
        slug = safe_filename(translated_title)
        output_file = Path(config.output_dir) / f"{slug}.html"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        html_content = build_html(
            article,
            translated_title,
            translated_content or article['text'],
            config,
            translated_html,
            output_path=output_file,
        )
        output_file.write_text(html_content, encoding='utf-8')
        
        # Cache
        if config.use_cache and cache:
            cache.set(url, {'title': translated_title, 'timestamp': time.time()})
        
        logger.info(f"✅ Saved: {output_file}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed {url}: {e}", exc_info=True)
        return False


async def main(config: Config):
    # Read URLs
    urls_file = Path(config.urls_file)
    if not urls_file.exists():
        logger.error(f"URLs file not found: {config.urls_file}")
        return
    
    urls = [line.strip() for line in urls_file.read_text().splitlines() if line.strip()]
    logger.info(f"Found {len(urls)} URLs to process")
    
    # Display configuration
    mode_text = "🎨 Rewrite & Optimize Mode" if config.rewrite_mode else "📝 Translation Mode"
    logger.info(f"{mode_text}: {config.source_lang} → {config.target_lang}")
    
    # Setup
    cache = Cache(Path(config.output_dir) / '.cache') if config.use_cache else None
    extractor = ArticleExtractor(config)
    translator = create_translator(config)
    
    # Process
    # 创建带代理的ClientSession（如果配置了代理）
    connector = None
    if config.proxy:
        # 注意：aiohttp的proxy参数在get/post时传递，不是在connector中
        # 但我们可以创建一个connector用于其他配置
        connector = aiohttp.TCPConnector(ssl=False)
        logger.info(f"🌐 Using proxy for web scraping: {config.proxy}")
    else:
        logger.info("🌐 No proxy configured for web scraping")
    
    async with aiohttp.ClientSession(connector=connector) as aio_session:
        session = EnhancedRetrySession(aio_session, config)
        
        if tqdm:
            tasks = [process_url(url, session, extractor, translator, config, cache) for url in urls]
            results = await tqdm.gather(*tasks, desc="Processing articles")
        else:
            results = await asyncio.gather(*[
                process_url(url, session, extractor, translator, config, cache) 
                for url in urls
            ])
    
    # Summary
    success_count = sum(1 for r in results if r)
    logger.info(f"\n{'='*50}")
    logger.info(f"✅ Successfully processed: {success_count}/{len(urls)}")
    logger.info(f"❌ Failed: {len(urls) - success_count}")
    logger.info(f"📁 Output directory: {config.output_dir}")
    logger.info(f"{'='*50}")


# ----------------------------- CLI -----------------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Batch article translator')
    parser.add_argument('--config', '-c', help='YAML config file')
    parser.add_argument('--input', '-i', default='urls.txt', help='URLs file')
    parser.add_argument('--outdir', '-o', default='output', help='Output directory')
    parser.add_argument('--source', '-s', default='auto', help='Source language (auto for auto-detection)')
    parser.add_argument('--lang', '-l', default='zh', help='Target language')
    parser.add_argument('--backend', '-b', default='simple', 
                       choices=['simple', 'mymemory', 'google', 'argos', 'googletrans', 'deepl', 'deepseek', 'openai'],
                       help='Translation backend')
    parser.add_argument('--rewrite', '-r', action='store_true', 
                       help='Enable content rewriting and optimization (only works with deepseek/openai)')
    parser.add_argument('--proxy', '-p', help='Proxy URL (e.g., http://127.0.0.1:7890 or socks5://127.0.0.1:1080)')
    parser.add_argument('--browser', action='store_true',
                       help='Use browser automation (Playwright/Selenium) for SPA pages')
    parser.add_argument('--browser-type', choices=['playwright', 'selenium'], default='playwright',
                       help='Browser automation type (default: playwright)')
    parser.add_argument('--browser-headless', action='store_true', default=True,
                       help='Run browser in headless mode (default: True)')
    parser.add_argument('--browser-wait', type=int, default=3,
                       help='Wait time for JavaScript rendering (seconds, default: 3)')
    parser.add_argument('--concurrency', type=int, default=6, help='Max concurrent requests')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout (seconds)')
    parser.add_argument('--no-cache', dest='cache', action='store_false', help='Disable caching')
    
    args = parser.parse_args()
    
    # Load config
    if args.config and yaml:
        config = Config.from_yaml(args.config)
    else:
        config = Config.from_args(args)
    
    # Run
    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        logger.info("\n⚠ Interrupted by user")
        sys.exit(1)
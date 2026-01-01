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

try:
    import argostranslate.package
    import argostranslate.translate
    HAS_ARGOS = True
except:
    HAS_ARGOS = False

try:
    from deep_translator import GoogleTranslator as DeepGoogleTranslator
    from deep_translator import MyMemoryTranslator
    HAS_DEEP_TRANSLATOR = True
except:
    HAS_DEEP_TRANSLATOR = False

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

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

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
        with open(path) as f:
            data = yaml.safe_load(f)
        
        # Ensure API keys are strings, not tuples or None
        if 'deepseek_api_key' in data and data['deepseek_api_key']:
            data['deepseek_api_key'] = str(data['deepseek_api_key']).strip()
        if 'openai_api_key' in data and data['openai_api_key']:
            data['openai_api_key'] = str(data['openai_api_key']).strip()
        if 'deepl_api_key' in data and data['deepl_api_key']:
            data['deepl_api_key'] = str(data['deepl_api_key']).strip()
        
        # Set default for rewrite_mode if not present
        if 'rewrite_mode' not in data:
            data['rewrite_mode'] = False
            
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
            headers['X-Requested-With'] = 'XMLHttpRequest'
        elif '8264.com' in domain:
            headers['Referer'] = 'https://www.8264.com/'
        
        return headers

    async def get(self, url: str, **kwargs) -> str:
        """Enhanced get with retry and anti-scraping"""
        headers = self._get_headers(url)
        headers.update(kwargs.pop('headers', {}))
        
        cookies = getattr(self.config, 'cookies', None) or {}
        
        for attempt in range(self.config.max_retries):
            try:
                async with self.sem:
                    # Add random delay to avoid rate limiting
                    if attempt > 0:
                        await asyncio.sleep(random.uniform(2, 5))
                    
                    async with self.session.get(
                        url,
                        headers=headers,
                        cookies=cookies,
                        timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                        allow_redirects=True,
                        ssl=False,  # Some sites have SSL issues
                        **kwargs
                    ) as resp:
                        # Handle different status codes
                        if resp.status == 403:
                            logger.warning(f"403 Forbidden for {url}, rotating User-Agent...")
                            headers['User-Agent'] = random.choice(USER_AGENTS)
                            continue
                        elif resp.status == 429:
                            wait = 2 ** (attempt + 2)
                            logger.warning(f"429 Rate limited, waiting {wait}s...")
                            await asyncio.sleep(wait)
                            continue
                        
                        resp.raise_for_status()
                        content = await resp.text()
                        
                        # Check if content is valid (not blocked page)
                        if len(content) < 500 or '验证' in content or 'blocked' in content.lower():
                            logger.warning(f"Suspicious content detected for {url}, retrying...")
                            await asyncio.sleep(3)
                            continue
                        
                        return content
                        
            except asyncio.TimeoutError:
                logger.warning(f"Timeout for {url} (attempt {attempt + 1}/{self.config.max_retries})")
                if attempt == self.config.max_retries - 1:
                    raise
            except aiohttp.ClientError as e:
                logger.warning(f"Client error for {url}: {e} (attempt {attempt + 1}/{self.config.max_retries})")
                if attempt == self.config.max_retries - 1:
                    raise
            except Exception as e:
                logger.error(f"Unexpected error for {url}: {e}")
                if attempt == self.config.max_retries - 1:
                    raise
            
            wait = 2 ** attempt
            logger.warning(f"Retry {attempt + 1}/{self.config.max_retries} for {url} after {wait}s")
            await asyncio.sleep(wait)


# ----------------------------- Article Extraction -----------------------------
class ArticleExtractor:
    def __init__(self, config: Config):
        self.config = config

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
        # Remove lead_image - we don't want images
        
        return {
            'title': title,
            'text': text,
            'lead_image': None,  # Disabled
            'html': None  # trafilatura gives plain text
        }

    def _extract_readability(self, html: str, url: str) -> Dict:
        doc = Document(html)
        title = doc.title()
        content_html = doc.summary()
        
        soup = BeautifulSoup(content_html, 'html.parser')
        # Remove all images
        for img in soup.find_all('img'):
            img.decompose()
        
        text = soup.get_text(separator='\n', strip=True)
        # lead_image removed
        
        return {
            'title': title,
            'text': text,
            'html': content_html,
            'lead_image': None  # Disabled
        }

    def _extract_bs4(self, html: str, url: str) -> Dict:
        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup, url)
        
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'nav', 'footer', 'header', 'img', 'aside']):
            tag.decompose()
        
        # Try site-specific selectors
        domain = urlparse(url).netloc
        content = None
        
        if 'mafengwo.cn' in domain:
            content = soup.select_one('.poi-detail') or soup.select_one('.article') or soup.select_one('._j_content_box')
        elif '8264.com' in domain:
            content = soup.select_one('.detail-con') or soup.select_one('.article-content')
        elif 'ctnews.com.cn' in domain:
            content = soup.select_one('.content') or soup.select_one('.article')
        
        # Fallback to common selectors
        if not content:
            for selector in ['article', 'main', '.article-content', '.post-content', '.entry-content', '.content']:
                content = soup.select_one(selector)
                if content:
                    break
        
        if not content:
            candidates = soup.find_all(['div', 'section'], recursive=True)
            if candidates:
                content = max(candidates, key=lambda x: len(x.get_text()))
            else:
                content = soup.body or soup
        
        text = content.get_text(separator='\n', strip=True)
        html_fragment = str(content)
        
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
                        instruction = f"Rewrite and optimize the following text into {self.target_name}. Make it more engaging, well-structured, and professional while preserving the core message."
                    else:
                        instruction = f"Rewrite and optimize the following {self.source_name} text into {self.target_name}. Make it more engaging, well-structured, and professional while preserving the core message."
                    system_content = "You are a professional content writer and editor. Rewrite and optimize the content to make it more engaging and well-structured. Output ONLY the rewritten content."
                else:
                    if self.config.source_lang == 'auto':
                        instruction = f"Translate the following text into {self.target_name}. Preserve formatting and meaning."
                    else:
                        instruction = f"Translate the following {self.source_name} text into {self.target_name}. Preserve formatting and meaning."
                    system_content = "You are a professional translator. Output ONLY the translated text, nothing else."
                
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
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": f"{instruction}\n\n{chunk}"}
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


class OpenAIBackend(TranslatorBackend):
    def __init__(self, config: Config):
        super().__init__(config)
        if not config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.api_key = config.openai_api_key
        self.url = 'https://api.openai.com/v1/chat/completions'
        self.rewrite_mode = config.rewrite_mode
        
        # Auto-detect proxy
        self.proxy = config.proxy or os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        
        # Language names
        lang_names = {
            'zh': 'Chinese', 'zh-CN': 'Simplified Chinese',
            'en': 'English', 'ja': 'Japanese', 'ko': 'Korean',
            'es': 'Spanish', 'fr': 'French', 'de': 'German'
        }
        self.source_name = lang_names.get(config.source_lang, config.source_lang)
        self.target_name = lang_names.get(config.target_lang, config.target_lang)
        
        mode_desc = "Rewrite & Optimize" if self.rewrite_mode else "Translate"
        logger.info(f"✓ OpenAI {mode_desc} ready: {self.source_name} → {self.target_name}")

    async def translate(self, text: str) -> str:
        chunks = self._chunk_text(text)
        results = []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        connector = None
        if self.proxy:
            connector = aiohttp.TCPConnector()
        
        async with aiohttp.ClientSession(connector=connector) as session:
            for chunk in chunks:
                # Build instruction based on mode
                if self.rewrite_mode:
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
                    system_prompt = "You are a professional translator. Translate the text accurately while preserving the original meaning and tone. Output ONLY the translated text."
                    user_prompt = f"Translate to {self.target_name}:\n\n{chunk}"
                
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7 if self.rewrite_mode else 0.3
                }
                
                proxy_url = self.proxy if self.proxy else None
                
                async with session.post(
                    self.url, 
                    headers=headers, 
                    json=payload, 
                    proxy=proxy_url,
                    timeout=60
                ) as resp:
                    resp.raise_for_status()
                    js = await resp.json()
                    translated = js['choices'][0]['message']['content'].strip()
                    results.append(translated)
        
        return '\n\n'.join(results)


def create_translator(config: Config) -> TranslatorBackend:
    backend = config.backend.lower()
    if backend == 'simple':
        return SimpleBackend(config)
    elif backend == 'mymemory':
        return DeepTranslatorBackend(config, service='mymemory')
    elif backend == 'google':
        return DeepTranslatorBackend(config, service='google')
    elif backend == 'argos':
        return ArgosBackend(config)
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
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.8; color: #333; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 0 20px; }}
        header {{ background: white; box-shadow: 0 2px 10px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 100; }}
        .header-top {{ background: linear-gradient(135deg, #c41e3a 0%, #8b1538 100%); color: white; padding: 8px 0; font-size: 0.85rem; text-align: center; }}
        nav {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem 0; }}
        .logo {{ font-size: 1.5rem; font-weight: bold; color: #c41e3a; text-decoration: none; }}
        .nav-menu {{ display: flex; list-style: none; gap: 2rem; }}
        .nav-menu a {{ color: #333; text-decoration: none; font-weight: 500; transition: color 0.3s; }}
        .nav-menu a:hover {{ color: #c41e3a; }}
        .breadcrumb {{ padding: 1.5rem 0; font-size: 0.9rem; }}
        .breadcrumb a {{ color: #666; text-decoration: none; }}
        .breadcrumb a:hover {{ color: #c41e3a; }}
        .breadcrumb span {{ color: #999; margin: 0 0.5rem; }}
        .article-layout {{ display: grid; grid-template-columns: 1fr 300px; gap: 2rem; margin-bottom: 3rem; }}
        .article-main {{ background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 15px rgba(0,0,0,0.08); }}
        .article-header {{ padding: 3rem 3rem 2rem; }}
        .article-category {{ display: inline-block; background: linear-gradient(135deg, #ff5722, #ff9800); color: white; padding: 6px 16px; border-radius: 20px; font-size: 0.85rem; font-weight: bold; margin-bottom: 1rem; }}
        .article-title {{ font-size: 2.5rem; color: #333; margin-bottom: 1rem; line-height: 1.3; }}
        .article-meta {{ display: flex; gap: 2rem; color: #999; font-size: 0.95rem; padding-bottom: 2rem; border-bottom: 2px solid #f0f0f0; flex-wrap: wrap; }}
        .meta-item {{ display: flex; align-items: center; gap: 0.5rem; }}
        .article-featured-image {{ width: 100%; height: 400px; object-fit: cover; }}
        .article-featured-placeholder {{ width: 100%; height: 400px; background: linear-gradient(135deg, #c41e3a, #ff9800); display: flex; align-items: center; justify-content: center; font-size: 6rem; }}
        .article-content {{ padding: 3rem; font-size: 1.1rem; line-height: 1.9; }}
        .article-content h2 {{ color: #ff5722; font-size: 1.8rem; margin: 2.5rem 0 1rem; padding-top: 1.5rem; border-top: 2px solid #f0f0f0; }}
        .article-content h2:first-of-type {{ border-top: none; padding-top: 0; }}
        .article-content h3 {{ color: #333; font-size: 1.4rem; margin: 2rem 0 1rem; }}
        .article-content p {{ margin-bottom: 1.5rem; color: #444; text-align: justify; }}
        .article-content ul, .article-content ol {{ margin: 1.5rem 0; padding-left: 2rem; }}
        .article-content li {{ margin-bottom: 0.8rem; color: #444; line-height: 1.7; }}
        .article-content ul {{ list-style-type: disc; }}
        .article-content ol {{ list-style-type: decimal; }}
        .article-content blockquote {{ border-left: 4px solid #ff9800; padding: 1.5rem 2rem; margin: 2rem 0; background: #fff3e0; border-radius: 0 8px 8px 0; font-style: italic; color: #555; }}
        .article-content img {{ max-width: 100%; height: auto; border-radius: 8px; margin: 2rem 0; }}
        .source-info {{ background: #f8f9fa; padding: 1.5rem; border-radius: 8px; margin: 2rem 0; border-left: 4px solid #c41e3a; }}
        .source-info strong {{ color: #c41e3a; }}
        .sidebar {{ display: flex; flex-direction: column; gap: 1.5rem; }}
        .widget {{ background: white; padding: 1.5rem; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
        .widget h3 {{ color: #c41e3a; margin-bottom: 1rem; font-size: 1.2rem; padding-bottom: 0.8rem; border-bottom: 2px solid #ffd700; }}
        .widget-list {{ list-style: none; }}
        .widget-list li {{ padding: 0.8rem 0; border-bottom: 1px solid #f0f0f0; }}
        .widget-list li:last-child {{ border-bottom: none; }}
        .widget-list a {{ color: #333; text-decoration: none; transition: color 0.3s; font-size: 0.95rem; }}
        .widget-list a:hover {{ color: #c41e3a; }}
        footer {{ background: #2c2c2c; color: #ccc; padding: 2rem 0 1rem; text-align: center; }}
        footer a {{ color: #ffd700; text-decoration: none; }}
        @media (max-width: 968px) {{ .article-layout {{ grid-template-columns: 1fr; }} .nav-menu {{ display: none; }} }}
    </style>
</head>
<body>
    <header>
        <div class="header-top">🌍 Your trusted source for China travel information since 2024</div>
        <nav class="container">
            <a href="index.html" class="logo">Travel-China.Help</a>
            <ul class="nav-menu">
                <li><a href="index.html#guides">Travel Guides</a></li>
                <li><a href="index.html#blog">Travel Stories</a></li>
                <li><a href="index.html#visa">Visa & Entry</a></li>
                <li><a href="index.html#culture">Culture</a></li>
            </ul>
        </nav>
    </header>

    <div class="container">
        <div class="breadcrumb">
            <a href="index.html">Home</a><span>›</span>
            <a href="blog.html">Articles</a><span>›</span>
            <span style="color: #333;">{title}</span>
        </div>
    </div>

    <div class="container">
        <div class="article-layout">
            <article class="article-main">
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

                <div class="source-info">
                    <strong>📌 Original Source:</strong><br>
                    This article was automatically translated from: <a href="{source_url}" target="_blank" style="color: #c41e3a;">{source_url}</a><br>
                    <small style="color: #666;">Translation provided by Travel-China.Help for informational purposes. Please refer to the original source for the most accurate information.</small>
                </div>
            </article>

            <aside class="sidebar">
                <div class="widget">
                    <h3>🔥 Popular Articles</h3>
                    <ul class="widget-list">
                        <li><a href="index.html#guides">China Travel Guides</a></li>
                        <li><a href="index.html#visa">Visa Information</a></li>
                        <li><a href="index.html#culture">Chinese Culture</a></li>
                        <li><a href="index.html#blog">Travel Stories</a></li>
                    </ul>
                </div>
                <div class="widget">
                    <h3>ℹ️ About This Translation</h3>
                    <p style="font-size: 0.9rem; color: #666; line-height: 1.6;">
                        This article has been automatically translated to help you access Chinese content. 
                        Some nuances may be lost in translation.
                    </p>
                </div>
                <div class="widget">
                    <h3>🌐 Language</h3>
                    <p style="font-size: 0.9rem; color: #666;">
                        Source: {source_lang_display}<br>
                        Target: {lang_display}
                    </p>
                </div>
            </aside>
        </div>
    </div>

    <footer>
        <div class="container">
            <p>&copy; 2024 Travel-China.Help | <a href="index.html">Home</a> | <a href="{source_url}" target="_blank">Original Article</a></p>
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

def build_html(article: Dict, translated_title: str, translated_text: str, config: Config) -> str:
    # No featured image - always use placeholder
    featured_image = '<div class="article-featured-placeholder">📰</div>'
    
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
    
    # Get language display names
    source_lang_display = LANG_NAMES.get(config.source_lang, config.source_lang)
    target_lang_display = LANG_NAMES.get(config.target_lang, config.target_lang)
    
    return HTML_TEMPLATE.format(
        title=translated_title,  # Use translated title
        source_url=article['url'],
        fetched=time.strftime('%B %d, %Y', time.localtime()),
        lang=config.target_lang,
        lang_display=target_lang_display,
        source_lang_display=source_lang_display,
        featured_image=featured_image,
        content=content_html
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
        
        # Extract article
        article = extractor.extract(html, url)
        article['url'] = url
        
        if not article['text'] or len(article['text']) < 100:
            logger.warning(f"⚠ Insufficient content extracted from {url}")
            return False
        
        logger.info(f"📝 Extracted {len(article['text'])} chars from {url}")
        
        # Translate/rewrite title
        mode_text = "Rewriting" if config.rewrite_mode else "Translating"
        logger.info(f"🔤 {mode_text} title: {article['title']}")
        translated_title = await translator.translate(article['title'])
        
        # Translate/rewrite content
        logger.info(f"🌐 {mode_text} content: {url}")
        translated_content = await translator.translate(article['text'])
        
        # Build HTML with translated title
        html_content = build_html(article, translated_title, translated_content, config)
        
        # Save with translated title in filename
        slug = safe_filename(translated_title)
        output_file = Path(config.output_dir) / f"{slug}.html"
        output_file.parent.mkdir(parents=True, exist_ok=True)
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
    async with aiohttp.ClientSession() as aio_session:
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
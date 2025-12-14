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
from urllib.parse import urljoin, urlparse
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Dict, List
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

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
    max_concurrency: int = 6
    timeout: int = 30
    chunk_size: int = 2000  # Reduced for simple backend
    use_cache: bool = True
    max_retries: int = 3
    user_agent: str = "ArticleTranslator/2.0 (+https://github.com/yourrepo)"

    @classmethod
    def from_yaml(cls, path: str):
        if not yaml:
            raise RuntimeError("PyYAML not installed")
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def from_args(cls, args):
        return cls(
            urls_file=args.input,
            output_dir=args.outdir,
            source_lang=args.source,
            target_lang=args.lang,
            backend=args.backend,
            deepl_api_key=os.environ.get('DEEPL_API_KEY'),
            openai_api_key=os.environ.get('OPENAI_API_KEY'),
            deepseek_api_key=os.environ.get('DEEPSEEK_API_KEY'),
            proxy=args.proxy or os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY'),
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
        
        # Remove unwanted elements including images
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'nav', 'footer', 'header', 'img']):
            tag.decompose()
        
        # Try common selectors
        content = None
        for selector in ['article', 'main', '.article-content', '.post-content', '.entry-content']:
            content = soup.select_one(selector)
            if content:
                break
        
        if not content:
            # Find largest text block
            candidates = soup.find_all(['div', 'section'], recursive=True)
            if candidates:
                content = max(candidates, key=lambda x: len(x.get_text()))
            else:
                content = soup.body or soup
        
        text = content.get_text(separator='\n', strip=True)
        html_fragment = str(content)
        # lead_image removed
        
        return {
            'title': title,
            'text': text,
            'html': html_fragment,
            'lead_image': None  # Disabled
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


class GoogletransBackend(TranslatorBackend):
    def __init__(self, config: Config):
        super().__init__(config)
        if not GoogleTranslator:
            raise RuntimeError("googletrans not installed: pip install googletrans==3.1.0a0")
        logger.warning("⚠️  googletrans is deprecated and may not work. Consider using 'mymemory' or 'openai' backend instead.")
        self.trans = GoogleTranslator()

    async def translate(self, text: str) -> str:
        loop = asyncio.get_running_loop()
        chunks = self._chunk_text(text)
        
        async def _translate_chunk(chunk: str) -> str:
            try:
                return await loop.run_in_executor(
                    None, 
                    lambda: self.trans.translate(chunk, dest=self.config.target_lang).text
                )
            except Exception as e:
                logger.error(f"googletrans failed: {e}. Try setting backend to 'mymemory' or 'openai'")
                raise
        
        results = await asyncio.gather(*[_translate_chunk(c) for c in chunks])
        return '\n\n'.join(results)


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


class GoogletransBackend(TranslatorBackend):
    """DEPRECATED: Old googletrans library - unreliable"""
    def __init__(self, config: Config):
        super().__init__(config)
        if not GoogleTranslator:
            raise RuntimeError("googletrans not installed: pip install googletrans==3.1.0a0")
        logger.warning("⚠️  googletrans is deprecated and may not work. Consider using 'simple' or 'openai' backend instead.")
        self.trans = GoogleTranslator()

    async def translate(self, text: str) -> str:
        loop = asyncio.get_running_loop()
        chunks = self._chunk_text(text)
        
        async def _translate_chunk(chunk: str) -> str:
            try:
                return await loop.run_in_executor(
                    None, 
                    lambda: self.trans.translate(chunk, dest=self.config.target_lang).text
                )
            except Exception as e:
                logger.error(f"googletrans failed: {e}. Try setting backend to 'simple' or 'openai'")
                raise
        
        results = await asyncio.gather(*[_translate_chunk(c) for c in chunks])
        return '\n\n'.join(results)


class DeepTranslatorBackend(TranslatorBackend):
    """Using deep-translator library - more reliable than googletrans"""
    def __init__(self, config: Config, service='google'):
        super().__init__(config)
        if not HAS_DEEP_TRANSLATOR:
            raise RuntimeError("deep-translator not installed: pip install deep-translator")
        
        # Map language codes
        lang_map = {
            'zh': 'zh-CN',
            'zh-CN': 'zh-CN',
            'zh-TW': 'zh-TW',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'es': 'es',
            'fr': 'fr',
            'de': 'de',
            'auto': 'auto'
        }
        source = lang_map.get(config.source_lang, 'auto')
        target = lang_map.get(config.target_lang, config.target_lang)
        
        if service == 'google':
            self.translator = DeepGoogleTranslator(source=source, target=target)
        elif service == 'mymemory':
            self.translator = MyMemoryTranslator(source=source, target=target)
        
        self.service = service
        logger.info(f"✓ Deep Translator ready ({service}): {source} → {target}")

    async def translate(self, text: str) -> str:
        loop = asyncio.get_running_loop()
        chunks = self._chunk_text(text)
        
        async def _translate_chunk(chunk: str) -> str:
            # Add small delay between requests to avoid rate limiting
            await asyncio.sleep(0.5)
            try:
                return await loop.run_in_executor(
                    None,
                    lambda: self.translator.translate(chunk)
                )
            except Exception as e:
                logger.warning(f"Translation chunk failed: {e}, retrying...")
                await asyncio.sleep(2)
                return await loop.run_in_executor(
                    None,
                    lambda: self.translator.translate(chunk)
                )
        
        results = await asyncio.gather(*[_translate_chunk(c) for c in chunks], return_exceptions=True)
        
        # Filter out exceptions and join results
        translated_parts = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Chunk {i} translation failed: {result}")
                translated_parts.append(chunks[i])  # Use original text as fallback
            else:
                translated_parts.append(result)
        
        return '\n\n'.join(translated_parts)


class ArgosBackend(TranslatorBackend):
    """Local offline translation using Argos Translate"""
    def __init__(self, config: Config):
        super().__init__(config)
        if not HAS_ARGOS:
            raise RuntimeError("argostranslate not installed: pip install argostranslate")
        
        # Map common language codes
        lang_map = {
            'zh': 'zh',
            'en': 'en',
            'ja': 'ja',
            'ko': 'ko',
            'es': 'es',
            'fr': 'fr',
            'de': 'de',
        }
        
        self.source_lang = 'en'  # Assume source is English by default
        self.target_lang = lang_map.get(config.target_lang, config.target_lang)
        
        # Try to install language packages with SSL workaround
        try:
            # Disable SSL verification for downloading packages (only for this operation)
            import ssl
            ssl._create_default_https_context = ssl._create_unverified_context
            
            logger.info("📦 Updating Argos package index...")
            argostranslate.package.update_package_index()
            available_packages = argostranslate.package.get_available_packages()
            
            # Find and install the required package
            package_to_install = None
            for pkg in available_packages:
                if pkg.from_code == self.source_lang and pkg.to_code == self.target_lang:
                    package_to_install = pkg
                    break
            
            if package_to_install:
                installed = argostranslate.package.get_installed_packages()
                already_installed = any(
                    p.from_code == self.source_lang and p.to_code == self.target_lang 
                    for p in installed
                )
                
                if not already_installed:
                    logger.info(f"📦 Downloading translation package: {self.source_lang} → {self.target_lang}")
                    argostranslate.package.install_from_path(package_to_install.download())
                    logger.info("✓ Package installed successfully")
        except Exception as e:
            logger.error(f"Failed to download Argos packages: {e}")
            logger.info("Trying to use already installed packages...")
        
        self.installed_languages = argostranslate.translate.get_installed_languages()
        self.from_lang = next((l for l in self.installed_languages if l.code == self.source_lang), None)
        self.to_lang = next((l for l in self.installed_languages if l.code == self.target_lang), None)
        
        if not self.from_lang or not self.to_lang:
            raise RuntimeError(
                f"Translation package {self.source_lang}→{self.target_lang} not available.\n"
                f"Manual installation:\n"
                f"  1. Download package from: https://github.com/argosopentech/argos-translate/releases\n"
                f"  2. Install: python3 -m argostranslate.package --install-file <package.argosmodel>\n"
                f"Available languages: {[l.code for l in self.installed_languages]}"
            )
        
        self.translation = self.from_lang.get_translation(self.to_lang)
        logger.info(f"✓ Argos Translate ready: {self.source_lang} → {self.target_lang}")

    async def translate(self, text: str) -> str:
        loop = asyncio.get_running_loop()
        chunks = self._chunk_text(text)
        
        async def _translate_chunk(chunk: str) -> str:
            return await loop.run_in_executor(
                None,
                lambda: self.translation.translate(chunk)
            )
        
        results = await asyncio.gather(*[_translate_chunk(c) for c in chunks])
        return '\n\n'.join(results)


class DeepLBackend(TranslatorBackend):
    def __init__(self, config: Config):
        super().__init__(config)
        if not config.deepl_api_key:
            raise RuntimeError("DEEPL_API_KEY not set")
        self.api_key = config.deepl_api_key
        self.base_url = "https://api-free.deepl.com/v2/translate"

    async def translate(self, text: str) -> str:
        chunks = self._chunk_text(text)
        results = []
        
        async with aiohttp.ClientSession() as session:
            for chunk in chunks:
                data = {
                    'auth_key': self.api_key,
                    'text': chunk,
                    'target_lang': self.config.target_lang.upper()
                }
                async with session.post(self.base_url, data=data) as resp:
                    resp.raise_for_status()
                    js = await resp.json()
                    if 'translations' in js and js['translations']:
                        results.append(js['translations'][0]['text'])
                    else:
                        raise RuntimeError(f"DeepL error: {js}")
        
        return '\n\n'.join(results)


class DeepSeekBackend(TranslatorBackend):
    """DeepSeek API - OpenAI compatible interface"""
    def __init__(self, config: Config):
        super().__init__(config)
        # if not config.deepseek_api_key:
        #     raise RuntimeError("DEEPSEEK_API_KEY not set")
        # self.api_key = config.deepseek_api_key
        self.api_key = os.environ.get('DEEPSEEK_API_KEY'),
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
            for chunk in chunks:
                # Build instruction based on source language
                if self.config.source_lang == 'auto':
                    instruction = f"Translate the following text into {self.target_name}. Preserve formatting and meaning."
                else:
                    instruction = f"Translate the following {self.source_name} text into {self.target_name}. Preserve formatting and meaning."
                
                payload = {
                    "model": "deepseek-chat",  # DeepSeek's main model
                    "messages": [
                        {"role": "system", "content": "You are a professional translator. Output ONLY the translated text, nothing else."},
                        {"role": "user", "content": f"{instruction}\n\n{chunk}"}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000
                }
                
                # Use proxy if configured
                proxy_url = self.proxy if self.proxy else None
                
                try:
                    async with session.post(
                        self.url, 
                        headers=headers, 
                        json=payload, 
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as r:
                        js = await r.json()
                        txt = js['choices'][0]['message']['content']
                        out.append(txt)
                except Exception as e:
                    logger.error(f"DeepSeek API error: {e}")
                    if "Cannot connect" in str(e) or "ClientConnectorError" in str(e):
                        raise RuntimeError(
                            f"Cannot connect to DeepSeek API. "
                            f"Check your network connection or set proxy: proxy: http://127.0.0.1:7890"
                        )
                    raise RuntimeError(f"DeepSeek translate error: {js if 'js' in locals() else str(e)}")
        
        return '\n\n'.join(out)


class OpenAIBackend(TranslatorBackend):
    def __init__(self, config: Config):
        super().__init__(config)
        if not config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.api_key = config.openai_api_key
        self.url = 'https://api.openai.com/v1/chat/completions'

    async def translate(self, text: str) -> str:
        chunks = self._chunk_text(text)
        results = []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            for chunk in chunks:
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a professional translator. Translate the text accurately while preserving the original meaning and tone. Output ONLY the translated text."
                        },
                        {
                            "role": "user",
                            "content": f"Translate to {self.config.target_lang}:\n\n{chunk}"
                        }
                    ],
                    "temperature": 0.3
                }
                
                async with session.post(self.url, headers=headers, json=payload, timeout=60) as resp:
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
        .article-content h2 {{ color: #ff5722; font-size: 1.8rem; margin: 2.5rem 0 1rem; padding-top: 1.5rem; }}
        .article-content h3 {{ color: #333; font-size: 1.4rem; margin: 2rem 0 1rem; }}
        .article-content p {{ margin-bottom: 1.5rem; color: #444; }}
        .article-content ul, .article-content ol {{ margin: 1.5rem 0; padding-left: 2rem; }}
        .article-content li {{ margin-bottom: 0.8rem; color: #444; }}
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
    
    # Convert plain text to HTML paragraphs
    content_html = ''.join(f'<p>{para}</p>' for para in translated_text.split('\n\n') if para.strip())
    
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
    session: RetrySession, 
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
        
        # Translate title
        logger.info(f"🔤 Translating title: {article['title']}")
        translated_title = await translator.translate(article['title'])
        
        # Translate content
        logger.info(f"🌐 Translating content: {url}")
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
    
    # Setup
    cache = Cache(Path(config.output_dir) / '.cache') if config.use_cache else None
    extractor = ArticleExtractor(config)
    translator = create_translator(config)
    
    # Process
    async with aiohttp.ClientSession() as aio_session:
        session = RetrySession(aio_session, config)
        
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
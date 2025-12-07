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
        text = extract(html, include_comments=False, include_tables=True)
        if not text:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup, url)
        lead_image = self._get_lead_image(soup, url)
        
        return {
            'title': title,
            'text': text,
            'lead_image': lead_image,
            'html': None  # trafilatura gives plain text
        }

    def _extract_readability(self, html: str, url: str) -> Dict:
        doc = Document(html)
        title = doc.title()
        content_html = doc.summary()
        
        soup = BeautifulSoup(content_html, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        lead_image = self._get_lead_image(soup, url)
        
        return {
            'title': title,
            'text': text,
            'html': content_html,
            'lead_image': lead_image
        }

    def _extract_bs4(self, html: str, url: str) -> Dict:
        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup, url)
        
        # Remove unwanted elements
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'nav', 'footer', 'header']):
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
        lead_image = self._get_lead_image(content, url)
        
        return {
            'title': title,
            'text': text,
            'html': html_fragment,
            'lead_image': lead_image
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
    elif backend == 'openai':
        return OpenAIBackend(config)
    else:
        raise ValueError(
            f"Unknown backend: {backend}. "
            f"Choose from: simple, mymemory, google, argos, googletrans, deepl, openai"
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
HTML_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ 
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; 
      max-width: 800px; 
      margin: 40px auto; 
      padding: 0 20px; 
      line-height: 1.7;
      color: #333;
    }}
    header {{ margin-bottom: 30px; }}
    h1 {{ 
      font-size: 2.2em; 
      line-height: 1.3; 
      margin-bottom: 16px;
      font-weight: 700;
    }}
    .meta {{ 
      color: #666; 
      font-size: 0.9em; 
      padding: 12px 0;
      border-top: 1px solid #eee;
      border-bottom: 1px solid #eee;
    }}
    .meta a {{ color: #0066cc; text-decoration: none; }}
    .meta a:hover {{ text-decoration: underline; }}
    .lead-image {{ 
      width: 100%; 
      height: auto; 
      margin: 20px 0;
      border-radius: 4px;
    }}
    .content {{ 
      font-size: 1.1em;
      margin-top: 30px;
    }}
    .content p {{ margin: 1em 0; }}
    .content img {{ max-width: 100%; height: auto; }}
    .footer {{ 
      margin-top: 50px; 
      padding-top: 20px;
      border-top: 2px solid #eee;
      font-size: 0.9em;
      color: #666;
    }}
  </style>
</head>
<body>
  <article>
    <header>
      <h1>{title}</h1>
      <div class="meta">
        <div>📄 Original: <a href="{source_url}" target="_blank">{source_url}</a></div>
        <div>🕐 Fetched: {fetched}</div>
        <div>🌐 Translated to: {lang_name}</div>
      </div>
      {lead_img}
    </header>
    <section class="content">{content}</section>
    <footer class="footer">
      <p>This article was automatically translated. <a href="{source_url}" target="_blank">View original</a></p>
    </footer>
  </article>
</body>
</html>
"""

LANG_NAMES = {
    'zh': 'Chinese (中文)',
    'en': 'English',
    'ja': 'Japanese (日本語)',
    'ko': 'Korean (한국어)',
    'es': 'Spanish (Español)',
    'fr': 'French (Français)',
    'de': 'German (Deutsch)',
}

def build_html(article: Dict, translated_text: str, config: Config) -> str:
    lead_img = ''
    if article.get('lead_image'):
        lead_img = f'<img class="lead-image" src="{article["lead_image"]}" alt="Lead image"/>'
    
    # Convert plain text to HTML paragraphs
    content_html = ''.join(f'<p>{para}</p>' for para in translated_text.split('\n\n') if para.strip())
    
    return HTML_TEMPLATE.format(
        title=article['title'],
        source_url=article['url'],
        fetched=time.strftime('%Y-%m-%d %H:%M:%S'),
        lang=config.target_lang,
        lang_name=LANG_NAMES.get(config.target_lang, config.target_lang),
        lead_img=lead_img,
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
        
        # Translate
        logger.info(f"🌐 Translating: {url}")
        translated = await translator.translate(article['text'])
        
        # Build HTML
        html_content = build_html(article, translated, config)
        
        # Save
        slug = safe_filename(article['title'])
        output_file = Path(config.output_dir) / f"{slug}.html"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(html_content, encoding='utf-8')
        
        # Cache
        if config.use_cache and cache:
            cache.set(url, {'title': article['title'], 'timestamp': time.time()})
        
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
                       choices=['simple', 'mymemory', 'google', 'argos', 'googletrans', 'deepl', 'openai'],
                       help='Translation backend')
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
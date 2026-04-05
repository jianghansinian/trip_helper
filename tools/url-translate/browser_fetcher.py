#!/usr/bin/env python3
"""
浏览器自动化抓取模块
支持Playwright和Selenium，用于抓取SPA（单页应用）页面

安装依赖：
    # Playwright (推荐)
    pip install playwright
    playwright install chromium
    
    # 或 Selenium
    pip install selenium
    # 需要下载ChromeDriver: https://chromedriver.chromium.org/
"""

import asyncio
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 尝试导入Playwright
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    PlaywrightTimeoutError = None

# 尝试导入Selenium
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False


class BrowserFetcher:
    """浏览器自动化抓取器"""
    
    def __init__(self, browser_type='playwright', headless=True, timeout=30, proxy=None):
        """
        Args:
            browser_type: 'playwright' 或 'selenium'
            headless: 是否无头模式
            timeout: 超时时间（秒）
            proxy: 代理地址，如 'http://127.0.0.1:7890'
        """
        self.browser_type = browser_type
        self.headless = headless
        self.timeout = timeout * 1000  # Playwright使用毫秒
        self.proxy = proxy
        self._browser = None
        self._context = None
        self._page = None
        
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
    
    async def start(self):
        """启动浏览器"""
        if self.browser_type == 'playwright':
            if not HAS_PLAYWRIGHT:
                raise RuntimeError(
                    "Playwright未安装。请运行：\n"
                    "  pip install playwright\n"
                    "  playwright install chromium"
                )
            self._playwright = await async_playwright().start()
            
            # 配置浏览器选项
            browser_args = {
                'headless': self.headless,
                'timeout': self.timeout,
            }
            
            # 添加代理
            if self.proxy:
                browser_args['proxy'] = {'server': self.proxy}
            
            self._browser = await self._playwright.chromium.launch(**browser_args)
            
            # 创建上下文（类似浏览器会话）
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
            
            if self.proxy:
                context_options['proxy'] = {'server': self.proxy}
            
            self._context = await self._browser.new_context(**context_options)
            self._page = await self._context.new_page()
            
            logger.info(f"✓ Playwright浏览器已启动 (headless={self.headless})")
            
        elif self.browser_type == 'selenium':
            if not HAS_SELENIUM:
                raise RuntimeError(
                    "Selenium未安装。请运行：\n"
                    "  pip install selenium\n"
                    "  并下载ChromeDriver: https://chromedriver.chromium.org/"
                )
            
            # Selenium是同步的，需要在异步环境中运行
            # 这里使用线程池执行
            loop = asyncio.get_event_loop()
            try:
                self._driver = await loop.run_in_executor(None, self._start_selenium)
                logger.info(f"✓ Selenium浏览器已启动 (headless={self.headless})")
            except Exception as e:
                error_msg = str(e)
                if "Could not reach host" in error_msg or "offline" in error_msg.lower():
                    raise RuntimeError(
                        f"浏览器初始化失败：网络连接问题\n"
                        f"webdriver-manager无法下载ChromeDriver。\n\n"
                        f"解决方案：\n"
                        f"  1. 检查网络连接\n"
                        f"  2. 配置代理（如果使用代理）\n"
                        f"  3. 手动安装ChromeDriver：\n"
                        f"     - 下载: https://chromedriver.chromium.org/\n"
                        f"     - 解压并放到PATH: sudo mv chromedriver /usr/local/bin/\n"
                        f"  4. 或使用Playwright: pip install playwright && playwright install chromium\n"
                        f"  5. 或暂时不使用浏览器自动化，尝试桌面版URL"
                    )
                else:
                    raise
        else:
            raise ValueError(f"不支持的浏览器类型: {self.browser_type}")
    
    def _start_selenium(self):
        """启动Selenium浏览器（同步方法）"""
        options = ChromeOptions()
        
        # 基础选项
        if self.headless:
            options.add_argument('--headless=new')  # 使用新的headless模式
        
        # WSL环境必需的选项（修复Chrome启动问题）
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-software-rasterizer')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-setuid-sandbox')
        options.add_argument('--disable-web-security')
        options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        
        # 内存和性能优化（WSL中很重要）
        options.add_argument('--memory-pressure-off')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-renderer-backgrounding')
        
        # 反检测选项
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        
        # 用户代理
        options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # 窗口大小
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--start-maximized')
        
        # 代理
        if self.proxy:
            options.add_argument(f'--proxy-server={self.proxy}')
        
        # 禁用不必要的功能
        prefs = {
            "profile.default_content_setting_values": {
                "notifications": 2,
                "geolocation": 2,
            },
            "profile.managed_default_content_settings": {
                "images": 2
            }
        }
        options.add_experimental_option("prefs", prefs)
        
        # 尝试使用webdriver-manager（如果可用）
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            try:
                # 尝试使用webdriver-manager下载ChromeDriver
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
                logger.info("✓ 使用webdriver-manager自动管理ChromeDriver")
            except Exception as e:
                # webdriver-manager失败（可能是网络问题），回退到系统ChromeDriver
                logger.warning(f"⚠ webdriver-manager失败: {e}")
                logger.warning("  尝试使用系统PATH中的ChromeDriver...")
                logger.warning("  如果失败，请手动安装ChromeDriver或配置代理")
                try:
                    driver = webdriver.Chrome(options=options)
                    logger.info("✓ 使用系统ChromeDriver")
                except Exception as e2:
                    # 诊断信息
                    chrome_installed = False
                    chromedriver_installed = False
                    chrome_version = "未知"
                    chromedriver_version = "未知"
                    
                    try:
                        import subprocess
                        result = subprocess.run(['google-chrome', '--version'], 
                                              capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            chrome_installed = True
                            chrome_version = result.stdout.strip()
                    except:
                        pass
                    
                    try:
                        result = subprocess.run(['chromedriver', '--version'], 
                                              capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            chromedriver_installed = True
                            chromedriver_version = result.stdout.strip()
                    except:
                        pass
                    
                    error_details = []
                    if not chrome_installed:
                        error_details.append("❌ Chrome浏览器未安装")
                    else:
                        error_details.append(f"✓ Chrome已安装: {chrome_version}")
                    
                    if not chromedriver_installed:
                        error_details.append("❌ ChromeDriver未安装或不在PATH中")
                    else:
                        error_details.append(f"✓ ChromeDriver已安装: {chromedriver_version}")
                    
                    if chrome_installed and chromedriver_installed:
                        error_details.append("⚠ 可能是版本不匹配，请确保ChromeDriver版本与Chrome版本匹配")
                    
                    raise RuntimeError(
                        f"无法启动Chrome浏览器。\n"
                        f"错误: {e2}\n\n"
                        f"诊断信息：\n" + "\n".join(error_details) + "\n\n"
                        f"解决方案：\n"
                        f"  1. 运行安装脚本: bash tools/browser/install_chromedriver.sh\n"
                        f"  2. 或手动安装ChromeDriver:\n"
                        f"     - 检查Chrome版本: google-chrome --version\n"
                        f"     - 下载对应版本: https://chromedriver.chromium.org/\n"
                        f"     - 解压并放到PATH: sudo mv chromedriver /usr/local/bin/\n"
                        f"  3. 或暂时不使用浏览器自动化，尝试桌面版URL（将m.ctrip.com改为www.ctrip.com）\n"
                        f"  4. 或使用Playwright: pip install playwright && playwright install chromium"
                    )
        except ImportError:
            # webdriver-manager未安装，使用系统ChromeDriver
            logger.info("⚠ webdriver-manager未安装，使用系统ChromeDriver")
            logger.info("  建议安装: pip install webdriver-manager")
            try:
                driver = webdriver.Chrome(options=options)
                logger.info("✓ 使用系统ChromeDriver")
            except Exception as e:
                raise RuntimeError(
                    f"无法启动Chrome浏览器。\n"
                    f"错误: {e}\n\n"
                    f"解决方案：\n"
                    f"  1. 安装Chrome浏览器: sudo apt install google-chrome-stable\n"
                    f"  2. 安装webdriver-manager: pip install webdriver-manager\n"
                    f"  3. 手动下载ChromeDriver并放到PATH中\n"
                    f"  4. 或使用Playwright: pip install playwright && playwright install chromium"
                )
        
        driver.set_page_load_timeout(self.timeout / 1000)  # Selenium使用秒
        return driver
    
    async def fetch(self, url: str, wait_for_selector: Optional[str] = None, wait_time: int = 3) -> str:
        """
        抓取页面内容
        
        Args:
            url: 要抓取的URL
            wait_for_selector: 等待特定选择器出现（可选）
            wait_time: 等待时间（秒），用于等待JavaScript渲染
        
        Returns:
            页面的HTML内容
        """
        if self.browser_type == 'playwright':
            return await self._fetch_playwright(url, wait_for_selector, wait_time)
        elif self.browser_type == 'selenium':
            return await self._fetch_selenium(url, wait_for_selector, wait_time)
    
    async def _fetch_playwright(self, url: str, wait_for_selector: Optional[str], wait_time: int) -> str:
        """使用Playwright抓取"""
        try:
            logger.info(f"🌐 使用Playwright访问: {url}")
            
            # 访问页面
            await self._page.goto(url, wait_until='networkidle', timeout=self.timeout)
            
            # 等待特定选择器（如果指定）
            if wait_for_selector:
                try:
                    await self._page.wait_for_selector(wait_for_selector, timeout=10000)
                    logger.debug(f"✓ 等待选择器出现: {wait_for_selector}")
                except PlaywrightTimeoutError:
                    logger.warning(f"⚠ 选择器未出现: {wait_for_selector}")
            
            # 额外等待JavaScript渲染
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            # 获取页面内容
            content = await self._page.content()
            logger.info(f"✓ 成功获取页面内容 ({len(content)} 字符)")
            
            return content
            
        except PlaywrightTimeoutError as e:
            logger.error(f"❌ Playwright超时: {e}")
            # 即使超时，也尝试获取当前内容
            try:
                return await self._page.content()
            except:
                raise RuntimeError(f"页面加载超时: {url}")
        except Exception as e:
            logger.error(f"❌ Playwright错误: {e}")
            raise
    
    async def _fetch_selenium(self, url: str, wait_for_selector: Optional[str], wait_time: int) -> str:
        """使用Selenium抓取（在异步环境中运行同步代码）"""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            try:
                logger.info(f"🌐 使用Selenium访问: {url}")
                
                # 访问页面
                self._driver.get(url)
                
                # 等待特定选择器（如果指定）
                if wait_for_selector:
                    try:
                        WebDriverWait(self._driver, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                        )
                        logger.debug(f"✓ 等待选择器出现: {wait_for_selector}")
                    except TimeoutException:
                        logger.warning(f"⚠ 选择器未出现: {wait_for_selector}")
                
                # 额外等待JavaScript渲染
                if wait_time > 0:
                    import time
                    time.sleep(wait_time)
                
                # 获取页面内容
                content = self._driver.page_source
                logger.info(f"✓ 成功获取页面内容 ({len(content)} 字符)")
                
                return content
                
            except TimeoutException as e:
                logger.error(f"❌ Selenium超时: {e}")
                # 即使超时，也尝试获取当前内容
                try:
                    return self._driver.page_source
                except:
                    raise RuntimeError(f"页面加载超时: {url}")
            except Exception as e:
                logger.error(f"❌ Selenium错误: {e}")
                raise
        
        return await loop.run_in_executor(None, _fetch)
    
    async def close(self):
        """关闭浏览器"""
        if self.browser_type == 'playwright':
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if hasattr(self, '_playwright'):
                await self._playwright.stop()
            logger.debug("✓ Playwright浏览器已关闭")
            
        elif self.browser_type == 'selenium':
            if hasattr(self, '_driver') and self._driver:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._driver.quit)
                logger.debug("✓ Selenium浏览器已关闭")


# 便捷函数
async def fetch_with_browser(url: str, browser_type='playwright', headless=True, 
                            timeout=30, proxy=None, wait_for_selector=None, wait_time=3) -> str:
    """
    使用浏览器自动化抓取页面
    
    Args:
        url: 要抓取的URL
        browser_type: 'playwright' 或 'selenium'
        headless: 是否无头模式
        timeout: 超时时间（秒）
        proxy: 代理地址
        wait_for_selector: 等待特定选择器出现
        wait_time: 等待JavaScript渲染的时间（秒）
    
    Returns:
        页面的HTML内容
    """
    async with BrowserFetcher(browser_type, headless, timeout, proxy) as fetcher:
        return await fetcher.fetch(url, wait_for_selector, wait_time)


# 测试代码
if __name__ == '__main__':
    import sys
    
    logging.basicConfig(level=logging.INFO)
    
    async def test():
        url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.ctrip.com'
        
        try:
            # 尝试使用Playwright
            if HAS_PLAYWRIGHT:
                print("使用Playwright...")
                content = await fetch_with_browser(url, browser_type='playwright', wait_time=3)
                print(f"✓ 成功获取 {len(content)} 字符")
            elif HAS_SELENIUM:
                print("使用Selenium...")
                content = await fetch_with_browser(url, browser_type='selenium', wait_time=3)
                print(f"✓ 成功获取 {len(content)} 字符")
            else:
                print("❌ 未安装浏览器自动化工具")
                print("安装Playwright: pip install playwright && playwright install chromium")
                print("或安装Selenium: pip install selenium")
        except Exception as e:
            print(f"❌ 错误: {e}")
    
    asyncio.run(test())


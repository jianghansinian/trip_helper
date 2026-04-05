#!/usr/bin/env python3
"""
浏览器环境检查脚本
检查Chrome和ChromeDriver是否正确安装
"""

import subprocess
import sys
from pathlib import Path

def check_command(cmd, name):
    """检查命令是否可用"""
    try:
        result = subprocess.run([cmd, '--version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version = result.stdout.strip()
            print(f"✅ {name}: {version}")
            return True, version
        else:
            print(f"❌ {name}: 未找到")
            return False, None
    except FileNotFoundError:
        print(f"❌ {name}: 未安装")
        return False, None
    except Exception as e:
        print(f"⚠️  {name}: 检查失败 - {e}")
        return False, None

def main():
    print("🔍 浏览器环境检查")
    print("=" * 50)
    
    # 检查Chrome
    chrome_ok, chrome_version = check_command('google-chrome', 'Chrome浏览器')
    
    # 检查ChromeDriver
    chromedriver_ok, chromedriver_version = check_command('chromedriver', 'ChromeDriver')
    
    # 检查Selenium
    try:
        import selenium
        print(f"✅ Selenium: {selenium.__version__}")
        selenium_ok = True
    except ImportError:
        print("❌ Selenium: 未安装")
        print("   安装: pip install selenium")
        selenium_ok = False
    
    # 检查webdriver-manager
    try:
        import webdriver_manager
        print(f"✅ webdriver-manager: 已安装")
        wdm_ok = True
    except ImportError:
        print("⚠️  webdriver-manager: 未安装（可选）")
        print("   安装: pip install webdriver-manager")
        wdm_ok = False
    
    print("\n" + "=" * 50)
    print("📋 诊断结果")
    print("=" * 50)
    
    if not chrome_ok:
        print("\n❌ Chrome浏览器未安装")
        print("   安装方法:")
        print("   wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb")
        print("   sudo apt install ./google-chrome-stable_current_amd64.deb")
    
    if not chromedriver_ok:
        print("\n❌ ChromeDriver未安装或不在PATH中")
        print("   解决方案:")
        print("   1. 运行安装脚本: bash tools/browser/install_chromedriver.sh")
        print("   2. 或手动下载: https://chromedriver.chromium.org/")
    
    if chrome_ok and chromedriver_ok:
        # 检查版本匹配
        try:
            chrome_major = chrome_version.split()[2].split('.')[0]
            print(f"\n📌 Chrome主版本: {chrome_major}")
            print("   请确保ChromeDriver版本与Chrome版本匹配")
        except:
            pass
    
    if not selenium_ok:
        print("\n❌ Selenium未安装")
        print("   安装: pip install selenium")
    
    print("\n" + "=" * 50)
    
    # 总结
    if chrome_ok and chromedriver_ok and selenium_ok:
        print("✅ 环境检查通过！可以使用Selenium了。")
        return 0
    else:
        print("⚠️  环境未完全配置，请按照上述提示安装缺失的组件。")
        return 1

if __name__ == '__main__':
    sys.exit(main())


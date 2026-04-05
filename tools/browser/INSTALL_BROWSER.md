# 浏览器自动化安装指南（WSL环境）

## 问题：Playwright在WSL中安装失败

在WSL环境中，Playwright可能会遇到兼容性问题。以下是几种解决方案：

## 方案1：修复Playwright安装（推荐）

### 步骤1：安装Node.js

```bash
# 使用nvm安装Node.js（推荐）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm use --lts

# 或使用apt安装
sudo apt update
sudo apt install -y nodejs npm
```

### 步骤2：重新安装Playwright

```bash
# 卸载旧版本
pip uninstall playwright -y

# 重新安装
pip install playwright

# 安装浏览器
playwright install chromium
```

### 步骤3：如果仍然失败，尝试使用系统Playwright

```bash
# 使用系统级安装
python3 -m playwright install chromium
```

## 方案2：使用Selenium（更稳定）

Selenium在WSL中通常更稳定：

### 步骤1：安装Selenium

```bash
pip install selenium
```

### 步骤2：安装ChromeDriver

**方法A：使用webdriver-manager（推荐，自动管理）**

```bash
pip install webdriver-manager
```

然后在代码中会自动下载和管理ChromeDriver。

**方法B：手动安装**

```bash
# 1. 检查Chrome版本
google-chrome --version

# 2. 下载对应版本的ChromeDriver
# 访问：https://chromedriver.chromium.org/downloads
# 或使用脚本：
CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+')
CHROMEDRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION%.*}")
wget "https://chromedriver.storage.googleapis.com/${CHROMEDRIVER_VERSION}/chromedriver_linux64.zip"
unzip chromedriver_linux64.zip
sudo mv chromedriver /usr/local/bin/
sudo chmod +x /usr/local/bin/chromedriver
```

### 步骤3：安装Chrome浏览器（如果还没有）

```bash
# Ubuntu/Debian
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb
```

## 方案3：修改browser_fetcher.py使用webdriver-manager

我已经更新了代码，如果安装了`webdriver-manager`，会自动使用它来管理ChromeDriver。

## 快速测试

### 测试Selenium

```bash
# 安装依赖
pip install selenium webdriver-manager

# 测试
python3 -c "
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)
driver.get('https://www.example.com')
print('✓ Selenium工作正常')
driver.quit()
"
```

## 推荐配置（WSL环境）

在 `tools/url-translate/config.yaml` 中使用 Selenium：

```yaml
use_browser: true
browser_type: selenium  # 在WSL中使用selenium更稳定
browser_headless: true
browser_wait_time: 3
```

## 故障排除

### 问题1：ChromeDriver版本不匹配

**解决**：使用`webdriver-manager`自动管理版本

```bash
pip install webdriver-manager
```

### 问题2：Chrome浏览器未安装

**解决**：
```bash
# 检查是否安装
google-chrome --version

# 如果未安装，安装Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb
```

### 问题3：权限问题

**解决**：
```bash
# 确保ChromeDriver有执行权限
chmod +x /usr/local/bin/chromedriver

# 或使用webdriver-manager（推荐）
```

## 最简单的解决方案

如果Playwright安装有问题，直接使用Selenium + webdriver-manager：

```bash
# 1. 安装依赖
pip install selenium webdriver-manager

# 2. 在config.yaml中配置
# browser_type: selenium

# 3. 运行（在仓库根目录）
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml --browser --browser-type selenium
```

这样就不需要手动管理ChromeDriver了。


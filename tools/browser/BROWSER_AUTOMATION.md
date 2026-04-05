# 浏览器自动化使用指南

## 概述

对于SPA（单页应用）页面（如携程移动端、马蜂窝等），内容通过JavaScript动态加载，普通的HTTP请求无法获取完整内容。需要使用浏览器自动化工具来执行JavaScript并获取渲染后的页面。

## 安装

### 方案1：Playwright（推荐）

```bash
# 安装Playwright
pip install playwright

# 安装Chromium浏览器
playwright install chromium
```

**优点：**
- 更现代、更快
- 更好的异步支持
- 自动下载浏览器，无需额外配置

### 方案2：Selenium

```bash
# 安装Selenium
pip install selenium

# 下载ChromeDriver
# 访问 https://chromedriver.chromium.org/
# 下载对应Chrome版本的驱动
# 将chromedriver放到PATH中
```

**优点：**
- 更成熟，社区支持多
- 支持更多浏览器

## 使用方法

### 方法1：在config.yaml中配置

```yaml
urls_file: urls.txt
output_dir: translated_articles
source_lang: zh
target_lang: en
rewrite_mode: true
backend: deepseek
deepseek_api_key: YOUR_KEY

# 浏览器自动化配置
use_browser: true              # 启用浏览器自动化
browser_type: playwright        # 使用playwright（或selenium）
browser_headless: true         # 无头模式（不显示浏览器窗口）
browser_wait_time: 3           # 等待JavaScript渲染的时间（秒）

# 可选：代理配置
proxy: http://127.0.0.1:7890
```

然后运行（在仓库根目录）：
```bash
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml
```

### 方法2：命令行参数

```bash
# 使用Playwright（默认）
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml --browser

# 使用Selenium
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml --browser --browser-type selenium

# 非无头模式（显示浏览器窗口，用于调试）
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml --browser --browser-headless

# 增加等待时间（对于加载慢的页面）
python3 tools/url-translate/translate.py --config tools/url-translate/config.yaml --browser --browser-wait 5
```

## 配置说明

### use_browser
- **类型**: boolean
- **默认**: false
- **说明**: 是否使用浏览器自动化。启用后，所有URL都会通过浏览器抓取。

### browser_type
- **类型**: string
- **可选值**: `playwright` 或 `selenium`
- **默认**: `playwright`
- **说明**: 选择使用的浏览器自动化工具。

### browser_headless
- **类型**: boolean
- **默认**: true
- **说明**: 是否使用无头模式。设置为false时，会显示浏览器窗口（用于调试）。

### browser_wait_time
- **类型**: integer
- **默认**: 3
- **单位**: 秒
- **说明**: 等待JavaScript渲染的时间。对于加载慢的页面，可以增加这个值。

## 使用场景

### 1. 携程移动端页面

```yaml
use_browser: true
browser_type: playwright
browser_wait_time: 5  # 携程页面加载较慢，增加等待时间
```

### 2. 马蜂窝页面（需要代理）

```yaml
use_browser: true
browser_type: playwright
proxy: http://127.0.0.1:7890
browser_wait_time: 3
```

### 3. 调试模式（查看浏览器）

```yaml
use_browser: true
browser_headless: false  # 显示浏览器窗口
```

## 性能考虑

- **浏览器自动化比普通HTTP请求慢**：每个页面需要启动浏览器、加载页面、等待渲染
- **建议**：只对SPA页面使用浏览器自动化，普通页面使用普通HTTP请求
- **并发限制**：浏览器自动化时，建议降低`max_concurrency`（如设置为1-2）

## 故障排除

### 1. Playwright未安装

**错误**: `Playwright未安装`

**解决**:
```bash
pip install playwright
playwright install chromium
```

### 2. Selenium ChromeDriver未找到

**错误**: `ChromeDriver not found`

**解决**:
1. 下载对应Chrome版本的ChromeDriver
2. 将chromedriver放到PATH中，或使用`webdriver-manager`:
   ```bash
   pip install webdriver-manager
   ```

### 3. 页面加载超时

**错误**: `页面加载超时`

**解决**:
- 增加`browser_wait_time`
- 增加`timeout`配置
- 检查网络连接和代理设置

### 4. 内存不足

**错误**: 浏览器占用内存过多

**解决**:
- 降低`max_concurrency`
- 使用无头模式（`browser_headless: true`）
- 考虑使用更轻量的浏览器（如Firefox）

## 示例

### 完整配置示例

```yaml
# config.yaml
urls_file: urls.txt
output_dir: translated_articles
source_lang: zh
target_lang: en
rewrite_mode: true
backend: deepseek
deepseek_api_key: sk-xxx

# 浏览器自动化
use_browser: true
browser_type: playwright
browser_headless: true
browser_wait_time: 3

# 代理（如果需要）
proxy: http://127.0.0.1:7890

# 性能配置
max_concurrency: 2  # 浏览器自动化时降低并发数
timeout: 60         # 增加超时时间
```

## 注意事项

1. **首次使用Playwright需要下载浏览器**（约200MB），需要一些时间
2. **浏览器自动化会消耗更多资源**（CPU、内存）
3. **建议在服务器环境使用无头模式**（`browser_headless: true`）
4. **对于大量URL，考虑分批处理**，避免内存溢出


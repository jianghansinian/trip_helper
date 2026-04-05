#!/bin/bash
# ChromeDriver安装脚本（适用于WSL/Linux）

echo "🔧 ChromeDriver安装脚本"
echo "========================"

# 检查Chrome是否安装
if ! command -v google-chrome &> /dev/null; then
    echo "❌ Chrome浏览器未安装"
    echo "正在安装Chrome..."
    
    # 下载Chrome
    wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    
    if [ $? -eq 0 ]; then
        sudo apt install -y /tmp/chrome.deb
        echo "✓ Chrome安装完成"
    else
        echo "❌ Chrome下载失败，请手动安装"
        echo "访问: https://www.google.com/chrome/"
        exit 1
    fi
else
    CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+')
    echo "✓ 检测到Chrome版本: $CHROME_VERSION"
fi

# 获取Chrome主版本号
CHROME_MAJOR=$(google-chrome --version | grep -oP '\d+' | head -1)
echo "📦 Chrome主版本: $CHROME_MAJOR"

# 下载对应版本的ChromeDriver
echo "📥 正在下载ChromeDriver..."

# 尝试从Chrome for Testing获取最新版本
CHROMEDRIVER_URL="https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/${CHROME_MAJOR}.0.0.0/linux64/chromedriver-linux64.zip"

# 如果失败，尝试传统方法
if ! wget -q -O /tmp/chromedriver.zip "$CHROMEDRIVER_URL" 2>/dev/null; then
    echo "⚠ 使用备用下载源..."
    # 使用chromedriver.storage.googleapis.com（旧版API）
    CHROMEDRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_MAJOR}")
    if [ -z "$CHROMEDRIVER_VERSION" ]; then
        echo "❌ 无法获取ChromeDriver版本"
        echo "请手动下载: https://chromedriver.chromium.org/downloads"
        exit 1
    fi
    CHROMEDRIVER_URL="https://chromedriver.storage.googleapis.com/${CHROMEDRIVER_VERSION}/chromedriver_linux64.zip"
    wget -q -O /tmp/chromedriver.zip "$CHROMEDRIVER_URL"
fi

if [ $? -eq 0 ]; then
    echo "✓ ChromeDriver下载完成"
    
    # 解压
    unzip -q -o /tmp/chromedriver.zip -d /tmp/
    
    # 移动到系统PATH
    if [ -f "/tmp/chromedriver-linux64/chromedriver" ]; then
        sudo mv /tmp/chromedriver-linux64/chromedriver /usr/local/bin/
    elif [ -f "/tmp/chromedriver" ]; then
        sudo mv /tmp/chromedriver /usr/local/bin/
    else
        echo "❌ 解压后未找到chromedriver文件"
        exit 1
    fi
    
    # 设置执行权限
    sudo chmod +x /usr/local/bin/chromedriver
    
    # 验证安装
    if chromedriver --version &> /dev/null; then
        echo "✅ ChromeDriver安装成功！"
        chromedriver --version
    else
        echo "❌ ChromeDriver安装失败"
        exit 1
    fi
else
    echo "❌ ChromeDriver下载失败"
    echo "可能的原因："
    echo "  1. 网络连接问题"
    echo "  2. 需要使用代理"
    echo ""
    echo "手动安装步骤："
    echo "  1. 访问: https://chromedriver.chromium.org/downloads"
    echo "  2. 下载对应Chrome版本的ChromeDriver"
    echo "  3. 解压: unzip chromedriver_linux64.zip"
    echo "  4. 移动: sudo mv chromedriver /usr/local/bin/"
    echo "  5. 设置权限: sudo chmod +x /usr/local/bin/chromedriver"
    exit 1
fi

echo ""
echo "🎉 安装完成！现在可以使用Selenium了。"


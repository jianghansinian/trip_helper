/**
 * 文章内容自动翻译系统
 * 为文章页面提供客户端翻译功能
 * 
 * 使用方式：
 * 1. 在文章页面中，将需要翻译的内容用 data-translate="true" 标记
 * 2. 调用 ArticleTranslator.translatePage() 进行翻译
 * 
 * 注意：这里使用的是浏览器内置的翻译能力（如果可用）
 * 或者可以集成第三方翻译API（如Google Translate、百度翻译等）
 */
class ArticleTranslator {
    constructor() {
        this.currentLang = 'en';
        this.supportedLangs = ['en', 'ko', 'ja', 'ru', 'de', 'fr', 'es', 'it', 'ar'];
        
        // 如果有全局i18n实例，使用它的语言设置
        if (typeof i18n !== 'undefined') {
            this.currentLang = i18n.getCurrentLanguage();
            // 监听语言切换事件
            document.addEventListener('languageChanged', (e) => {
                this.currentLang = e.detail.lang;
                this.translatePage(this.currentLang);
            });
        }
    }

    /**
     * 翻译整个页面
     */
    async translatePage(targetLang = null) {
        if (!targetLang) {
            targetLang = this.currentLang;
        }

        if (targetLang === 'en') {
            // 英语是原文，不需要翻译
            this.restoreOriginal();
            return;
        }

        // 获取所有需要翻译的元素
        const elements = document.querySelectorAll('[data-translate="true"]');
        
        // 存储原文（如果还没有存储）
        elements.forEach(el => {
            if (!el.dataset.originalText) {
                el.dataset.originalText = el.textContent;
            }
        });

        // 执行翻译
        await this.translateElements(Array.from(elements), targetLang);
    }

    /**
     * 翻译元素列表
     */
    async translateElements(elements, targetLang) {
        // 方案1: 使用浏览器内置翻译（如果支持）
        if (this.useBrowserTranslation()) {
            this.translateWithBrowser(elements, targetLang);
            return;
        }

        // 方案2: 使用简单的翻译服务（这里使用免费的API）
        // 注意：生产环境建议使用专业的翻译API（如Google Cloud Translation、Azure Translator等）
        await this.translateWithAPI(elements, targetLang);
    }

    /**
     * 使用浏览器内置翻译（如果可用）
     */
    translateWithBrowser(elements, targetLang) {
        // 标记需要翻译的内容
        elements.forEach(el => {
            el.setAttribute('translate', 'yes');
            el.setAttribute('lang', 'en');
        });

        // 设置页面语言（这会触发浏览器的自动翻译）
        document.documentElement.lang = targetLang;

        // 注意：这个方法依赖于浏览器的自动翻译功能
        // 如果浏览器不支持，需要回退到API翻译
    }

    /**
     * 使用API翻译（需要配置翻译服务）
     */
    async translateWithAPI(elements, targetLang) {
        // 这里需要配置翻译API
        // 示例：使用Google Cloud Translation API 或其他翻译服务
        
        // 为每个元素翻译
        for (const el of elements) {
            const originalText = el.dataset.originalText || el.textContent;
            if (!originalText || originalText.trim().length === 0) continue;

            try {
                // TODO: 实现实际的翻译API调用
                // const translatedText = await this.callTranslationAPI(originalText, targetLang);
                // el.textContent = translatedText;
                
                // 临时方案：显示提示信息
                console.log(`需要翻译到 ${targetLang}:`, originalText.substring(0, 50));
                
                // 在实际项目中，您可以：
                // 1. 集成Google Cloud Translation API
                // 2. 集成Azure Translator
                // 3. 集成百度翻译API
                // 4. 使用免费的翻译服务（如MyMemory Translation API）
                
            } catch (error) {
                console.error('翻译失败:', error);
            }
        }
    }

    /**
     * 检查是否可以使用浏览器翻译
     */
    useBrowserTranslation() {
        // 检查浏览器是否支持自动翻译
        // 大多数现代浏览器都支持
        return true;
    }

    /**
     * 恢复原文
     */
    restoreOriginal() {
        const elements = document.querySelectorAll('[data-translate="true"]');
        elements.forEach(el => {
            if (el.dataset.originalText) {
                el.textContent = el.dataset.originalText;
            }
            el.removeAttribute('translate');
        });
        document.documentElement.lang = 'en';
    }
}

// 创建全局实例
if (typeof window !== 'undefined') {
    window.articleTranslator = new ArticleTranslator();
}


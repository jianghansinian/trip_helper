/**
 * 文章内容动态翻译系统
 * 使用翻译API实时翻译文章标题和正文
 * 
 * 使用方式：
 * 1. 在文章HTML中，为需要翻译的内容添加 data-translate="true" 属性
 * 2. 调用 ArticleContentI18n.translatePage(lang) 进行翻译
 */

class ArticleContentI18n {
    constructor() {
        this.currentLang = 'en';
        this.supportedLangs = ['en', 'ko', 'ja', 'ru', 'de', 'fr', 'es', 'it', 'ar'];
        this.translationCache = new Map(); // 缓存翻译结果
        this.translator = 'api';
        this.isTranslating = false;
        this.init();
    }

    /**
     * 初始化
     */
    init() {
        // 如果有全局i18n实例，使用它的语言设置
        if (typeof i18n !== 'undefined') {
            this.currentLang = i18n.getCurrentLanguage();
            // 监听语言切换事件
            document.addEventListener('languageChanged', (e) => {
                this.currentLang = e.detail.lang;
                if (this.currentLang !== 'en') {
                    this.translatePage(this.currentLang);
                } else {
                    this.restoreOriginal();
                }
            });
        }

        // 对于站内语言切换，强制使用 API 翻译，避免浏览器自动翻译不可控
    }

    /**
     * 翻译整个页面
     */
    async translatePage(targetLang = null) {
        if (!targetLang) {
            targetLang = this.currentLang;
        }

        if (this.isTranslating) {
            return;
        }

        if (targetLang === 'en') {
            // 英语是原文，不需要翻译
            this.restoreOriginal();
            return;
        }

        console.log(`🌐 开始翻译到 ${targetLang}...`);

        this.isTranslating = true;
        
        try {
            // 获取所有需要翻译的元素（先用显式标记，再回退到自动识别）
            let elements = Array.from(document.querySelectorAll('[data-translate="true"]'));
            if (elements.length === 0) {
                elements = this.getAutoTranslatableElements();
            }

            if (elements.length === 0) {
                console.warn('⚠️  没有找到可翻译内容');
                return;
            }

            // 存储原文（如果还没有存储）
            elements.forEach(el => {
                if (!el.dataset.originalHtml) {
                    el.dataset.originalHtml = el.innerHTML;
                }
                if (!el.dataset.originalText) {
                    el.dataset.originalText = el.textContent.trim();
                }
                if (!el.getAttribute('data-translate')) {
                    el.setAttribute('data-translate', 'true');
                }
            });

            // 执行翻译
            await this.translateWithAPI(elements, targetLang);
        } finally {
            this.isTranslating = false;
        }
    }

    /**
     * 自动识别文章页中可翻译元素
     */
    getAutoTranslatableElements() {
        const selectors = [
            'header .header-top',
            '.nav-menu a',
            '.breadcrumb a',
            '.breadcrumb span',
            '.article-title',
            '.article-subtitle',
            '.article-category',
            '.article-content h2',
            '.article-content h3',
            '.article-content h4',
            '.article-content p',
            '.article-content li',
            '.article-content th',
            '.article-content td',
            '.article h1',
            '.article h2',
            '.article h3',
            '.article h4',
            '.article p',
            '.article li',
            '.article th',
            '.article td',
            '.widget h3',
            '.widget-list a',
            'footer p',
            'footer a'
        ];

        const candidates = new Set();
        selectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => candidates.add(el));
        });

        return Array.from(candidates).filter(el => {
            const text = el.textContent ? el.textContent.trim() : '';
            return text.length > 1;
        });
    }

    /**
     * 使用API翻译
     */
    async translateWithAPI(elements, targetLang) {
        const langCode = this.getLangCode(targetLang);
        const elementsArray = Array.from(elements);
        
        // 批量翻译（每次翻译多个元素）
        const batchSize = 5;
        for (let i = 0; i < elementsArray.length; i += batchSize) {
            const batch = elementsArray.slice(i, i + batchSize);
            await Promise.all(batch.map(el => this.translateElement(el, targetLang, langCode)));
        }

        console.log(`✅ 已完成翻译到 ${targetLang}`);
    }

    /**
     * 翻译单个元素
     */
    async translateElement(element, targetLang, langCode) {
        const originalText = element.dataset.originalText || element.textContent.trim();
        
        if (!originalText || originalText.length === 0) {
            return;
        }

        // 检查缓存
        const cacheKey = `${originalText}_${targetLang}`;
        if (this.translationCache.has(cacheKey)) {
            element.textContent = this.translationCache.get(cacheKey);
            return;
        }

        try {
            // 使用免费的MyMemory翻译API（有使用限制）
            // 生产环境建议使用Google Cloud Translation API或Azure Translator
            const translatedText = await this.callTranslationAPI(originalText, langCode);
            
            if (translatedText) {
                element.textContent = translatedText;
                // 缓存翻译结果
                this.translationCache.set(cacheKey, translatedText);
            }
        } catch (error) {
            console.error('翻译失败:', error);
            // 翻译失败时保持原文
        }
    }

    /**
     * 调用翻译API
     */
    async callTranslationAPI(text, targetLang) {
        // 方案1: 使用MyMemory翻译API（免费，但有使用限制）
        try {
            const response = await fetch(
                `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}&langpair=en|${targetLang}`
            );
            const data = await response.json();
            
            if (data.responseStatus === 200 && data.responseData) {
                return data.responseData.translatedText;
            }
        } catch (error) {
            console.warn('MyMemory API失败，尝试其他方法:', error);
        }

        // 方案2: 使用Google Translate（需要配置）
        // 这里可以集成Google Cloud Translation API
        
        // 方案3: 使用Azure Translator（需要配置）
        // 这里可以集成Azure Translator API

        // 如果所有API都失败，返回原文
        return text;
    }

    /**
     * 获取语言代码
     */
    getLangCode(lang) {
        const langMap = {
            'ko': 'ko',
            'ja': 'ja',
            'ru': 'ru',
            'de': 'de',
            'fr': 'fr',
            'es': 'es',
            'it': 'it',
            'ar': 'ar'
        };
        return langMap[lang] || lang;
    }

    /**
     * 恢复原文
     */
    restoreOriginal() {
        const elements = document.querySelectorAll('[data-translate="true"]');
        elements.forEach(el => {
            if (el.dataset.originalHtml) {
                el.innerHTML = el.dataset.originalHtml;
            } else if (el.dataset.originalText) {
                el.textContent = el.dataset.originalText;
            }
        });
        document.documentElement.lang = 'en';
        console.log('✅ 已恢复原文');
    }
}

// 创建全局实例
if (typeof window !== 'undefined') {
    window.articleContentI18n = new ArticleContentI18n();
}


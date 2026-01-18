/**
 * 多语言国际化系统
 * 支持语言切换和自动翻译
 */
class I18n {
    constructor() {
        this.currentLang = 'en'; // 默认英语
        this.translations = {};
        this.loadSavedLanguage();
        this.init();
    }

    /**
     * 初始化系统
     */
    init() {
        // 从localStorage加载保存的语言
        const savedLang = localStorage.getItem('siteLanguage');
        if (savedLang && this.isValidLanguage(savedLang)) {
            this.currentLang = savedLang;
        }

        // 检测浏览器语言
        if (!savedLang) {
            const browserLang = navigator.language || navigator.userLanguage;
            const langCode = browserLang.split('-')[0];
            if (this.isValidLanguage(langCode)) {
                this.currentLang = langCode;
            }
        }

        // 设置HTML lang属性
        document.documentElement.lang = this.currentLang;

        // 应用RTL方向（阿拉伯语）
        if (this.currentLang === 'ar') {
            document.documentElement.dir = 'rtl';
        } else {
            document.documentElement.dir = 'ltr';
        }
    }

    /**
     * 检查语言是否有效
     */
    isValidLanguage(lang) {
        const validLangs = ['en', 'ko', 'ja', 'ru', 'de', 'fr', 'es', 'it', 'ar'];
        return validLangs.includes(lang);
    }

    /**
     * 加载保存的语言
     */
    loadSavedLanguage() {
        const saved = localStorage.getItem('siteLanguage');
        if (saved && this.isValidLanguage(saved)) {
            this.currentLang = saved;
        }
    }

    /**
     * 设置翻译数据
     */
    setTranslations(translations) {
        this.translations = translations;
    }

    /**
     * 获取翻译文本
     */
    t(key, defaultValue = '') {
        if (this.translations[this.currentLang] && this.translations[this.currentLang][key]) {
            return this.translations[this.currentLang][key];
        }
        // 如果当前语言没有翻译，尝试英语
        if (this.currentLang !== 'en' && this.translations['en'] && this.translations['en'][key]) {
            return this.translations['en'][key];
        }
        // 最后返回默认值或key本身
        return defaultValue || key;
    }

    /**
     * 切换语言
     */
    setLanguage(lang) {
        if (!this.isValidLanguage(lang)) {
            console.warn(`Invalid language code: ${lang}`);
            return;
        }

        this.currentLang = lang;
        localStorage.setItem('siteLanguage', lang);
        
        // 更新HTML lang属性
        document.documentElement.lang = lang;
        
        // 更新RTL方向
        if (lang === 'ar') {
            document.documentElement.dir = 'rtl';
        } else {
            document.documentElement.dir = 'ltr';
        }

        // 重新应用翻译
        this.applyTranslations();

        // 触发语言切换事件
        document.dispatchEvent(new CustomEvent('languageChanged', { detail: { lang } }));
    }

    /**
     * 应用翻译到页面
     */
    applyTranslations() {
        // 翻译所有带有data-i18n属性的元素
        document.querySelectorAll('[data-i18n]').forEach(element => {
            const key = element.getAttribute('data-i18n');
            const translation = this.t(key);
            
            if (translation) {
                // 检查是否是input的placeholder
                if (element.tagName === 'INPUT' && element.type !== 'submit' && element.type !== 'button') {
                    element.placeholder = translation;
                } else if (element.tagName === 'TEXTAREA') {
                    // textarea的placeholder
                    if (element.getAttribute('placeholder')) {
                        element.placeholder = translation;
                    } else {
                        element.textContent = translation;
                    }
                } else if (element.getAttribute('data-i18n-html') === 'true') {
                    // 如果使用HTML翻译
                    element.innerHTML = translation;
                } else {
                    element.textContent = translation;
                }
            }
        });

        // 翻译title和meta description
        const titleKey = document.querySelector('title')?.getAttribute('data-i18n');
        if (titleKey) {
            document.title = this.t(titleKey) || document.title;
        }

        const metaDesc = document.querySelector('meta[name="description"]');
        const descKey = metaDesc?.getAttribute('data-i18n');
        if (descKey) {
            metaDesc.setAttribute('content', this.t(descKey) || metaDesc.getAttribute('content'));
        }
    }

    /**
     * 获取当前语言
     */
    getCurrentLanguage() {
        return this.currentLang;
    }
}

// 创建全局i18n实例
const i18n = new I18n();

// 页面加载完成后应用翻译
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (window.translations) {
            i18n.setTranslations(window.translations);
            i18n.applyTranslations();
        }
    });
} else {
    if (window.translations) {
        i18n.setTranslations(window.translations);
        i18n.applyTranslations();
    }
}


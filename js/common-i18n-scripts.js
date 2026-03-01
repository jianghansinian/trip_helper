/**
 * 通用i18n脚本代码
 * 在所有页面中使用相同的语言切换功能
 */

// 语言选择器功能
function toggleLanguageDropdown() {
    const dropdown = document.getElementById('language-dropdown');
    if (dropdown) dropdown.classList.toggle('show');
}

// 点击外部关闭下拉菜单
document.addEventListener('click', function(event) {
    const selector = document.querySelector('.language-selector');
    const dropdown = document.getElementById('language-dropdown');
    if (dropdown && selector && !selector.contains(event.target)) {
        dropdown.classList.remove('show');
    }
});

// 切换语言
function changeLanguage(lang) {
    if (typeof i18n !== 'undefined') {
        i18n.setLanguage(lang);
    }
    
    // 更新当前语言显示
    const langNames = {
        'en': 'English',
        'ko': '한국어',
        'ja': '日本語',
        'ru': 'Русский',
        'de': 'Deutsch',
        'fr': 'Français',
        'es': 'Español',
        'it': 'Italiano',
        'ar': 'العربية'
    };
    const langNameEl = document.getElementById('current-lang-name');
    if (langNameEl) {
        langNameEl.textContent = langNames[lang] || 'English';
    }
    
    // 更新活动选项
    document.querySelectorAll('.language-option').forEach(opt => {
        opt.classList.remove('active');
        if (opt.getAttribute('onclick') === `changeLanguage('${lang}')`) {
            opt.classList.add('active');
        }
    });
    
    // 关闭下拉菜单
    const dropdown = document.getElementById('language-dropdown');
    if (dropdown) dropdown.classList.remove('show');

    // 翻译文章内容（旧版）
    if (typeof articleTranslator !== 'undefined') {
        articleTranslator.translatePage(lang);
    }

    // 翻译文章内容（新版动态翻译）
    if (typeof articleContentI18n !== 'undefined') {
        articleContentI18n.translatePage(lang);
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    if (typeof i18n !== 'undefined') {
        const langNames = {
            'en': 'English',
            'ko': '한국어',
            'ja': '日本語',
            'ru': 'Русский',
            'de': 'Deutsch',
            'fr': 'Français',
            'es': 'Español',
            'it': 'Italiano',
            'ar': 'العربية'
        };
        const currentLang = i18n.getCurrentLanguage();
        const langNameEl = document.getElementById('current-lang-name');
        if (langNameEl) {
            langNameEl.textContent = langNames[currentLang] || 'English';
        }

        // 设置活动语言选项
        document.querySelectorAll('.language-option').forEach(opt => {
            opt.classList.remove('active');
            if (opt.getAttribute('onclick') === `changeLanguage('${currentLang}')`) {
                opt.classList.add('active');
            }
        });

        if (currentLang !== 'en') {
            if (typeof articleTranslator !== 'undefined') {
                articleTranslator.translatePage(currentLang);
            }
            if (typeof articleContentI18n !== 'undefined') {
                articleContentI18n.translatePage(currentLang);
            }
        }
    }
});


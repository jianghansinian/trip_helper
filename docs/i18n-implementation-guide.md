# 多语言实现指南

## 概述

本站采用**混合翻译方案**，既保证了UI元素的统一翻译，又提供了文章内容的灵活翻译方式。

## 翻译方案架构

### 1. UI元素翻译（translations.js）
- **适用范围**：所有固定UI文本
  - 导航菜单
  - 按钮文字
  - 页脚链接
  - 表单标签
  - 错误提示
  - 等等

- **维护方式**：手动维护在 `js/translations.js`
- **优点**：
  - 翻译质量可控
  - 性能好（无需API调用）
  - 适合固定文本

### 2. 文章内容翻译（三种方案）

#### 方案A：服务器端预翻译（推荐用于热门文章）
- 在部署前，使用翻译API为每篇文章生成多语言版本
- 生成的文件结构：
  ```
  blog/
    article.html (英文原版)
    article.ko.html (韩语版)
    article.ja.html (日语版)
    ...
  ```
- **优点**：速度快、SEO友好
- **缺点**：存储空间大、维护成本高

#### 方案B：客户端动态翻译（推荐用于一般文章）
- 文章内容标记 `data-translate="true"`
- 用户切换语言时，使用翻译API实时翻译
- **优点**：维护简单、存储空间小
- **缺点**：需要翻译API、首次加载稍慢

#### 方案C：混合方案（最佳实践）
- 热门/重要文章：使用方案A（预翻译）
- 一般文章：使用方案B（动态翻译）
- 用户可以选择"翻译质量优先"或"速度优先"

## 实施步骤

### 阶段1：完成UI翻译（当前阶段）
- [x] 创建 translations.js
- [x] 创建 i18n.js 翻译引擎
- [x] 主页面多语言支持
- [ ] blog/index.html 多语言支持
- [ ] guides/index.html 多语言支持
- [ ] about.html, contact.html, privacy.html 多语言支持

### 阶段2：文章内容翻译工具
- [ ] 集成翻译API（Google/Azure/百度）
- [ ] 创建批量翻译脚本
- [ ] 创建文章标记工具

### 阶段3：自动化流程
- [ ] 集成到部署脚本
- [ ] 添加翻译缓存机制
- [ ] 创建翻译质量检查工具

## 快速开始

### 为新页面添加多语言支持

1. **在页面头部引入翻译文件**：
```html
<script src="js/translations.js"></script>
<script src="js/i18n.js"></script>
```

2. **添加语言选择器**（复制index.html中的语言选择器代码）

3. **为UI元素添加data-i18n属性**：
```html
<nav>
    <a href="#" data-i18n="nav.travelGuides">Travel Guides</a>
    <a href="#" data-i18n="nav.travelStories">Travel Stories</a>
</nav>
```

4. **在translations.js中添加对应的翻译键值**

### 为文章添加翻译支持

1. **标记需要翻译的内容**：
```html
<article>
    <h1 data-translate="true">Article Title</h1>
    <div data-translate="true">
        <p>Article content here...</p>
    </div>
</article>
```

2. **在页面底部添加翻译脚本**：
```html
<script src="js/article-i18n.js"></script>
<script>
    // 当语言切换时自动翻译
    document.addEventListener('languageChanged', () => {
        if (typeof articleTranslator !== 'undefined') {
            articleTranslator.translatePage();
        }
    });
</script>
```

## 配置翻译API

### Google Cloud Translation API
```javascript
// 在 article-i18n.js 中配置
async translateWithAPI(elements, targetLang) {
    const apiKey = 'YOUR_GOOGLE_API_KEY';
    const response = await fetch(
        `https://translation.googleapis.com/language/translate/v2?key=${apiKey}`,
        {
            method: 'POST',
            body: JSON.stringify({
                q: text,
                source: 'en',
                target: targetLang,
                format: 'text'
            })
        }
    );
    // 处理响应...
}
```

### Azure Translator
```javascript
const subscriptionKey = 'YOUR_AZURE_KEY';
const endpoint = 'https://api.cognitive.microsofttranslator.com/translate';
```

### 百度翻译API
```javascript
const appId = 'YOUR_BAIDU_APP_ID';
const secretKey = 'YOUR_BAIDU_SECRET';
```

## 最佳实践

1. **缓存翻译结果**：避免重复翻译相同内容
2. **渐进式翻译**：优先翻译可见内容，后台翻译其他内容
3. **翻译质量检查**：定期检查自动翻译的质量
4. **用户反馈机制**：允许用户报告翻译错误
5. **SEO优化**：为多语言页面添加hreflang标签

## 常见问题

### Q: 每次新文章都需要手动翻译吗？
A: 不需要。如果使用方案B（动态翻译），新文章只需要添加 `data-translate="true"` 标记即可。系统会自动翻译。

### Q: 翻译API费用高吗？
A: 看使用的服务：
- Google Cloud Translation: 每百万字符约$20
- Azure Translator: 每百万字符约$10
- 百度翻译: 有免费额度，超出后按量付费

### Q: 可以只翻译部分文章吗？
A: 可以。只对标记了 `data-translate="true"` 的元素进行翻译。

### Q: 翻译质量如何保证？
A: 
1. 重要文章建议预翻译+人工校对
2. 一般文章使用自动翻译
3. 提供用户反馈机制

## 下一步

1. 完成所有静态页面的UI翻译
2. 选择并配置翻译API
3. 为现有文章添加翻译标记
4. 测试翻译功能和性能
5. 收集用户反馈并优化


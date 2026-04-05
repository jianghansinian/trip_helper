# 多语言支持快速指南

## 问题解答

### Q: 要把所有页面都支持这些语言，还需要做哪些工作？

**答案：主要需要完成以下工作：**

1. **静态页面UI翻译**（1-2天工作量）
   - blog/index.html
   - guides/index.html  
   - about.html
   - contact.html
   - privacy.html
   - admin.html
   - 等等

2. **为每个页面添加语言选择器**（可以自动化）
   - 使用 `tools/i18n/add-i18n-support.py` 脚本批量添加

3. **文章内容翻译**（可选，建议自动化）
   - 方案A：重要文章预翻译（手动/脚本生成）
   - 方案B：使用客户端自动翻译（添加标记即可）

### Q: 仍然通过创建translations.js的方式吗？

**答案：部分使用translations.js，部分自动化翻译**

#### 使用 translations.js（必须）：
- ✅ **UI元素**：导航菜单、按钮、页脚、表单标签等固定文本
- ✅ **静态页面内容**：关于我们、隐私政策等页面的大部分内容
- ✅ **优点**：翻译质量可控，性能好

#### 使用自动翻译（推荐）：
- ✅ **文章内容**：博客文章、指南文章的正文内容
- ✅ **动态内容**：评论、用户生成内容
- ✅ **优点**：维护简单，新文章无需手动翻译

### Q: 每次上传新文章会不会很不方便？

**答案：不会！使用自动化方案，上传新文章非常方便：**

#### 方案对比

| 方案 | 新文章操作 | 维护成本 | 翻译质量 | 推荐度 |
|------|-----------|---------|---------|--------|
| **纯手动翻译** | 需要为每篇文章手动添加9种语言翻译 | ⭐⭐⭐⭐⭐ 很高 | ⭐⭐⭐⭐⭐ 最好 | ⭐⭐ 不推荐 |
| **translations.js + 自动翻译** | 只需添加 `data-translate="true"` 标记 | ⭐⭐ 很低 | ⭐⭐⭐⭐ 很好 | ⭐⭐⭐⭐⭐ 推荐 |
| **服务器端预翻译** | 运行脚本自动生成多语言版本 | ⭐⭐⭐ 中等 | ⭐⭐⭐⭐ 很好 | ⭐⭐⭐⭐ 适合热门文章 |

#### 推荐工作流程

**日常文章发布（最简单）：**
```
1. 写文章（英文）
2. 在需要翻译的内容上添加 data-translate="true"
3. 上传 → 完成！
   - UI元素自动使用translations.js翻译
   - 文章内容使用自动翻译API翻译
```

**重要文章（质量优先）：**
```
1. 写文章（英文）
2. 运行翻译脚本生成多语言版本
3. 人工校对（可选）
4. 上传多语言版本
```

## 快速开始

### 1. 为新页面添加多语言支持（自动化）

```bash
# 为所有页面添加语言选择器
python3 tools/i18n/add-i18n-support.py

# 只为blog目录添加
python3 tools/i18n/add-i18n-support.py --dir blog

# 预览模式（不实际修改）
python3 tools/i18n/add-i18n-support.py --dry-run
```

### 2. 为新文章添加翻译支持

在文章HTML中添加标记：
```html
<article>
    <h1 data-translate="true">Article Title</h1>
    <div data-translate="true">
        <p>Article content here...</p>
        <p>More content...</p>
    </div>
</article>
```

### 3. 在translations.js中添加新翻译键

如果需要翻译新的UI元素：
```javascript
// js/translations.js
window.translations = {
    en: {
        'your.new.key': 'English text',
        // ...
    },
    ko: {
        'your.new.key': '한국어 텍스트',
        // ...
    },
    // ... 其他语言
};
```

## 配置自动翻译API（可选）

### 使用Google Cloud Translation API

1. 注册Google Cloud账号
2. 启用Translation API
3. 获取API密钥
4. 在 `js/article-i18n.js` 中配置：

```javascript
async translateWithAPI(elements, targetLang) {
    const apiKey = 'YOUR_API_KEY';
    // 实现API调用...
}
```

### 使用免费翻译服务

- **MyMemory Translation API**（免费，每日10000字符）
- **LibreTranslate**（开源，可自建服务器）
- **百度翻译API**（有免费额度）

## 实施计划

### 阶段1：完成UI翻译（当前）
- [x] 创建 translations.js
- [x] 创建 i18n.js
- [x] 主页面多语言支持
- [ ] 运行脚本为其他页面添加语言选择器
- [ ] 为其他页面的UI元素添加data-i18n属性

**预计时间：2-4小时**

### 阶段2：配置自动翻译（可选）
- [ ] 选择翻译API服务
- [ ] 配置API密钥
- [ ] 测试翻译功能
- [ ] 优化翻译性能（缓存等）

**预计时间：1-2天**

### 阶段3：文章内容翻译
- [ ] 为现有文章添加 `data-translate="true"` 标记
- [ ] 测试文章翻译功能
- [ ] 优化翻译质量

**预计时间：按文章数量，每篇5-10分钟**

## 成本估算

### 翻译API成本（如果使用）
- **Google Cloud Translation**: $20/百万字符
- **Azure Translator**: $10/百万字符  
- **百度翻译**: 免费额度后 $0.05/千字符

### 示例
- 平均每篇文章：2000字符
- 100篇文章 × 2000字符 = 200,000字符 = 0.2百万字符
- 成本：约 $2-4（Google）或 $2（Azure）

**或者使用免费服务，成本为 $0**

## 总结

✅ **好消息**：
1. UI翻译只需要做一次（translations.js）
2. 新文章只需添加一个标记（`data-translate="true"`）
3. 可以完全自动化，不需要手动翻译每篇文章
4. 翻译质量可以通过预翻译+校对的方式保证重要文章质量

✅ **推荐方案**：
- **UI元素**：使用 translations.js（已实现）
- **文章内容**：使用自动翻译 + 标记（推荐）
- **重要文章**：可以预翻译提高质量

这样既保证了翻译质量，又大大降低了维护成本！


# 文章内容多语言实现指南

## 概述

本文档说明如何为网站的文章标题和正文添加多语言支持。

## 两种实现方案

### 方案1：预翻译（推荐用于重要文章）

**优点：**
- ✅ 加载速度快（无需API调用）
- ✅ SEO友好（搜索引擎可以索引所有语言版本）
- ✅ 翻译质量可控（可以人工审核）
- ✅ 离线可用

**缺点：**
- ❌ 存储空间大（每个语言一个文件）
- ❌ 维护成本高（更新需要重新翻译）
- ❌ 需要预先翻译所有内容

**实现步骤：**

1. **使用翻译工具生成多语言版本**

```bash
# 翻译单篇文章到所有语言
python3 tools/i18n/multi_lang_translator.py --article blog/sichuan_hotpot.html

# 翻译目录下所有文章
python3 tools/i18n/multi_lang_translator.py --dir blog --langs ko,ja,ru,de,fr,es,ar

# 只翻译标题和元数据（快速模式）
python3 tools/i18n/multi_lang_translator.py --article blog/sichuan_hotpot.html --meta-only
```

使用 **`tools/pagegen/doc_to_html.py`** 可将**中文或英文**的整篇站点文章 HTML / Markdown 译为**一种**目标语言（复用 `tools/url-translate` 的 DeepSeek / OpenAI / `simple` 等后端与 `config.yaml`）：

```bash
# 英文文章 → 日语页面（默认只译正文区与 <title>/描述，不动导航）
python3 tools/pagegen/doc_to_html.py -i blog/sichuan_hotpot.html --target ja --source en

# 中文文章 → 英文页面
python3 tools/pagegen/doc_to_html.py -i guides/visa-guide.html --target en --source zh

# 输出路径可省略，默认为同目录 sichuan_hotpot.ja.html
```

2. **生成的文件结构**

```
blog/
  sichuan_hotpot.html          (英文原版)
  sichuan_hotpot.ko.html       (韩语版)
  sichuan_hotpot.ja.html       (日语版)
  sichuan_hotpot.ru.html       (俄语版)
  ...
```

3. **在HTML中标记需要翻译的内容**

```html
<!-- 标题 -->
<h1 class="article-title" data-translate="true">I Ate Everything at a Sichuan Hot Pot Restaurant</h1>

<!-- 副标题 -->
<p class="article-subtitle" data-translate="true">From duck intestines to pig brain...</p>

<!-- 正文段落 -->
<div class="article-content">
    <p data-translate="true">Let me start by saying this: I have regrets...</p>
    <p data-translate="true">The challenge started innocently enough...</p>
</div>
```

4. **添加语言切换逻辑**

在文章页面底部添加：

```html
<script src="../js/article-content-i18n.js"></script>
<script>
    // 监听语言切换事件
    document.addEventListener('languageChanged', function(e) {
        const lang = e.detail.lang;
        // 如果是预翻译版本，直接跳转到对应语言的文件
        if (lang !== 'en') {
            const currentFile = window.location.pathname;
            const langFile = currentFile.replace('.html', `.${lang}.html`);
            // 检查文件是否存在，如果存在则跳转
            fetch(langFile, { method: 'HEAD' })
                .then(response => {
                    if (response.ok) {
                        window.location.href = langFile;
                    } else {
                        // 文件不存在，使用动态翻译
                        articleContentI18n.translatePage(lang);
                    }
                })
                .catch(() => {
                    // 使用动态翻译
                    articleContentI18n.translatePage(lang);
                });
        } else {
            // 恢复英文原版
            const currentFile = window.location.pathname;
            const baseFile = currentFile.replace(/\.\w+\.html$/, '.html');
            if (baseFile !== currentFile) {
                window.location.href = baseFile;
            }
        }
    });
</script>
```

### 方案2：动态翻译（推荐用于一般文章）

**优点：**
- ✅ 维护简单（只需维护一个文件）
- ✅ 存储空间小
- ✅ 更新方便（修改原文即可）

**缺点：**
- ❌ 需要翻译API（可能有费用）
- ❌ 首次加载稍慢
- ❌ 翻译质量可能不稳定

**实现步骤：**

1. **在HTML中标记需要翻译的内容**

```html
<!-- 标题 -->
<h1 class="article-title" data-translate="true">I Ate Everything at a Sichuan Hot Pot Restaurant</h1>

<!-- 正文 -->
<div class="article-content">
    <p data-translate="true">Let me start by saying this: I have regrets...</p>
    <p data-translate="true">The challenge started innocently enough...</p>
</div>
```

2. **引入翻译脚本**

在文章页面底部添加：

```html
<script src="../js/article-content-i18n.js"></script>
```

3. **自动翻译**

翻译系统会自动监听语言切换事件，并在用户切换语言时自动翻译内容。

### 方案3：混合方案（最佳实践）

**策略：**
- 重要/热门文章：使用预翻译（方案1）
- 一般文章：使用动态翻译（方案2）

**实现：**

1. 为重要文章生成多语言版本
2. 一般文章使用动态翻译
3. 在语言切换时，优先检查是否有预翻译版本，如果没有则使用动态翻译

## 快速开始

### 为现有文章添加翻译支持

1. **编辑文章HTML，添加 data-translate 属性**

```html
<h1 class="article-title" data-translate="true">文章标题</h1>
<p class="article-subtitle" data-translate="true">文章副标题</p>
<div class="article-content">
    <p data-translate="true">段落1</p>
    <p data-translate="true">段落2</p>
</div>
```

2. **引入翻译脚本**

在 `</body>` 标签前添加：

```html
<script src="../js/article-content-i18n.js"></script>
```

3. **测试翻译**

打开文章页面，切换语言，查看翻译效果。

### 批量处理现有文章

可以使用脚本自动为所有文章添加 `data-translate` 属性：

```bash
# 为blog目录下的所有文章添加翻译标记
python3 tools/i18n/add-translate-attributes.py --dir blog

# 为guides目录下的所有文章添加翻译标记
python3 tools/i18n/add-translate-attributes.py --dir guides
```

## 翻译API配置

### 使用免费的MyMemory API（默认）

无需配置，但有使用限制（每天1000次请求）。

### 使用Google Cloud Translation API

1. 获取API密钥
2. 修改 `js/article-content-i18n.js` 中的 `callTranslationAPI` 方法

### 使用Azure Translator

1. 获取API密钥和端点
2. 修改 `js/article-content-i18n.js` 中的 `callTranslationAPI` 方法

## 注意事项

1. **翻译质量**：自动翻译可能不够准确，重要内容建议人工审核
2. **性能**：动态翻译会增加页面加载时间，建议对重要文章使用预翻译
3. **SEO**：预翻译版本对SEO更友好
4. **缓存**：翻译结果会缓存在浏览器中，提高性能

## 推荐工作流程

1. **新文章发布时**：
   - 为文章添加 `data-translate="true"` 属性
   - 使用动态翻译（方案2）

2. **文章成为热门后**：
   - 使用 `multi_lang_translator.py` 生成多语言版本
   - 切换到预翻译（方案1）

3. **定期维护**：
   - 检查翻译质量
   - 更新过时的翻译
   - 清理不再需要的多语言文件


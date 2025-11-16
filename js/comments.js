// comments.js
// 将这个文件放在你的网站根目录的 js/ 文件夹中

class CommentSystem {
  constructor(containerId, pageName = 'general') {
    this.container = document.getElementById(containerId);
    this.pageName = pageName;
    this.apiBase = '/api/comments';
    this.init();
  }

  async init() {
    await this.loadComments();
    this.setupForm();
  }

  async loadComments() {
    try {
      const response = await fetch(`${this.apiBase}?page=${this.pageName}`);
      const data = await response.json();
      
      if (data.success) {
        // 只显示已审核的评论
        const approvedComments = data.comments.filter(c => c.approved);
        this.renderComments(approvedComments);
      }
    } catch (error) {
      console.error('Error loading comments:', error);
      this.showError('Failed to load comments. Please try again later.');
    }
  }

  renderComments(comments) {
    const commentsSection = this.container.querySelector('.comments-list') || 
                           this.createCommentsSection();
    
    if (comments.length === 0) {
      commentsSection.innerHTML = `
        <div style="text-align: center; padding: 2rem; color: #999;">
          <p>No comments yet. Be the first to share your thoughts!</p>
        </div>
      `;
      return;
    }

    commentsSection.innerHTML = comments.map(comment => this.renderComment(comment)).join('');
  }

  createCommentsSection() {
    const section = document.createElement('div');
    section.className = 'comments-list';
    this.container.appendChild(section);
    return section;
  }

  renderComment(comment) {
    const date = new Date(comment.timestamp);
    const timeAgo = this.getTimeAgo(date);
    const avatar = this.getAvatarEmoji(comment.name);
    const categoryColor = this.getCategoryColor(comment.category);

    return `
      <div class="comment" style="background: white; padding: 1.5rem; border-radius: 10px; border-left: 4px solid ${categoryColor}; margin-bottom: 1.5rem;">
        <div style="display: flex; gap: 1rem;">
          <div style="width: 45px; height: 45px; border-radius: 50%; background: linear-gradient(135deg, #c41e3a, #ffd700); display: flex; align-items: center; justify-content: center; font-size: 1.3rem; flex-shrink: 0;">
            ${avatar}
          </div>
          <div style="flex: 1;">
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem; flex-wrap: wrap;">
              <strong style="color: #333;">${this.escapeHtml(comment.name)}</strong>
              <span style="color: #999; font-size: 0.85rem;">${timeAgo}</span>
            </div>
            <div style="background: ${categoryColor}15; padding: 6px 12px; border-radius: 5px; display: inline-block; margin-bottom: 0.8rem;">
              <span style="font-size: 0.85rem; color: ${categoryColor}; font-weight: 600;">${comment.category}</span>
            </div>
            <p style="color: #555; line-height: 1.7; margin: 0; white-space: pre-wrap;">${this.escapeHtml(comment.message)}</p>
          </div>
        </div>
      </div>
    `;
  }

  setupForm() {
    const form = this.container.querySelector('#comment-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.submitComment(form);
    });
  }

  async submitComment(form) {
    const submitButton = form.querySelector('button[type="submit"]');
    const originalText = submitButton.textContent;
    
    try {
      // 禁用按钮
      submitButton.disabled = true;
      submitButton.textContent = 'Submitting...';

      const formData = new FormData(form);
      const data = {
        name: formData.get('name'),
        email: formData.get('email'),
        category: formData.get('category'),
        message: formData.get('message'),
        page: this.pageName,
        notify: formData.get('notify') === 'on'
      };

      // 简单的验证
      if (!data.name || !data.email || !data.message) {
        throw new Error('Please fill in all required fields');
      }

      if (!this.validateEmail(data.email)) {
        throw new Error('Please enter a valid email address');
      }

      const response = await fetch(this.apiBase, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
      });

      const result = await response.json();

      if (result.success) {
        this.showSuccess('Thank you! Your comment has been submitted and will appear after review.');
        form.reset();
        // 可选：重新加载评论
        // await this.loadComments();
      } else {
        throw new Error(result.error || 'Failed to submit comment');
      }
    } catch (error) {
      console.error('Error submitting comment:', error);
      this.showError(error.message || 'Failed to submit comment. Please try again.');
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = originalText;
    }
  }

  // 辅助方法
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  validateEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  getTimeAgo(date) {
    const seconds = Math.floor((new Date() - date) / 1000);
    
    const intervals = {
      year: 31536000,
      month: 2592000,
      week: 604800,
      day: 86400,
      hour: 3600,
      minute: 60
    };

    for (const [unit, secondsInUnit] of Object.entries(intervals)) {
      const interval = Math.floor(seconds / secondsInUnit);
      if (interval >= 1) {
        return `${interval} ${unit}${interval > 1 ? 's' : ''} ago`;
      }
    }

    return 'Just now';
  }

  getAvatarEmoji(name) {
    // 根据名字的第一个字母返回不同的表情
    const emojis = ['👨', '👩', '🧑', '👴', '👵', '🙋', '🙋‍♂️', '🙋‍♀️'];
    const index = name.charCodeAt(0) % emojis.length;
    return emojis[index];
  }

  getCategoryColor(category) {
    const colors = {
      'General Feedback': '#1976d2',
      'Question About China Travel': '#f57c00',
      'Share My Travel Story': '#f57f17',
      'Suggestion for New Content': '#7b1fa2',
      'Report a Problem': '#d32f2f',
      'Just Want to Say Thanks!': '#388e3c'
    };
    return colors[category] || '#1976d2';
  }

  showSuccess(message) {
    this.showNotification(message, 'success');
  }

  showError(message) {
    this.showNotification(message, 'error');
  }

  showNotification(message, type) {
    const notification = document.createElement('div');
    notification.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      padding: 1rem 1.5rem;
      background: ${type === 'success' ? '#4caf50' : '#f44336'};
      color: white;
      border-radius: 8px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      z-index: 10000;
      animation: slideIn 0.3s ease-out;
    `;
    notification.textContent = message;

    // 添加动画
    const style = document.createElement('style');
    style.textContent = `
      @keyframes slideIn {
        from {
          transform: translateX(400px);
          opacity: 0;
        }
        to {
          transform: translateX(0);
          opacity: 1;
        }
      }
    `;
    document.head.appendChild(style);

    document.body.appendChild(notification);

    setTimeout(() => {
      notification.style.animation = 'slideIn 0.3s ease-out reverse';
      setTimeout(() => notification.remove(), 300);
    }, 5000);
  }
}

// 使用示例：
// 在你的HTML页面底部添加：
// <script src="/js/comments.js"></script>
// <script>
//   // 初始化评论系统
//   const comments = new CommentSystem('comments-container', 'homepage');
// </script>
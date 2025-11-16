// functions/api/comments.js
// 这个文件应该放在你的项目根目录下的 functions/api/ 文件夹中

export async function onRequestGet(context) {
  try {
    const { COMMENTS } = context.env;
    const url = new URL(context.request.url);
    const page = url.searchParams.get('page') || 'general';
    
    // 从 KV 获取评论
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    return new Response(JSON.stringify({
      success: true,
      comments: comments
    }), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({
      success: false,
      error: error.message
    }), {
      status: 500,
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
      }
    });
  }
}

export async function onRequestPost(context) {
  try {
    const { COMMENTS } = context.env;
    const data = await context.request.json();
    
    // 验证数据
    if (!data.name || !data.email || !data.message) {
      return new Response(JSON.stringify({
        success: false,
        error: 'Missing required fields'
      }), {
        status: 400,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*'
        }
      });
    }
    
    const page = data.page || 'general';
    
    // 获取现有评论
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    // 创建新评论
    const newComment = {
      id: Date.now().toString(),
      name: data.name,
      email: data.email, // 不会在前端显示
      category: data.category || 'General Feedback',
      message: data.message,
      timestamp: new Date().toISOString(),
      approved: false, // 默认需要审核
      replies: []
    };
    
    // 添加到评论列表
    comments.unshift(newComment);
    
    // 保存到 KV（保留最新100条）
    await COMMENTS.put(`comments:${page}`, JSON.stringify(comments.slice(0, 100)));
    
    // 发送通知邮件（可选）
    // 你可以使用 Cloudflare Email Workers 或其他邮件服务
    
    return new Response(JSON.stringify({
      success: true,
      message: 'Comment submitted successfully. It will appear after approval.',
      comment: newComment
    }), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
      }
    });
  } catch (error) {
    return new Response(JSON.stringify({
      success: false,
      error: error.message
    }), {
      status: 500,
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*'
      }
    });
  }
}

export async function onRequestOptions(context) {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    }
  });
}


// functions/api/admin-comments.js
// 管理员查看所有评论的API

export async function onRequestGet(context) {
  try {
    const { COMMENTS } = context.env;
    const url = new URL(context.request.url);
    const adminKey = url.searchParams.get('key');
    
    // 简单的管理员密钥验证（你需要在Cloudflare设置环境变量 ADMIN_KEY）
    if (adminKey !== context.env.ADMIN_KEY) {
      return new Response(JSON.stringify({
        success: false,
        error: 'Unauthorized'
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    const page = url.searchParams.get('page') || 'general';
    
    // 获取所有评论（包括未审核的）
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    return new Response(JSON.stringify({
      success: true,
      comments: comments,
      total: comments.length
    }), {
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (error) {
    return new Response(JSON.stringify({
      success: false,
      error: error.message
    }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// 审核评论
export async function onRequestPost(context) {
  try {
    const { COMMENTS } = context.env;
    const data = await context.request.json();
    const adminKey = data.adminKey;
    
    if (adminKey !== context.env.ADMIN_KEY) {
      return new Response(JSON.stringify({
        success: false,
        error: 'Unauthorized'
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    const page = data.page || 'general';
    const commentId = data.commentId;
    const action = data.action; // 'approve' or 'delete'
    
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    if (action === 'approve') {
      const comment = comments.find(c => c.id === commentId);
      if (comment) {
        comment.approved = true;
      }
    } else if (action === 'delete') {
      const index = comments.findIndex(c => c.id === commentId);
      if (index !== -1) {
        comments.splice(index, 1);
      }
    }
    
    await COMMENTS.put(`comments:${page}`, JSON.stringify(comments));
    
    return new Response(JSON.stringify({
      success: true,
      message: `Comment ${action}d successfully`
    }), {
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (error) {
    return new Response(JSON.stringify({
      success: false,
      error: error.message
    }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
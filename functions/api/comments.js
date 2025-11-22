// functions/api/comments.js
export async function onRequestGet(context) {
  try {
    const { COMMENTS } = context.env;
    const url = new URL(context.request.url);
    const page = url.searchParams.get('page') || 'homepage';
    
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    // 只返回已审核的评论
    const approvedComments = comments.filter(c => c.approved);
    
    return new Response(JSON.stringify({
      success: true,
      comments: approvedComments
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
    
    // 验证必填字段
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
    
    const page = data.page || 'homepage';
    
    // 获取现有评论
    const commentsJson = await COMMENTS.get(`comments:${page}`);
    const comments = commentsJson ? JSON.parse(commentsJson) : [];
    
    // 创建新评论
    const newComment = {
      id: Date.now().toString(),
      name: data.name,
      email: data.email,
      category: data.category || 'General Feedback',
      message: data.message,
      timestamp: new Date().toISOString(),
      approved: false, // 默认需要审核
      replies: []
    };
    
    // 添加到评论列表开头
    comments.unshift(newComment);
    
    // 保存到 KV（只保留最新100条）
    await COMMENTS.put(`comments:${page}`, JSON.stringify(comments.slice(0, 100)));
    
    return new Response(JSON.stringify({
      success: true,
      message: 'Comment submitted successfully. It will appear after approval.'
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

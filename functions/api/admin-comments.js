// functions/api/admin-comments.js
export async function onRequestGet(context) {
  try {
    const { COMMENTS, ADMIN_KEY } = context.env;
    const url = new URL(context.request.url);
    const adminKey = url.searchParams.get('key');
    
    // 验证管理员密钥
    if (adminKey !== ADMIN_KEY) {
      return new Response(JSON.stringify({
        success: false,
        error: 'Unauthorized'
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    const page = url.searchParams.get('page') || 'homepage';
    
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

export async function onRequestPost(context) {
  try {
    const { COMMENTS, ADMIN_KEY } = context.env;
    const data = await context.request.json();
    const adminKey = data.adminKey;
    
    // 验证管理员密钥
    if (adminKey !== ADMIN_KEY) {
      return new Response(JSON.stringify({
        success: false,
        error: 'Unauthorized'
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    const page = data.page || 'homepage';
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

export async function onRequestOptions(context) {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    }
  });
}
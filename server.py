"""OpenAI Compatible API Server for Qwen AI with Context Support and Token Rotation"""

import json
import time
import threading
import random
import os
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        print(f"[Server] 已加载环境变量: {env_path}")
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from qwen_ai import QwenAiClient
from qwen_ai.vless_proxy import get_subscription_pool, init_subscription_pool_from_env
from qwen_ai.node_storage import get_node_storage
from qwen_ai.node_tester import get_node_tester


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[str] = None
    chat_id: Optional[str] = None

    class Config:
        extra = "allow"


# Global settings from environment
AUTO_DELETE_CHAT = os.environ.get('AUTO_DELETE_CHAT', 'false').lower() == 'true'


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "qwen-ai"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


# In-memory chat session storage
class ChatSessionManager:
    """Manage chat sessions for context support"""
    
    def __init__(self, ttl_hours: int = 24):
        self.sessions: Dict[str, Dict] = {}
        self.ttl = timedelta(hours=ttl_hours)
        self.lock = threading.Lock()
    
    def get(self, chat_id: str) -> Optional[Dict]:
        """Get chat session"""
        with self.lock:
            session = self.sessions.get(chat_id)
            if session:
                # Check if expired
                if datetime.now() - session['created'] > self.ttl:
                    del self.sessions[chat_id]
                    return None
                session['last_used'] = datetime.now()
                return session
            return None
    
    def set(self, chat_id: str, model: str, messages: List[Dict]):
        """Save chat session"""
        with self.lock:
            self.sessions[chat_id] = {
                'chat_id': chat_id,
                'model': model,
                'messages': messages,
                'created': datetime.now(),
                'last_used': datetime.now(),
            }
    
    def update_messages(self, chat_id: str, messages: List[Dict]):
        """Update messages in session"""
        with self.lock:
            if chat_id in self.sessions:
                self.sessions[chat_id]['messages'] = messages
                self.sessions[chat_id]['last_used'] = datetime.now()
    
    def cleanup_expired(self):
        """Remove expired sessions"""
        with self.lock:
            now = datetime.now()
            expired = [
                chat_id for chat_id, session in self.sessions.items()
                if now - session['created'] > self.ttl
            ]
            for chat_id in expired:
                del self.sessions[chat_id]


# Global session manager
session_manager = ChatSessionManager(ttl_hours=24)

# Global subscription proxy pool
subscription_pool = None

# 初始化订阅代理池
async def init_proxy_pool():
    """初始化订阅代理池"""
    global subscription_pool
    try:
        subscription_pool = await init_subscription_pool_from_env()
        print(f"[Proxy] Subscription pool initialized with pattern: {subscription_pool.pattern}")
        stats = subscription_pool.get_stats()
        print(f"[Proxy] Available nodes: {stats.get('current_pattern', {}).get('available', 0)}")
    except Exception as e:
        print(f"[Proxy] Failed to initialize subscription pool: {e}")
        subscription_pool = None


app = FastAPI(
    title="Qwen AI OpenAI Compatible API",
    description="OpenAI compatible API for Qwen AI (chat.qwen.ai) with context support and Vless proxy pool",
    version="0.3.0"
)

# 添加 CORS 支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """服务启动时初始化"""
    await init_proxy_pool()


SUPPORTED_MODELS = [
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.5-omni-plus",
    "qwen3.5-flash",
    "qwen3.5-max-preview",
    "qwen3.5-max-2026-03-08",
    "qwen3.6-plus-preview",
    "qwen3.5-397b-a17b",
    "qwen3.5-122b-a10b",
    "qwen3.5-omni-flash",
    "qwen3.5-27b",
    "qwen3.5-35b-a3b",
    "qwen3-max",
    "qwen3-235b-a22b-2507",
    "qwen3-coder",
    "qwen3-vl-235b-a22b",
    "qwen3-omni-flash",
    "qwen2.5-max",
]


@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """List available models"""
    models = [ModelInfo(id=model_id) for model_id in SUPPORTED_MODELS]
    return ModelsResponse(data=models)


def select_random_token(token_string: str) -> str:
    """Select a random token from comma-separated list"""
    tokens = [t.strip() for t in token_string.split(',') if t.strip()]
    if not tokens:
        raise ValueError("No valid tokens provided")
    return random.choice(tokens)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None)
):
    """Chat completions endpoint with context support and token rotation"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if authorization.startswith("Bearer "):
        jwt_token_string = authorization[7:]
    else:
        jwt_token_string = authorization

    if not jwt_token_string:
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    start_time = time.time()
    try:
        jwt_token = select_random_token(jwt_token_string)

        client = QwenAiClient(token=jwt_token)

        existing_chat_id = request.chat_id

        if request.stream:
            response = StreamingResponse(
                openai_stream(client, request.model, request.messages, request.temperature, existing_chat_id, AUTO_DELETE_CHAT),
                media_type="text/event-stream"
            )
            # 记录成功请求
            latency = time.time() - start_time
            request_stats.record_request(success=True, latency=latency)
            return response
        else:
            response = await openai_non_stream(client, request.model, request.messages, request.temperature, existing_chat_id, AUTO_DELETE_CHAT)
            # 记录成功请求
            latency = time.time() - start_time
            request_stats.record_request(success=True, latency=latency)
            return response

    except Exception as e:
        # 记录失败请求
        latency = time.time() - start_time
        request_stats.record_request(success=False, latency=latency)
        raise HTTPException(status_code=500, detail=str(e))


async def openai_non_stream(client, model, messages, temperature, existing_chat_id=None, auto_delete_chat=False):
    """Non-streaming response with context support"""
    chat_id = existing_chat_id
    chat_created = False

    try:
        if chat_id:
            response, new_chat_id, _ = client.adapter.chat_completion(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                auto_delete_chat=auto_delete_chat
            )
            chat_id = new_chat_id
            chat_created = True
        else:
            response, chat_id, _ = client.adapter.chat_completion(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                auto_delete_chat=auto_delete_chat
            )
            chat_created = True

        content = ''
        reasoning = ''
        response_id = ''
        created = int(time.time())

        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode('utf-8')
            if not line_str.startswith('data: '):
                continue

            data_str = line_str[6:]
            if data_str == '[DONE]':
                break

            try:
                data = json.loads(data_str)
                if data.get('response.created', {}).get('response_id'):
                    response_id = data['response.created']['response_id']

                if data.get('choices'):
                    delta = data['choices'][0].get('delta', {})
                    phase = delta.get('phase')
                    status = delta.get('status')
                    text = delta.get('content', '')

                    if phase == 'think' and status != 'finished':
                        reasoning += text
                    elif phase == 'answer' or phase is None:
                        content += text
            except:
                pass

        # Handle auto delete or session save
        if auto_delete_chat and chat_created and chat_id:
            try:
                client.adapter.delete_chat(chat_id)
                print(f'[Server] Auto-deleted chat: {chat_id}')
            except Exception as e:
                print(f'[Server] Failed to auto-delete chat {chat_id}: {e}')
        else:
            session_manager.set(chat_id, model, messages + [{'role': 'assistant', 'content': content}])

        return JSONResponse(content={
            'id': response_id or '',
            'object': 'chat.completion',
            'created': created,
            'model': model,
            'chat_id': chat_id if not auto_delete_chat else None,
            'choices': [{
                'index': 0,
                'message': {
                    'role': 'assistant',
                    'content': content,
                    'reasoning_content': reasoning if reasoning else None
                },
                'finish_reason': 'stop'
            }],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}
        })

    except Exception as e:
        if chat_id:
            try:
                client.adapter.delete_chat(chat_id)
            except:
                pass
        raise


def openai_stream(client, model, messages, temperature, existing_chat_id=None, auto_delete_chat=False):
    """Streaming response with context support, thinking and image generation"""
    chat_id = existing_chat_id
    created = int(time.time())
    full_content = ''
    reasoning_content = ''
    has_sent_role = False
    chat_created = False
    
    try:
        if chat_id:
            # Continue existing chat
            response, new_chat_id, _ = client.adapter.chat_completion(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                auto_delete_chat=auto_delete_chat
            )
            chat_id = new_chat_id
            chat_created = True
        else:
            # New chat
            response, chat_id, _ = client.adapter.chat_completion(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                auto_delete_chat=auto_delete_chat
            )
            chat_created = True

        response_id = ''
        
        for line in response.iter_lines():
            if not line:
                continue
            
            line_str = line.decode('utf-8')
            if not line_str.startswith('data: '):
                continue
            
            data_str = line_str[6:]
            
            if data_str == '[DONE]':
                continue
            
            try:
                data = json.loads(data_str)
                
                # Get response_id from response.created
                if data.get('response.created', {}).get('response_id'):
                    response_id = data['response.created']['response_id']
                    continue
                
                if not data.get('choices'):
                    continue
                
                qwen_choice = data['choices'][0]
                qwen_delta = qwen_choice.get('delta', {})
                
                phase = qwen_delta.get('phase')
                status = qwen_delta.get('status')
                content = qwen_delta.get('content', '')
                extra = qwen_delta.get('extra', {})
                
                # Build OpenAI format chunk
                openai_chunk = {
                    'id': response_id or '',
                    'object': 'chat.completion.chunk',
                    'created': created,
                    'model': model,
                    'chat_id': chat_id,
                    'choices': [{
                        'index': 0,
                        'delta': {},
                        'finish_reason': None
                    }]
                }
                
                # First chunk with role
                if not has_sent_role:
                    openai_chunk['choices'][0]['delta']['role'] = 'assistant'
                    has_sent_role = True
                    yield f'data: {json.dumps(openai_chunk)}\n\n'
                
                # Handle thinking_summary phase - show reasoning process
                if phase == 'thinking_summary':
                    summary_thought = extra.get('summary_thought', {})
                    if summary_thought.get('content'):
                        # Combine thinking content
                        thinking_text = '\n'.join(summary_thought['content'])
                        if thinking_text and thinking_text != reasoning_content:
                            reasoning_content = thinking_text
                            # Send reasoning content in custom field
                            openai_chunk['choices'][0]['delta'] = {
                                'reasoning_content': thinking_text
                            }
                            yield f'data: {json.dumps(openai_chunk)}\n\n'
                    
                    # Send finish for thinking phase
                    if status == 'finished':
                        openai_chunk['choices'][0]['delta'] = {'reasoning_content': ''}
                        yield f'data: {json.dumps(openai_chunk)}\n\n'
                
                # Handle image_gen_tool phase - image generation
                elif phase == 'image_gen_tool':
                    function_call = qwen_delta.get('function_call', {})
                    function_id = qwen_delta.get('function_id', '')
                    
                    if function_call.get('name') == 'image_gen':
                        # Send tool call start
                        openai_chunk['choices'][0]['delta'] = {
                            'tool_calls': [{
                                'index': 0,
                                'id': function_id or 'image_gen_0',
                                'type': 'function',
                                'function': {
                                    'name': 'image_gen',
                                    'arguments': function_call.get('arguments', '{}')
                                }
                            }]
                        }
                        yield f'data: {json.dumps(openai_chunk)}\n\n'
                    
                    # Handle image generation result
                    if status == 'finished' and extra.get('tool_result'):
                        tool_result = extra['tool_result']
                        image_list = extra.get('image_list', [])
                        
                        # Send image URLs in content
                        if image_list:
                            image_urls = [img.get('image', '') for img in image_list if img.get('image')]
                            if image_urls:
                                image_content = '\n'.join([f'![Generated Image]({url})' for url in image_urls])
                                openai_chunk['choices'][0]['delta'] = {'content': image_content}
                                full_content += image_content
                                yield f'data: {json.dumps(openai_chunk)}\n\n'
                
                # Handle regular content (answer phase)
                elif phase == 'answer' or phase is None:
                    if content:
                        openai_chunk['choices'][0]['delta'] = {'content': content}
                        full_content += content
                        yield f'data: {json.dumps(openai_chunk)}\n\n'
                    
                    # Final chunk
                    if status == 'finished':
                        openai_chunk['choices'][0]['delta'] = {}
                        openai_chunk['choices'][0]['finish_reason'] = 'stop'
                        yield f'data: {json.dumps(openai_chunk)}\n\n'
                        yield 'data: [DONE]\n\n'

                        # Handle auto delete or session save
                        if auto_delete_chat and chat_created and chat_id:
                            try:
                                client.adapter.delete_chat(chat_id)
                                print(f'[Server] Auto-deleted chat: {chat_id}')
                            except Exception as e:
                                print(f'[Server] Failed to auto-delete chat {chat_id}: {e}')
                        else:
                            # Save session for context
                            session_manager.set(chat_id, model, messages + [{'role': 'assistant', 'content': full_content}])
                        break
                    
            except json.JSONDecodeError:
                continue
        
    except Exception as e:
        error = {'error': {'message': str(e), 'type': 'internal_error'}}
        yield f'data: {json.dumps(error)}\n\n'
        yield 'data: [DONE]\n\n'
        # Clean up on error
        if chat_id:
            try:
                client.adapter.delete_chat(chat_id)
            except:
                pass


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "qwen-ai-openai-api"}


class TokenHealthRequest(BaseModel):
    tokens: str
    
    class Config:
        extra = "allow"


class TokenHealthResult(BaseModel):
    token: str
    status: str  # "healthy" or "unhealthy"
    valid: bool
    error: Optional[str] = None


class TokenHealthResponse(BaseModel):
    total: int
    healthy: int
    unhealthy: int
    results: List[TokenHealthResult]


@app.post("/v1/tokens/health", response_model=TokenHealthResponse)
async def check_tokens_health(request: TokenHealthRequest):
    """Check health status of one or more JWT tokens
    
    Request body:
        tokens: Comma-separated JWT tokens to check
    
    Returns:
        Health status for each token
    """
    token_list = [t.strip() for t in request.tokens.split(',') if t.strip()]
    
    if not token_list:
        raise HTTPException(status_code=400, detail="No tokens provided")
    
    results = []
    healthy_count = 0
    
    for token in token_list:
        # Mask token for display (show first 20 and last 10 chars)
        masked_token = token[:20] + "..." + token[-10:] if len(token) > 30 else token
        
        try:
            client = QwenAiClient(token=token)
            # Try to create a chat to verify token is valid
            chat_id = client.adapter.create_chat('qwen3.5-plus', 'Health_Check')
            # Delete the test chat immediately
            client.adapter.delete_chat(chat_id)
            
            results.append(TokenHealthResult(
                token=masked_token,
                status="healthy",
                valid=True,
                error=None
            ))
            healthy_count += 1
            
        except Exception as e:
            error_msg = str(e)
            # Check for specific error types
            if "401" in error_msg or "Unauthorized" in error_msg:
                error_msg = "Token expired or invalid"
            elif "403" in error_msg:
                error_msg = "Token forbidden"
            elif "timeout" in error_msg.lower():
                error_msg = "Request timeout"
            
            results.append(TokenHealthResult(
                token=masked_token,
                status="unhealthy",
                valid=False,
                error=error_msg
            ))
    
    return TokenHealthResponse(
        total=len(token_list),
        healthy=healthy_count,
        unhealthy=len(token_list) - healthy_count,
        results=results
    )


@app.get("/v1/tokens/health")
async def check_tokens_health_get(
    tokens: str
):
    """Check health status of one or more JWT tokens (GET method)
    
    Query parameter:
        tokens: Comma-separated JWT tokens to check
    
    Returns:
        Health status for each token
    """
    return await check_tokens_health(TokenHealthRequest(tokens=tokens))


@app.get("/")
async def root():
    return {
        "service": "Qwen AI OpenAI Compatible API",
        "version": "0.3.0",
        "features": ["context_support", "streaming", "non_streaming", "token_health_check", "vless_proxy_pool"],
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "health": "/health",
            "tokens_health": "/v1/tokens/health",
            "proxy_stats": "/v1/proxy/stats",
            "proxy_refresh": "/v1/proxy/refresh",
            "proxy_test": "/v1/proxy/test"
        }
    }


# ==================== Vless 代理管理 API ====================

class ProxyRefreshRequest(BaseModel):
    test_nodes: bool = True


class ProxyTestRequest(BaseModel):
    pattern: Optional[str] = None
    max_concurrent: int = 10


@app.get("/v1/proxy/stats")
async def proxy_stats():
    """获取代理池统计信息"""
    global subscription_pool
    
    if subscription_pool is None:
        return {
            "enabled": False,
            "message": "Proxy pool not initialized"
        }
    
    try:
        stats = subscription_pool.get_stats()
        return {
            "enabled": True,
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/proxy/refresh")
async def proxy_refresh(request: ProxyRefreshRequest, background_tasks: BackgroundTasks):
    """刷新订阅并测试节点"""
    global subscription_pool
    
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    
    try:
        result = await subscription_pool.refresh_subscriptions(test_nodes=request.test_nodes)
        return {
            "success": True,
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/proxy/test")
async def proxy_test(request: ProxyTestRequest):
    """测试代理节点"""
    global subscription_pool
    
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    
    try:
        tester = get_node_tester()
        await tester.init()
        
        results = await tester.test_all_available_nodes(pattern=request.pattern)
        summary = tester.get_test_summary(results)
        
        return {
            "success": True,
            "summary": summary,
            "results": [
                {
                    "identifier": r.identifier,
                    "success": r.success,
                    "latency": r.latency,
                    "error": r.error
                }
                for r in results
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/proxy/nodes")
async def proxy_nodes(pattern: Optional[str] = None, only_available: bool = True):
    """获取代理节点列表"""
    global subscription_pool
    
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    
    try:
        nodes = subscription_pool.get_available_nodes(pattern)
        
        if only_available:
            nodes = [n for n in nodes if n.is_available]
        
        return {
            "total": len(nodes),
            "nodes": [
                {
                    "identifier": n.identifier,
                    "name": n.name,
                    "address": n.address,
                    "port": n.port,
                    "network": n.network,
                    "tls": n.tls,
                    "is_available": n.is_available,
                    "fail_count": n.fail_count,
                    "success_count": n.success_count,
                    "average_latency": n.average_latency,
                    "last_tested": n.last_tested
                }
                for n in nodes
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 请求统计
class RequestStats:
    """请求统计管理器"""
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.request_history = []  # 最近100条请求记录
        self.lock = threading.Lock()
    
    def record_request(self, success: bool = True, latency: float = 0):
        """记录请求"""
        with self.lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1
            
            # 记录历史
            self.request_history.append({
                'time': datetime.now().isoformat(),
                'success': success,
                'latency': latency
            })
            
            # 只保留最近100条
            if len(self.request_history) > 100:
                self.request_history = self.request_history[-100:]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.lock:
            # 计算成功率
            success_rate = (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0
            
            # 计算平均延迟
            avg_latency = sum(r['latency'] for r in self.request_history) / len(self.request_history) if self.request_history else 0
            
            # 按小时分组统计请求数（用于图表）
            hourly_data = {}
            now = datetime.now()
            for i in range(24):
                hour_key = (now - timedelta(hours=i)).strftime('%H:00')
                hourly_data[hour_key] = 0
            
            for req in self.request_history:
                req_time = datetime.fromisoformat(req['time'])
                hour_key = req_time.strftime('%H:00')
                if hour_key in hourly_data:
                    hourly_data[hour_key] += 1
            
            # 转换为图表数据格式（最近12小时）
            chart_labels = []
            chart_data = []
            for i in range(11, -1, -1):
                hour_key = (now - timedelta(hours=i)).strftime('%H:00')
                chart_labels.append(hour_key)
                chart_data.append(hourly_data.get(hour_key, 0))
            
            return {
                'total_requests': self.total_requests,
                'successful_requests': self.successful_requests,
                'failed_requests': self.failed_requests,
                'success_rate': round(success_rate, 2),
                'average_latency': round(avg_latency, 2),
                'request_history': self.request_history[-20:],  # 最近20条
                'chart_labels': chart_labels,
                'chart_data': chart_data
            }


# 全局请求统计实例
request_stats = RequestStats()


@app.get("/v1/admin/stats")
async def get_admin_stats():
    """获取管理后台统计数据"""
    try:
        stats = request_stats.get_stats()
        
        # 获取代理统计
        proxy_stats = {"total_nodes": 0, "available_nodes": 0, "avg_latency": 0}
        if subscription_pool and subscription_pool._initialized:
            nodes = subscription_pool.get_available_nodes()
            available = [n for n in nodes if n.is_available]
            proxy_stats = {
                "total_nodes": len(nodes),
                "available_nodes": len(available),
                "avg_latency": sum(n.average_latency for n in available) / len(available) if available else 0
            }
        
        return {
            **stats,
            **proxy_stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 管理后台路由
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """管理后台页面"""
    try:
        with open("templates/admin.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Admin template not found")


@app.get("/v1/admin/config")
async def get_admin_config():
    """获取当前配置（从环境变量）"""
    return {
        "port": os.environ.get("PORT", "8000"),
        "debug": os.environ.get("DEBUG", "false").lower() == "true",
        "auto_delete_chat": os.environ.get("AUTO_DELETE_CHAT", "false").lower() == "true",
        "proxy_enabled": os.environ.get("PROXY_ENABLED", "false").lower() == "true",
        "subscription_url": os.environ.get("VLESS_SUBSCRIPTION_URLS", ""),
        "pattern": os.environ.get("VLESS_SUBSCRIPTION_PATTERNS", "")
    }


class ConfigUpdateRequest(BaseModel):
    port: str = "8000"
    debug: bool = False
    auto_delete_chat: bool = False
    proxy_enabled: bool = False
    subscription_url: str = ""
    pattern: str = ""


@app.post("/v1/admin/config")
async def update_admin_config(request: ConfigUpdateRequest):
    """更新配置（保存到 .env 文件）"""
    import traceback
    try:
        env_file = ".env"
        env_content = f"""# Qwen AI Reverse API 配置
# 更新时间: {datetime.now().isoformat()}

# ==================== 服务配置 =====================
HOST="0.0.0.0"
PORT="{request.port}"
DEBUG={'true' if request.debug else 'false'}
AUTO_DELETE_CHAT={'true' if request.auto_delete_chat else 'false'}

# ==================== 代理配置 =====================
PROXY_ENABLED={'true' if request.proxy_enabled else 'false'}

# ==================== Vless 订阅配置 =====================
VLESS_SUBSCRIPTION_URLS="{request.subscription_url}"
VLESS_SUBSCRIPTION_PATTERNS="{request.pattern}"
VLESS_AUTO_REFRESH_ON_START=true
VLESS_STORAGE_FILE="vless_nodes.json"
"""

        # 获取当前工作目录和文件路径
        cwd = os.getcwd()
        file_path = os.path.abspath(env_file)

        # 备份现有配置
        if os.path.exists(env_file):
            backup = f"{env_file}.backup"
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    existing = f.read()
                with open(backup, 'w', encoding='utf-8') as f:
                    f.write(existing)
            except Exception as backup_error:
                print(f"[Config] 备份配置失败: {backup_error}")

        # 写入新配置
        try:
            with open(env_file, 'w', encoding='utf-8') as f:
                f.write(env_content)
            print(f"[Config] 配置已写入文件: {file_path}")
        except Exception as write_error:
            error_msg = f"写入文件失败: {str(write_error)}"
            print(f"[Config] {error_msg}")
            return {"success": False, "message": error_msg}

        # 验证文件是否写入成功
        if not os.path.exists(env_file):
            return {"success": False, "message": "文件写入后未找到"}

        # 更新当前环境变量
        os.environ["PORT"] = str(request.port)
        os.environ["DEBUG"] = "true" if request.debug else "false"
        os.environ["AUTO_DELETE_CHAT"] = "true" if request.auto_delete_chat else "false"
        os.environ["PROXY_ENABLED"] = "true" if request.proxy_enabled else "false"
        os.environ["VLESS_SUBSCRIPTION_URLS"] = request.subscription_url
        os.environ["VLESS_SUBSCRIPTION_PATTERNS"] = request.pattern

        # 更新全局变量
        global AUTO_DELETE_CHAT
        AUTO_DELETE_CHAT = request.auto_delete_chat

        return {
            "success": True,
            "message": "配置已保存",
            "file_path": file_path,
            "config": {
                "port": request.port,
                "debug": request.debug,
                "auto_delete_chat": request.auto_delete_chat,
                "proxy_enabled": request.proxy_enabled,
                "subscription_url": request.subscription_url,
                "pattern": request.pattern
            }
        }
    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"[Config] 保存配置时出错:\n{error_detail}")
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

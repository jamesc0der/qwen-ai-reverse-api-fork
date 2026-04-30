"""OpenAI Compatible API Server for Qwen AI with Context Support and Token Rotation"""

import json
import time
import threading
import random
import os
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from qwen_ai import QwenAiClient
from qwen_ai.vless_proxy import get_subscription_pool, init_subscription_pool_from_env
from qwen_ai.node_storage import get_node_storage
from qwen_ai.node_tester import get_node_tester

# ── Debug logging ──────────────────────────────────────────────────────────────
# Set DEFAULT_DEBUG_LOGGING = True here OR pass --debug to start_server.py
DEFAULT_DEBUG_LOGGING: bool = False

from qwen_ai.debug_logger import (
    init_session_logging,
    close_session_logging,
    is_debug,
    log_request_start,
    log_request_end,
    log_chat_create,
    log_chat_delete,
    log_stream_chunk,
    log_tool_detected,
    log_tool_parsed,
    log_proxy_selected,
    log_proxy_result,
    log_exception,
    log_token_health,
    log_raw,
)
# ──────────────────────────────────────────────────────────────────────────────


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


async def init_proxy_pool():
    """Initialize subscription proxy pool"""
    global subscription_pool
    try:
        subscription_pool = await init_subscription_pool_from_env()
        msg = (f"Subscription pool initialized with pattern: {subscription_pool.pattern}")
        print(f"[Proxy] {msg}", flush=True)
        log_raw("INFO", "PROXY", msg)
        stats = subscription_pool.get_stats()
        avail = stats.get('current_pattern', {}).get('available', 0)
        print(f"[Proxy] Available nodes: {avail}", flush=True)
        log_raw("INFO", "PROXY", f"Available nodes: {avail}")
    except Exception as e:
        print(f"[Proxy] Failed to initialize subscription pool: {e}", flush=True)
        log_exception("init_proxy_pool", e)
        subscription_pool = None


app = FastAPI(
    title="Qwen AI OpenAI Compatible API",
    description="OpenAI compatible API for Qwen AI (chat.qwen.ai) with context support and Vless proxy pool",
    version="0.3.0"
)


@app.on_event("startup")
async def startup_event():
    """Initialize on server startup"""
    # Init debug logging if enabled via env (start_server.py sets the env var)
    debug_flag = (
        os.environ.get("QWEN_DEBUG_LOGGING", "").lower() in ("1", "true", "yes")
        or DEFAULT_DEBUG_LOGGING
    )
    if debug_flag:
        # log_path = init_session_logging(debug=True)
        log_raw("INFO", "SERVER", f"FastAPI startup — AUTO_DELETE_CHAT={AUTO_DELETE_CHAT}")

    await init_proxy_pool()


@app.on_event("shutdown")
async def shutdown_event():
    log_raw("INFO", "SERVER", "FastAPI shutdown")
    close_session_logging()


# ──────────────────────────────────────────────────────────────────────────────

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

    try:
        jwt_token = select_random_token(jwt_token_string)

        log_raw("DEBUG", "AUTH",
                f"token selected (masked): {jwt_token[:12]}…{jwt_token[-6:]}")

        client = QwenAiClient(token=jwt_token)

        existing_chat_id = request.chat_id

        log_raw("INFO", "COMPLETIONS",
                f"model={request.model}  stream={request.stream}  "
                f"tools={'yes' if request.tools else 'no'}  "
                f"messages={len(request.messages)}")

        if request.stream:
            return StreamingResponse(
                openai_stream(
                    client, request.model, request.messages,
                    request.temperature, existing_chat_id,
                    AUTO_DELETE_CHAT, tools=request.tools
                ),
                media_type="text/event-stream"
            )
        else:
            return await openai_non_stream(
                client, request.model, request.messages,
                request.temperature, existing_chat_id,
                AUTO_DELETE_CHAT, tools=request.tools
            )

    except Exception as e:
        log_exception("chat_completions", e)
        raise HTTPException(status_code=500, detail=str(e))


async def openai_non_stream(client, model, messages, temperature,
                             existing_chat_id=None, auto_delete_chat=False,
                             tools=None):
    """Non-streaming response with context support"""
    t0 = time.time()
    log_raw("DEBUG", "NON_STREAM", f"start  model={model}")

    try:
        # Use client.chat_completions to handle tool conversion
        result = client.chat_completions(
            model=model,
            messages=messages,
            stream=False,
            temperature=temperature,
            tools=tools,
            auto_delete_chat=auto_delete_chat
        )
        
        # Save session if not auto-deleting and result is valid
        if not auto_delete_chat and isinstance(result, dict) and result.get('chat_id'):
            session_manager.set(
                result['chat_id'], model,
                messages + [{'role': 'assistant',
                             'content': result['choices'][0]['message'].get('content', '')}]
            )

        return JSONResponse(content=result)

    except Exception as e:
        log_exception("openai_non_stream", e)
        if existing_chat_id:
            try:
                client.adapter.delete_chat(existing_chat_id)
            except Exception:
                pass
        raise


def openai_stream(client, model, messages, temperature,
                  existing_chat_id=None, auto_delete_chat=False, tools=None):
    """Streaming response with context support, thinking and image generation"""
    chunk_index = 0
    t0 = time.time()
    log_raw("DEBUG", "STREAM", f"start  model={model}")

    try:
        # Use client.chat_completions to handle tool conversion
        generator = client.chat_completions(
            model=model,
            messages=messages,
            stream=True,
            temperature=temperature,
            tools=tools,
            auto_delete_chat=auto_delete_chat
        )

        for chunk in generator:
            if chunk is None:
                continue

            # Log individual SSE chunks
            if is_debug():
                chunk_index += 1
                # Parse the chunk to extract phase/content for logging
                try:
                    if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                        data = json.loads(chunk[6:])
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            phase = delta.get("phase")
                            status = choices[0].get("finish_reason")
                            content = delta.get("content", "") or delta.get("reasoning_content", "")
                            log_stream_chunk(chunk_index, phase, content, status)
                    elif chunk.strip() == "data: [DONE]":
                        elapsed = (time.time() - t0) * 1000
                        log_raw("INFO", "STREAM",
                                f"DONE  chunks={chunk_index}  [{elapsed:.1f} ms]")
                except Exception:
                    pass  # never let logging break the stream

            yield chunk

    except Exception as e:
        log_exception("openai_stream", e)
        error = {'error': {'message': str(e), 'type': 'internal_error'}}
        yield f'data: {json.dumps(error)}\n\n'
        yield 'data: [DONE]\n\n'


@app.get("/health")
async def health_check():
    log_raw("DEBUG", "HEALTH", "health check called")
    return {"status": "healthy", "service": "qwen-ai-openai-api"}


class TokenHealthRequest(BaseModel):
    tokens: str

    class Config:
        extra = "allow"


class TokenHealthResult(BaseModel):
    token: str
    status: str
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
        masked_token = (token[:20] + "..." + token[-10:]
                        if len(token) > 30 else token)

        try:
            client = QwenAiClient(token=token)
            # Try to create a chat to verify token is valid
            chat_id = client.adapter.create_chat('qwen3.5-plus', 'Health_Check')
            # Delete the test chat immediately
            client.adapter.delete_chat(chat_id)

            log_token_health(masked_token, valid=True)
            results.append(TokenHealthResult(
                token=masked_token, status="healthy", valid=True, error=None
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

            log_token_health(masked_token, valid=False, error=error_msg)
            results.append(TokenHealthResult(
                token=masked_token, status="unhealthy", valid=False, error=error_msg
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
        "features": [
            "context_support", "streaming", "non_streaming",
            "token_health_check", "vless_proxy_pool", "debug_logging"
        ],
        "endpoints": {
            "chat_completions": "/v1/chat/completions",
            "models": "/v1/models",
            "health": "/health",
            "tokens_health": "/v1/tokens/health",
            "proxy_stats": "/v1/proxy/stats",
            "proxy_refresh": "/v1/proxy/refresh",
            "proxy_test": "/v1/proxy/test",
        }
    }


# ==================== Vless Proxy Management API ===================

class ProxyRefreshRequest(BaseModel):
    test_nodes: bool = True


class ProxyTestRequest(BaseModel):
    pattern: Optional[str] = None
    max_concurrent: int = 10


@app.get("/v1/proxy/stats")
async def proxy_stats():
    """Get proxy pool statistics"""
    global subscription_pool
    if subscription_pool is None:
        return {"enabled": False, "message": "Proxy pool not initialized"}
    try:
        stats = subscription_pool.get_stats()
        log_raw("DEBUG", "PROXY", f"stats requested: {json.dumps(stats)[:500]}")
        return {"enabled": True, "stats": stats}
    except Exception as e:
        log_exception("proxy_stats", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/proxy/refresh")
async def proxy_refresh(request: ProxyRefreshRequest,
                        background_tasks: BackgroundTasks):
    """Refresh subscriptions and test nodes"""
    global subscription_pool
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    try:
        log_raw("INFO", "PROXY", f"refresh requested  test_nodes={request.test_nodes}")
        result = await subscription_pool.refresh_subscriptions(
            test_nodes=request.test_nodes
        )
        log_raw("INFO", "PROXY", f"refresh done: {result}")
        return {"success": True, "result": result}
    except Exception as e:
        log_exception("proxy_refresh", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/proxy/test")
async def proxy_test(request: ProxyTestRequest):
    """Test proxy nodes"""
    global subscription_pool
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    try:
        tester = get_node_tester()
        await tester.init()
        results = await tester.test_all_available_nodes(pattern=request.pattern)
        summary = tester.get_test_summary(results)
        log_raw("INFO", "PROXY", f"node test summary: {summary}")
        return {
            "success": True,
            "summary": summary,
            "results": [
                {
                    "identifier": r.identifier,
                    "success": r.success,
                    "latency": r.latency,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except Exception as e:
        log_exception("proxy_test", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/proxy/nodes")
async def proxy_nodes(pattern: Optional[str] = None,
                      only_available: bool = True):
    """Get proxy node list"""
    global subscription_pool
    if subscription_pool is None:
        raise HTTPException(status_code=503, detail="Proxy pool not initialized")
    try:
        nodes = subscription_pool.get_available_nodes(pattern)
        if only_available:
            nodes = [n for n in nodes if n.is_available]
        log_raw("DEBUG", "PROXY",
                f"nodes list requested pattern={pattern} count={len(nodes)}")
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
                    "last_tested": n.last_tested,
                }
                for n in nodes
            ],
        }
    except Exception as e:
        log_exception("proxy_nodes", e)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

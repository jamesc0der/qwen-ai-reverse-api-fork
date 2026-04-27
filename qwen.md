# Qwen AI OpenAI Compatible API - Complete Project Documentation

> **Version**: 0.3.0 | **License**: MIT | **Python**: 3.8+

## 📋 Table of Contents

1. [Project Overview](#-project-overview)
2. [Architecture](#-architecture)
3. [File Structure](#-file-structure)
4. [Configuration](#-configuration)
5. [Core Modules](#-core-modules)
6. [API Endpoints](#-api-endpoints)
7. [Proxy System](#-proxy-system)
8. [Deployment Guide](#-deployment-guide)
9. [Vercel Free-Tier Compatibility](#-vercel-free-tier-compatibility)
10. [Code Reference](#-code-reference)

---

## 🎯 Project Overview

This project is a **reverse-engineered API wrapper** for Qwen AI (chat.qwen.ai) that provides an **OpenAI-compatible interface**. It allows users to interact with Qwen's AI models using the standard OpenAI SDK format.

### ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🔌 **OpenAI Compatible** | Drop-in replacement for OpenAI API endpoints |
| 🚀 **Streaming Support** | Real-time SSE streaming with low latency |
| 💬 **Context Management** | Multi-turn conversation support with session persistence |
| 🧠 **Thinking Process** | Exposes model reasoning/thinking content via `reasoning_content` field |
| 🎨 **Image Generation** | Supports Qwen's image generation tool calls |
| 🔄 **Token Rotation** | Multi-JWT token load balancing with random selection |
| ✅ **Health Checks** | Token validity verification endpoint |
| 🌐 **Vless Proxy Pool** | Advanced proxy management with subscription support |
| 📍 **Node Filtering** | Pattern-based node selection (e.g., "CF优选-电信") |
| 🔍 **Health Testing** | Automatic node availability and latency testing |
| 📊 **Proxy Management** | Complete REST API for proxy pool administration |

### ⚠️ Important Notice

> This project is a **reverse engineering** of Qwen AI's web API for **educational and research purposes only**. Please comply with Qwen AI's Terms of Service and avoid commercial use or large-scale requests.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Client Application                │
│  (OpenAI SDK / curl / custom HTTP client)           │
└─────────────────┬───────────────────────────────────┘
                  │ HTTP POST /v1/chat/completions
                  ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Server (server.py)              │
│  • Request validation & routing                      │
│  • Token selection (random from comma-separated)     │
│  • Session management (ChatSessionManager)           │
│  • Streaming/Non-streaming response handling         │
└─────────────────┬───────────────────────────────────┘
                  │
    ┌─────────────┴─────────────┐
    ▼                           ▼
┌─────────────┐      ┌─────────────────────┐
│ QwenAiClient│      │  Proxy System       │
│ (client.py) │      │  (vless_proxy.py)   │
└──────┬──────┘      └────────┬────────────┘
       │                      │
       ▼                      ▼
┌─────────────┐      ┌─────────────────────┐
│QwenAiAdapter│      │ Subscription Manager│
│ (adapter.py)│      │   (subscription.py) │
└──────┬──────┘      └────────┬────────────┘
       │                      │
       ▼                      ▼
┌─────────────┐      ┌─────────────────────┐
│HTTP Requests│      │  Node Storage       │
│ to chat.qwen.ai│    │  (node_storage.py)  │
└─────────────┘      └────────┬────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   Node Tester       │
                    │  (node_tester.py)   │
                    └─────────────────────┘
```

---

## 📁 File Structure

```
qwen-api/
├── 📄 README.md                    # Main documentation
├── 📄 LICENSE                      # MIT License
├── 📄 requirements.txt             # Python dependencies
├── 📄 .env.example                 # Environment variable template
├── 📄 .gitignore                   # Git ignore rules
├── 📄 proxy_config.example.txt     # Vless proxy configuration example
├── 📄 PROXY_SETUP.md              # Detailed proxy setup guide
├── 📄 example_reg.md              # Auto-registration logic reference
├── 📄 vless_nodes.json            # Local node storage (auto-generated)
├── 🐍 server.py                    # FastAPI application entry point
├── 🐍 start_server.py              # Server startup script with CLI args
│
├── 📁 docs/                        # Screenshot documentation
│   ├── 1.png                       # Token acquisition guide
│   ├── 2.png                       # Identity verification demo
│   ├── 3.png                       # Multi-turn conversation demo
│   └── 4.png                       # Image generation demo
│
├── 📁 qwen_ai/                     # Core Python SDK module
│   ├── 📄 __init__.py              # Module exports
│   ├── 📄 client.py                # OpenAI-compatible client interface
│   ├── 📄 adapter.py               # Qwen AI HTTP adapter (reverse API)
│   ├── 📄 stream_handler.py        # SSE stream parsing & OpenAI conversion
│   ├── 📄 tool_parser.py           # Function calling tool parsing
│   ├── 📄 vless_proxy.py           # Vless protocol proxy implementation
│   ├── 📄 proxy_adapter.py         # requests library Vless integration
│   ├── 📄 subscription.py          # Subscription URL parsing & node filtering
│   ├── 📄 node_storage.py          # JSON-based node persistence
│   └── 📄 node_tester.py           # Concurrent node health testing
│
├── 📁 .venv/                       # Python virtual environment (Windows)
│   ├── 📁 Lib/site-packages/       # Installed dependencies
│   └── 📁 Scripts/                 # Executable scripts
│
└── 📁 .git/                        # Git repository metadata
```

---

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# ==================== JWT Token Configuration ====================
# Your Qwen AI JWT tokens (comma-separated for rotation)
QWEN_TOKENS="token1,token2,token3"

# ==================== Vless Proxy Configuration ====================
# Enable proxy functionality
ENABLE_PROXY=false

# Subscription URLs (comma/semicolon/newline separated)
VLESS_SUBSCRIPTION_URLS="https://example.com/subscription1,https://example.com/subscription2"

# Node matching patterns (regex support)
VLESS_SUBSCRIPTION_PATTERNS="CF优选-电信"

# Auto-refresh subscriptions on startup
VLESS_AUTO_REFRESH_ON_START=true

# Node storage file path
VLESS_STORAGE_FILE="vless_nodes.json"

# ==================== Standard Proxy (Optional) ====================
HTTP_PROXY=""
HTTPS_PROXY=""
VLESS_PROXY_FILE=""  # File with one Vless URI per line
VLESS_PROXIES=""     # Direct Vless URIs (comma-separated)

# ==================== Server Configuration ====================
HOST="0.0.0.0"
PORT="8000"
DEBUG=false

# ==================== Chat Configuration ====================
# Auto-delete chat records after completion
AUTO_DELETE_CHAT=false
```

### Startup Script Options

```bash
python start_server.py [OPTIONS]

Options:
  --host HOST       Server bind address (default: 0.0.0.0)
  --port PORT       Server bind port (default: 8000)
  --reload          Enable auto-reload for development
  --no-proxy        Disable proxy even if configured in .env
```

---

## 🔧 Core Modules

### 1. `qwen_ai/client.py` - OpenAI Compatible Client

```python
class QwenAiClient:
    def __init__(self, token: str, cookies: Optional[str] = None)
    
    def chat_completions(
        model: str,
        messages: List[Dict],
        stream: bool = False,
        temperature: Optional[float] = None,
        tools: Optional[List[Dict]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
        auto_delete_chat: bool = False
    ) -> Union[Generator, Dict]
    
    def create_chat(model: str, title: str) -> str
    def delete_chat(chat_id: str) -> bool
    def delete_all_chats() -> bool
```

**Key Features:**
- Tool/function calling support via `tools_to_system_prompt()`
- Thinking mode toggle via `-thinking`/`-fast` model suffixes
- Automatic chat session management

### 2. `qwen_ai/adapter.py` - Qwen AI HTTP Adapter

```python
class QwenAiAdapter:
    QWEN_AI_BASE = 'https://chat.qwen.ai'
    
    # Model alias mapping
    MODEL_ALIASES = {
        'qwen': 'qwen3-max',
        'qwen3.5': 'qwen3.5-plus',
        'qwen3-coder': 'qwen3-coder-plus',
        # ... more aliases
    }
    
    def map_model(openai_model: str) -> str
    def create_chat(model_id: str, title: str) -> str
    def chat_completion(model, messages, stream, temperature, ...) -> Tuple[Response, chat_id, parent_id]
    def delete_chat(chat_id: str) -> bool
```

**Request Headers:**
- Custom browser-like headers to mimic web client
- JWT token in `Authorization: Bearer` header
- Anti-bot headers (`bx-v`, `bx-umidtoken`, `bx-ua`)

### 3. `qwen_ai/stream_handler.py` - SSE Stream Parser

Handles conversion of Qwen's SSE stream to OpenAI format:

```python
class QwenAiStreamHandler:
    def handle_stream(response) -> Generator[str, None, None]
    def handle_non_stream(response) -> Dict[str, Any]
    
    # Phase handling:
    # - 'think': Reasoning content → reasoning_content field
    # - 'thinking_summary': Summary thoughts
    # - 'answer': Final response content
    # - 'image_gen_tool': Image generation tool calls
```

**OpenAI Chunk Format:**
```json
{
  "id": "response_id",
  "object": "chat.completion.chunk",
  "created": 1234567890,
  "model": "qwen3.5-plus",
  "choices": [{
    "index": 0,
    "delta": {
      "role": "assistant",
      "content": "text",
      "reasoning_content": "thinking process"
    },
    "finish_reason": "stop"
  }]
}
```

### 3.5. `qwen_ai/tool_parser.py` - Tool Call Parsing

Supports multiple XML-based tool call formats used by Qwen AI:

```python
class ToolParser:
    @staticmethod
    def has_tool_use(content: str) -> bool
    @staticmethod
    def parse_tool_use(content: str) -> Optional[List[Dict]]
    @staticmethod
    def tools_to_system_prompt(tools: List[Dict]) -> str
```

**Supported Formats:**
1. **Standard XML:** `<function_calls><call:tool_name>{"arg": "value"}</call></function_calls>`
2. **Tool Use XML:** `<tool_use><n>tool_name</n><arguments>{"arg": "value"}</arguments></tool_use>`
3. **Simplified JSON:** `<tool_name>{"arg": "value"}</tool_name>`
4. **Nested Tags:** `<tool_name><param>value</param></tool_name>`
5. **XML Attributes:** `<tool_name attr="value"></tool_name>` *(new)*

**Example Attribute Format:**
```xml
<exit_plan_mode plan="Exit plan mode as requested by user"></exit_plan_mode>
```

### 4. `qwen_ai/vless_proxy.py` - Vless Protocol Implementation

```python
class VlessURI:
    # Parses: vless://uuid@host:port?security=tls&type=ws&path=/ws#remark
    
class VlessProxy:
    async def create_connection(target_host, target_port) -> (StreamReader, StreamWriter)
    async def test_connection() -> bool

class VlessProxyPool:
    def add_proxy(uri: str) -> bool
    def get_proxy(strategy='round_robin') -> VlessProxy
    async def test_all_proxies() -> Dict[str, bool]

class SubscriptionProxyPool:
    async def refresh_subscriptions(test_nodes=True) -> Dict
    def get_available_nodes(pattern) -> List[VlessNode]
    def get_random_node(pattern) -> Optional[VlessNode]
```

**Vless URI Format:**
```
vless://{uuid}@{address}:{port}?{params}#{remark}

Parameters:
  security=tls|reality|none    # Encryption type
  type=tcp|ws|grpc             # Transport protocol
  host=example.com             # HTTP Host header
  path=/websocket              # WebSocket path
  sni=example.com              # TLS SNI
  fp=chrome                    # TLS fingerprint
  pbk=PublicKey                # Reality public key
  sid=ShortID                  # Reality ShortID
```

### 5. `qwen_ai/subscription.py` - Subscription Management

```python
class VlessNode:
    uri: str           # Original Vless URI
    name: str          # Node remark/name
    address: str       # Server address
    port: int          # Server port
    network: str       # tcp/ws/grpc
    security: str      # tls/reality/none
    is_available: bool # Health status
    fail_count: int    # Consecutive failures
    success_count: int # Successful connections
    average_latency: float

class SubscriptionManager:
    async def fetch_all() -> Dict[str, List[VlessNode]]
    def get_nodes_by_pattern(pattern, only_available=True) -> List[VlessNode]
    def mark_node_result(identifier, success, latency)
```

**Pattern Matching:**
- Simple substring: `"CF优选-电信"` matches nodes containing this text
- Regex support: `".*香港.*"` matches nodes with "香港" anywhere in name

### 6. `qwen_ai/node_storage.py` - Persistent Node Storage

```python
class NodeStorage:
    async def load() -> Dict[str, VlessNode]
    async def save(nodes=None, force=False)
    async def merge_with_subscription(sub_nodes) -> (added, updated, removed)
    async def mark_node_result(identifier, success, latency)
    async def clean_expired(max_age_days=7)
```

**Storage Format (`vless_nodes.json`):**
```json
{
  "version": "1.0",
  "updated_at": "2026-04-23T21:29:07.526864",
  "nodes": {
    "host:port": {
      "uri": "vless://...",
      "name": "CF优选-电信-节点1",
      "address": "host",
      "port": 443,
      "is_available": true,
      "fail_count": 0,
      "success_count": 15,
      "average_latency": 120.5
    }
  }
}
```

### 7. `qwen_ai/node_tester.py` - Health Testing

```python
class NodeTester:
    async def test_node(node, target_url) -> TestResult
    async def test_nodes(nodes, progress_callback) -> List[TestResult]
    async def test_all_available_nodes(pattern) -> List[TestResult]
    async def get_recommended_nodes(pattern, min_success_rate, max_latency, limit)
```

**TestResult Dataclass:**
```python
@dataclass
class TestResult:
    identifier: str      # "host:port"
    success: bool        # Connection success
    latency: float       # Response time in ms
    error: Optional[str] # Error message if failed
    timestamp: float     # Test timestamp
```

---

## 🌐 API Endpoints

### Chat Completions

```http
POST /v1/chat/completions
Authorization: Bearer <JWT_TOKEN or TOKEN1,TOKEN2,TOKEN3>
Content-Type: application/json
```

**Request Body:**
```json
{
  "model": "qwen3.5-plus",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048,
  "top_p": 0.9,
  "tools": [...],
  "tool_choice": "auto",
  "chat_id": "optional-existing-chat-id"
}
```

**Response (Non-streaming):**
```json
{
  "id": "response_id",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "qwen3.5-plus",
  "chat_id": "new_chat_id",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Response text",
      "reasoning_content": "Thinking process (if enabled)"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  }
}
```

**Response (Streaming - SSE):**
```
data: {"id":"","model":"qwen3.5-plus","object":"chat.completion.chunk",...}

data: {"choices":[{"delta":{"role":"assistant"}}]}

data: {"choices":[{"delta":{"reasoning_content":"Let me think..."}}]}

data: {"choices":[{"delta":{"content":"Hello!"}}]}

data: {"choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Token Health Check

```http
POST /v1/tokens/health
Content-Type: application/json

{
  "tokens": "token1,token2,token3"
}
```

**Response:**
```json
{
  "total": 3,
  "healthy": 2,
  "unhealthy": 1,
  "results": [
    {
      "token": "eyJhbGci...abc",
      "status": "healthy",
      "valid": true,
      "error": null
    },
    {
      "token": "invalid...xyz",
      "status": "unhealthy",
      "valid": false,
      "error": "Token expired"
    }
  ]
}
```

### Proxy Management APIs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/proxy/stats` | GET | Get proxy pool statistics |
| `/v1/proxy/nodes` | GET | List available nodes |
| `/v1/proxy/refresh` | POST | Refresh subscriptions and test nodes |
| `/v1/proxy/test` | POST | Test specific nodes |

**Example: Refresh Subscriptions**
```bash
curl -X POST http://localhost:8000/v1/proxy/refresh \
  -H "Content-Type: application/json" \
  -d '{"test_nodes": true}'
```

**Example: Get Nodes by Pattern**
```bash
curl "http://localhost:8000/v1/proxy/nodes?pattern=CF优选-电信&only_available=true"
```

### Utility Endpoints

```http
GET /v1/models          # List supported models
GET /health             # Server health check
GET /                   # Service info and endpoint list
```

---

## 🔐 Proxy System Deep Dive

### Architecture Flow

```
┌─────────────────┐
│  .env Config    │
│  ENABLE_PROXY   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Subscription    │
│ URLs Parsed     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Base64 Decode   │
│ Vless URIs      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Pattern Filter  │
│ (CF优选-电信)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Concurrent Test │
│ (Google/CF/Qwen)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Update Storage  │
│ vless_nodes.json│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Random Selection│
│ for API Calls   │
└─────────────────┘
```

### Proxy Selection Strategy

1. **Round-Robin**: Sequential selection from healthy nodes
2. **Random**: Random selection from healthy nodes
3. **Health-Aware**: Automatically skip nodes with `fail_count >= 3`

### Health Testing Targets

```python
TEST_TARGETS = [
    ('https://www.google.com', 'Google'),
    ('https://www.cloudflare.com', 'Cloudflare'),
    ('https://chat.qwen.ai', 'Qwen AI'),  # Primary target
]
```

### Failure Handling

- **3 consecutive failures** → Mark node as unavailable
- **Successful request** → Reset failure counter
- **Latency tracking** → Average response time calculation
- **Auto-cleanup** → Remove nodes not seen in 7 days

---

## 🚀 Deployment Guide

### Local Development

```bash
# 1. Clone and setup
git clone <repo>
cd qwen-api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your JWT tokens

# 4. Start server
python start_server.py --reload --port 8000
```

### Docker Deployment

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "start_server.py", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t qwen-api .
docker run -p 8000:8000 --env-file .env qwen-api
```

### Production Considerations

1. **Rate Limiting**: Implement middleware to prevent abuse
2. **Logging**: Add structured logging for monitoring
3. **Metrics**: Expose Prometheus metrics for observability
4. **Secrets Management**: Use environment variables or secret managers
5. **Health Checks**: Configure load balancer health endpoints

---

## ☁️ Vercel Free-Tier Compatibility

### ❌ **NOT COMPATIBLE** - Cannot deploy to Vercel Free Tier

**Reasons:**

1. **Python Runtime Limitations**
   - Vercel's Python runtime is designed for serverless functions, not long-running servers
   - This project requires a persistent FastAPI/uvicorn server process

2. **WebSocket/Streaming Requirements**
   - The project uses Server-Sent Events (SSE) for streaming responses
   - Vercel's serverless functions have execution timeouts (10-60 seconds on free tier)
   - Streaming responses may be cut off before completion

3. **Background Tasks**
   - Proxy health testing runs async background tasks
   - Vercel serverless functions cannot maintain background processes

4. **File System Persistence**
   - The project writes to `vless_nodes.json` for node persistence
   - Vercel's file system is ephemeral; files are lost between invocations

5. **Network Restrictions**
   - Vercel free tier has egress limits and may block certain proxy protocols
   - Vless proxy connections may be flagged or throttled

6. **Memory/CPU Limits**
   - Free tier: 512MB RAM, limited CPU
   - Concurrent proxy testing could exceed these limits

### ✅ Alternative Deployment Options

| Platform | Free Tier | Suitable | Notes |
|----------|-----------|----------|-------|
| **Railway** | $5 credit/mo | ✅ Yes | Persistent containers, good for this project |
| **Render** | Free tier | ✅ Yes | Web services with always-on option |
| **Fly.io** | Free allowance | ✅ Yes | Global edge deployment, supports Python |
| **PythonAnywhere** | Free tier | ⚠️ Limited | Web app support, but may have proxy restrictions |
| **Heroku** | ❌ No free tier | ✅ Paid | Reliable but requires payment |
| **Self-hosted VPS** | Varies | ✅ Yes | Full control, but requires maintenance |

### If You Must Use Serverless...

**Partial compatibility with modifications:**

1. **Remove streaming**: Use only non-streaming responses
2. **Disable proxy features**: Set `ENABLE_PROXY=false`
3. **Use external storage**: Replace `vless_nodes.json` with Redis/Supabase
4. **Convert to serverless functions**: Rewrite endpoints as individual Vercel functions

**Example serverless adaptation:**
```python
# vercel_function.py
from fastapi import FastAPI, Request
from qwen_ai import QwenAiClient

app = FastAPI()

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    # Stateless, no background tasks
    # No file I/O, no persistent connections
    # Return response within 10 seconds
    pass
```

**Trade-offs:**
- ❌ No streaming support
- ❌ No proxy pool management
- ❌ No session persistence
- ❌ Limited to single-request lifecycle

---

## 📚 Code Reference

### Key Classes Summary

#### `ChatSessionManager` (server.py)
```python
class ChatSessionManager:
    """In-memory chat session storage with TTL"""
    def __init__(self, ttl_hours: int = 24)
    def get(chat_id: str) -> Optional[Dict]
    def set(chat_id: str, model: str, messages: List[Dict])
    def update_messages(chat_id: str, messages: List[Dict])
    def cleanup_expired()
```

#### `QwenAiAdapter` (adapter.py)
```python
class QwenAiAdapter:
    """HTTP adapter for chat.qwen.ai API"""
    
    # Default headers mimicking browser
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0...',
        'Authorization': 'Bearer {token}',
        'X-Request-Id': '{uuid}',
        # ... anti-bot headers
    }
    
    # Model name mapping
    MODEL_ALIASES = {
        'qwen3.5': 'qwen3.5-plus',
        # ...
    }
    
    def chat_completion(model, messages, stream, temperature, ...)
    # Returns: (requests.Response, chat_id, parent_id)
```

#### `VlessProxy` (vless_proxy.py)
```python
class VlessProxy:
    """Vless protocol client"""
    
    def __init__(self, uri: str)
    # Parses Vless URI into components
    
    async def create_connection(target_host, target_port)
    # Establishes Vless tunnel to target
    
    async def test_connection(target_host='google.com', target_port=443)
    # Tests proxy by making HTTP request through tunnel
```

#### `SubscriptionProxyPool` (vless_proxy.py)
```python
class SubscriptionProxyPool:
    """High-level proxy management with subscriptions"""
    
    async def init()
    # Initializes subscription manager, storage, tester
    
    async def refresh_subscriptions(test_nodes=True)
    # Fetches nodes from URLs, filters, tests, stores
    
    def get_random_node(pattern=None)
    # Returns healthy node matching pattern
    
    def get_stats()
    # Returns pool statistics for API
```

### Environment Variable Processing

```python
# start_server.py load_env()
def load_env():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"\'')
```

### Token Selection Logic

```python
# server.py select_random_token()
def select_random_token(token_string: str) -> str:
    """Select random token from comma-separated list"""
    tokens = [t.strip() for t in token_string.split(',') if t.strip()]
    if not tokens:
        raise ValueError("No valid tokens provided")
    return random.choice(tokens)
```

### Stream Parsing Logic

```python
# stream_handler.py handle_stream()
for line in response.iter_lines():
    if not line_str.startswith('data: '):
        continue
    data_str = line_str[6:]  # Remove 'data: ' prefix
    if data_str == '[DONE]':
        continue
    
    data = json.loads(data_str)
    
    # Handle different phases
    if delta.get('phase') == 'think':
        # Send reasoning_content
    elif delta.get('phase') == 'answer':
        # Send regular content
    elif delta.get('phase') == 'image_gen_tool':
        # Send tool_calls for image generation
```

---

## 🔧 Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `401 Unauthorized` | JWT token expired; get new token from chat.qwen.ai |
| `Connection timeout` | Check proxy configuration or network connectivity |
| `No healthy proxies` | Run `/v1/proxy/refresh` to update node list |
| `Stream cuts off` | Increase server timeout; check client SSE handling |
| `Token rotation not working` | Ensure tokens are comma-separated with no spaces |

### Debug Mode

```bash
# Enable debug logging
export DEBUG=true
python start_server.py --reload

# Check server logs for:
# [Proxy] Subscription pool initialized
# [Server] Auto-deleted chat: xxx
# [QwenAI] Stream error: xxx
```

### Testing Endpoints

```bash
# Health check
curl http://localhost:8000/health

# List models
curl http://localhost:8000/v1/models

# Test token
curl -X POST http://localhost:8000/v1/tokens/health \
  -H "Content-Type: application/json" \
  -d '{"tokens": "your_token_here"}'

# Simple chat test
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your_token" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5-plus","messages":[{"role":"user","content":"Hello"}]}'
```

---

## 📄 License

MIT License - See [LICENSE](LICENSE) file for details.

```
Copyright (c) 2026 吴基岩

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

> **Disclaimer**: This project is for educational purposes only. Use at your own risk and comply with all applicable terms of service.

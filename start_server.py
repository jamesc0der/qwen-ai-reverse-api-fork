#!/usr/bin/env python3
"""Start the OpenAI Compatible API Server for Qwen AI

Supports proxy functionality controlled by environment variables
"""

import argparse
import os
import sys
import asyncio
from pathlib import Path


def load_env():
    """Load environment variables"""
    env_file = Path(__file__).parent / ".env"
    
    if env_file.exists():
        print(f"[Config] Loading config file: {env_file}")
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'\'"')
                    if key and value:
                        os.environ[key] = value
                        if 'token' not in key.lower() and 'password' not in key.lower():
                            print(f"[Config] {key}={value[:50]}{'...' if len(value) > 50 else ''}")
    else:
        print("[Config] .env file not found, using default configuration")


async def init_proxy_pool():
    """Initialize proxy pool"""
    try:
        from qwen_ai.vless_proxy import init_subscription_pool_from_env
        
        print("[Proxy] Initializing subscription proxy pool...")
        pool = await init_subscription_pool_from_env()
        
        stats = pool.get_stats()
        print(f"[Proxy] Proxy pool initialized")
        print(f"[Proxy] Current pattern: {stats.get('pattern', 'N/A')}")
        print(f"[Proxy] Available nodes: {stats.get('current_pattern', {}).get('available', 0)}")
        
        return pool
    except Exception as e:
        print(f"[Proxy] Proxy pool initialization failed: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Start Qwen AI OpenAI Compatible API Server")
    parser.add_argument("--host", default=None, help="Host to bind (default: from env or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: from env or 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--no-proxy", action="store_true", help="Disable proxy even if configured")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Qwen AI OpenAI Compatible API Server")
    print("=" * 60)
    
    # Load environment variables
    load_env()
    
    # Get Configuration
    host = args.host or os.environ.get('HOST', '0.0.0.0')
    port = args.port or int(os.environ.get('PORT', '8000'))
    enable_proxy = os.environ.get('ENABLE_PROXY', 'false').lower() == 'true'
    
    # Check agent configuration
    if enable_proxy and not args.no_proxy:
        sub_urls = os.environ.get('VLESS_SUBSCRIPTION_URLS', '')
        if sub_urls:
            print(f"\n[Proxy] Proxy functionality is enabled")
            pattern = os.environ.get('VLESS_SUBSCRIPTION_PATTERNS', 'CF Optimized - Telecom')
            print(f"[Proxy] Matching rule: {pattern}")
            
            # Initialize the proxy pool
            try:
                asyncio.run(init_proxy_pool())
            except Exception as e:
                print(f"[Proxy] Warning: Proxy pool initialization failed: {e}")
        else:
            print("\n[Proxy] Warning: Proxy functionality is enabled but the subscription URL is not configured")
            print("[Proxy] Please configure VLESS_SUBSCRIPTION_URLS in the .env file")
    else:
        if args.no_proxy:
            print("\n[Proxy] Proxy functionality has been disabled via the --no-proxy parameter")
        else:
            print("\n[Proxy] Proxy functionality is not enabled")
            print("[Proxy] Set ENABLE_PROXY=true to enable proxy functionality")
    
    print(f"\n[Server] Start service...")
    print(f"[Server] Listening address: {host}:{port}")
    print(f"[Server] API documentation: http://{host}:{port}/docs")
    print(f"[Server] Health Check: http://{host}:{port}/health")
    print("=" * 60)
    
    import uvicorn
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=args.reload or os.environ.get('DEBUG', 'false').lower() == 'true',
        log_level="info"
    )


if __name__ == "__main__":
    main()
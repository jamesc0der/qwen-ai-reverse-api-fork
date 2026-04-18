#!/usr/bin/env python3
"""Start the OpenAI Compatible API Server for Qwen AI"""

import argparse
import os
import sys
import asyncio

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"[Startup] 已加载环境变量: {env_path}")
    else:
        print(f"[Startup] 未找到 .env 文件，使用默认配置")
except ImportError:
    print("[Startup] python-dotenv 未安装，跳过 .env 文件加载")

import uvicorn


async def init_proxy_pool():
    """初始化代理池"""
    try:
        from qwen_ai.vless_proxy import init_subscription_pool_from_env

        print("[Proxy] 初始化订阅代理池...")
        pool = await init_subscription_pool_from_env()

        stats = pool.get_stats()
        print(f"[Proxy] 代理池初始化完成")
        print(f"[Proxy] 当前规则: {stats.get('pattern', 'N/A')}")
        print(f"[Proxy] 可用节点: {stats.get('current_pattern', {}).get('available', 0)}")

        return pool
    except Exception as e:
        print(f"[Proxy] 代理池初始化失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Start Qwen AI OpenAI Compatible API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    args = parser.parse_args()

    print("=" * 60)
    print("Qwen AI OpenAI Compatible API Server")
    print("=" * 60)
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")

    # 检查是否启用代理
    proxy_enabled = os.environ.get('PROXY_ENABLED', 'false').lower() == 'true'
    sub_urls = os.environ.get('VLESS_SUBSCRIPTION_URLS', '')

    if proxy_enabled and sub_urls:
        print("\n[Proxy] 代理功能已启用")
        pattern = os.environ.get('VLESS_SUBSCRIPTION_PATTERNS', 'CF优选-电信')
        print(f"[Proxy] 匹配规则: {pattern}")

        # 初始化代理池
        asyncio.run(init_proxy_pool())
    elif proxy_enabled and not sub_urls:
        print("\n[Proxy] 警告: 代理已启用但未配置订阅URL")
        print("[Proxy] 请在 .env 文件中设置 VLESS_SUBSCRIPTION_URLS")
    else:
        print("\n[Proxy] 代理功能未启用")

    print(f"\nAPI Documentation: http://{args.host}:{args.port}/docs")
    print(f"Health Check: http://{args.host}:{args.port}/health")
    print(f"Admin Panel: http://{args.host}:{args.port}/admin")
    print("=" * 60)

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()

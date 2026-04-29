#!/usr/bin/env python3
"""Start the OpenAI Compatible API Server for Qwen AI

Supports proxy functionality and optional per-session debug file logging.

Usage:
    python start_server.py                   # normal mode
    python start_server.py --debug           # enable detailed debug log file
    python start_server.py --debug --port 9000
"""

import argparse
import os
import sys
import asyncio
from pathlib import Path


def load_env():
    """Load environment variables from .env file"""
    env_file = Path(__file__).parent / ".env"

    if env_file.exists():
        print(f"[Config] Loading config: {env_file}")
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    if key and value:
                        os.environ[key] = value
                        if 'token' not in key.lower() and 'password' not in key.lower():
                            print(
                                f"[Config] {key}="
                                f"{value[:50]}{'...' if len(value) > 50 else ''}"
                            )
    else:
        print("[Config] No .env file found, using defaults / environment")


async def init_proxy_pool():
    """Pre-initialize the subscription proxy pool before uvicorn starts"""
    try:
        from qwen_ai.vless_proxy import init_subscription_pool_from_env

        print("[Proxy] Initializing subscription proxy pool...")
        pool = await init_subscription_pool_from_env()

        stats = pool.get_stats()
        print("[Proxy] Proxy pool ready")
        print(f"[Proxy] Pattern : {stats.get('pattern', 'N/A')}")
        print(f"[Proxy] Available nodes: "
              f"{stats.get('current_pattern', {}).get('available', 0)}")
        return pool
    except Exception as e:
        print(f"[Proxy] Proxy pool init failed: {e}")
        return None


def _init_debug_logging(logs_dir: str = "logs"):
    """
    Enable per-session debug logging to file.
    Called before uvicorn starts so the session file is created immediately.
    The server.py startup_event reads QWEN_DEBUG_LOGGING env var to attach
    library loggers that are only available after the app is imported.
    """
    # Signal to server.py that debug is active
    os.environ["QWEN_DEBUG_LOGGING"] = "true"

    # Bootstrap the session file now (headers, PID, etc.)
    from qwen_ai.debug_logger import init_session_logging
    log_path = init_session_logging(debug=True, logs_dir=logs_dir)
    return log_path


def main():
    parser = argparse.ArgumentParser(
        description="Start Qwen AI OpenAI Compatible API Server"
    )
    parser.add_argument("--host", default=None,
                        help="Host to bind (default: env HOST or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None,
                        help="Port to bind (default: env PORT or 8000)")
    parser.add_argument("--reload", action="store_true",
                        help="Enable auto-reload for development")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Disable proxy even if configured")
    parser.add_argument("--debug", action="store_true",
                        help=(
                            "Enable detailed per-session debug logging to "
                            "logs/session_<timestamp>.log"
                        ))
    parser.add_argument("--logs-dir", default="logs",
                        help="Directory for debug log files (default: logs/)")

    args = parser.parse_args()

    print("=" * 60)
    print("Qwen AI OpenAI Compatible API Server")
    print("=" * 60)

    # Load .env
    load_env()

    # Determine settings
    host = args.host or os.environ.get('HOST', '0.0.0.0')
    port = args.port or int(os.environ.get('PORT', '8000'))
    enable_proxy = os.environ.get('ENABLE_PROXY', 'false').lower() == 'true'

    # ── Debug logging ──────────────────────────────────────────────────────
    # Priority: --debug flag > DEFAULT_DEBUG_LOGGING in server.py > env var
    debug_requested = (
        args.debug
        or os.environ.get("QWEN_DEBUG_LOGGING", "").lower() in ("1", "true", "yes")
    )

    # Also honour DEFAULT_DEBUG_LOGGING from server.py if importable
    if not debug_requested:
        try:
            from server import DEFAULT_DEBUG_LOGGING
            debug_requested = DEFAULT_DEBUG_LOGGING
        except Exception:
            pass

    if debug_requested:
        log_path = _init_debug_logging(logs_dir=args.logs_dir)
        print(f"\n[Debug] ✓ Debug logging ENABLED")
        print(f"[Debug]   Log file: {log_path.resolve()}")
        print(f"[Debug]   All requests, responses, stream chunks, tool calls,")
        print(f"[Debug]   proxy events and exceptions will be recorded.")
    else:
        print("\n[Debug] Debug logging disabled  "
              "(pass --debug or set DEFAULT_DEBUG_LOGGING=True in server.py)")

    # ── Proxy ─────────────────────────────────────────────────────────────
    if enable_proxy and not args.no_proxy:
        sub_urls = os.environ.get('VLESS_SUBSCRIPTION_URLS', '')
        if sub_urls:
            print(f"\n[Proxy] Proxy enabled")
            pattern = os.environ.get('VLESS_SUBSCRIPTION_PATTERNS', 'CF优选-电信')
            print(f"[Proxy] Pattern: {pattern}")
            try:
                asyncio.run(init_proxy_pool())
            except Exception as e:
                print(f"[Proxy] Warning: proxy pool pre-init failed: {e}")
        else:
            print("\n[Proxy] Warning: proxy enabled but VLESS_SUBSCRIPTION_URLS not set")
            print("[Proxy] Configure VLESS_SUBSCRIPTION_URLS in .env")
    else:
        if args.no_proxy:
            print("\n[Proxy] Proxy disabled via --no-proxy")
        else:
            print("\n[Proxy] Proxy not enabled  (set ENABLE_PROXY=true to enable)")

    # ── Start server ───────────────────────────────────────────────────────
    print(f"\n[Server] Starting...")
    print(f"[Server] Address : {host}:{port}")
    print(f"[Server] Docs    : http://{host}:{port}/docs")
    print(f"[Server] Health  : http://{host}:{port}/health")
    print("=" * 60)

    import uvicorn
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=args.reload or os.environ.get('DEBUG', 'false').lower() == 'true',
        log_level="info",
    )


if __name__ == "__main__":
    main()
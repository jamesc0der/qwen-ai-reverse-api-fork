"""Debug Logger - Per-session detailed file logging

Each session (server start) gets its own timestamped log file in logs/.
Only active when DEBUG_LOGGING=True (env) or --debug CLI flag is passed.
"""

import os
import sys
import json
import time
import logging
import traceback
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


# ── Module-level state ────────────────────────────────────────────────────────

_debug_enabled: bool = False
_session_log_file: Optional[Path] = None
_file_handler: Optional[logging.FileHandler] = None
_session_start: float = 0.0
_session_id: str = ""
_request_counter: int = 0
_request_counter_lock = threading.Lock()

# The root logger used for ALL file output in this session
_session_logger: Optional[logging.Logger] = None


# ── Public API ────────────────────────────────────────────────────────────────

def is_debug() -> bool:
    """Return True if debug logging is active."""
    return _debug_enabled


def init_session_logging(debug: bool = False, logs_dir: str = "logs") -> Optional[Path]:
    """
    Call once at server startup.  If debug=False this is a no-op.

    Args:
        debug:    Enable debug file logging.
        logs_dir: Directory where log files are written (created if missing).

    Returns:
        Path to the log file, or None if debug is disabled.
    """
    global _debug_enabled, _session_log_file, _file_handler
    global _session_start, _session_id, _session_logger

    _debug_enabled = debug
    if not debug:
        return None

    _session_start = time.time()
    _session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Create logs directory
    log_dir = Path(logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"session_{_session_id}.log"
    _session_log_file = log_path

    # ── Set up the dedicated session logger ──────────────────────────────────
    _session_logger = logging.getLogger(f"qwen_debug_{_session_id}")
    _session_logger.setLevel(logging.DEBUG)
    _session_logger.propagate = False  # don't bubble up to root logger

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _file_handler = logging.FileHandler(log_path, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(fmt)
    _session_logger.addHandler(_file_handler)

    # ── Also attach to every qwen_ai sub-logger ──────────────────────────────
    _attach_to_library_loggers()

    # ── Write session header ──────────────────────────────────────────────────
    _write_header()

    print(f"[Debug] Session log: {log_path.resolve()}", flush=True)
    return log_path


def close_session_logging():
    """Flush and close the file handler.  Call on server shutdown."""
    if not _debug_enabled or _session_logger is None:
        return
    _write_footer()
    if _file_handler:
        _file_handler.flush()
        _file_handler.close()


# ── Structured log helpers ────────────────────────────────────────────────────

def log_request_start(request_id: str, method: str, path: str,
                      headers: dict, body: Any) -> int:
    """Log an incoming HTTP request."""
    if not _debug_enabled:
        return 0

    with _request_counter_lock:
        global _request_counter
        _request_counter += 1
        req_num = _request_counter

    _log("INFO", "REQUEST", f"#{req_num} {method} {path}")
    _log("DEBUG", "REQUEST",
         f"#{req_num} headers={_safe_headers(headers)}")
    _log("DEBUG", "REQUEST",
         f"#{req_num} body={_safe_json(body)}")
    return req_num


def log_request_end(req_num: int, status: int, elapsed_ms: float,
                    extra: str = ""):
    if not _debug_enabled:
        return
    _log("INFO", "REQUEST",
         f"#{req_num} → {status}  [{elapsed_ms:.1f} ms] {extra}")


def log_chat_create(req_num: int, model: str, chat_id: str):
    if not _debug_enabled:
        return
    _log("INFO", "QWEN_CHAT",
         f"#{req_num} created chat_id={chat_id}  model={model}")


def log_chat_delete(chat_id: str, success: bool):
    if not _debug_enabled:
        return
    status = "OK" if success else "FAILED"
    _log("INFO", "QWEN_CHAT", f"delete chat_id={chat_id}  status={status}")


def log_stream_chunk(req_num: int, phase: Optional[str],
                     content: str, status: Optional[str] = None):
    """Log a single SSE data chunk from Qwen."""
    if not _debug_enabled:
        return
    snippet = content[:200].replace("\n", "\\n") if content else ""
    _log("DEBUG", "STREAM",
         f"#{req_num} phase={phase} status={status} content={snippet!r}")


def log_tool_detected(req_num: int, raw_content: str):
    if not _debug_enabled:
        return
    _log("INFO", "TOOL_PARSE",
         f"#{req_num} tool-use detected in content "
         f"(len={len(raw_content)})")
    _log("DEBUG", "TOOL_PARSE",
         f"#{req_num} raw_content={raw_content[:500]!r}")


def log_tool_parsed(req_num: int, tool_calls: list):
    if not _debug_enabled:
        return
    _log("INFO", "TOOL_PARSE",
         f"#{req_num} parsed {len(tool_calls)} tool call(s)")
    for i, tc in enumerate(tool_calls):
        _log("DEBUG", "TOOL_PARSE",
             f"#{req_num} call[{i}] name={tc.get('function', {}).get('name')} "
             f"args={tc.get('function', {}).get('arguments', '')[:300]!r}")


def log_proxy_selected(proxy_id: str, strategy: str):
    if not _debug_enabled:
        return
    _log("DEBUG", "PROXY", f"selected {proxy_id}  strategy={strategy}")


def log_proxy_result(proxy_id: str, success: bool,
                     latency_ms: float = 0, error: str = ""):
    if not _debug_enabled:
        return
    if success:
        _log("DEBUG", "PROXY",
             f"{proxy_id} OK  latency={latency_ms:.1f} ms")
    else:
        _log("WARNING", "PROXY",
             f"{proxy_id} FAIL  error={error}")


def log_exception(context: str, exc: BaseException):
    if not _debug_enabled:
        return
    tb = traceback.format_exc()
    _log("ERROR", "EXCEPTION",
         f"{context}: {type(exc).__name__}: {exc}\n{tb}")


def log_token_health(masked_token: str, valid: bool, error: str = ""):
    if not _debug_enabled:
        return
    status = "healthy" if valid else f"unhealthy ({error})"
    _log("INFO", "TOKEN_HEALTH", f"{masked_token}  → {status}")


def log_raw(level: str, component: str, message: str):
    """Generic log entry — use for anything not covered above."""
    if not _debug_enabled:
        return
    _log(level.upper(), component, message)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _log(level: str, component: str, message: str):
    """Write one line to the session log file."""
    if _session_logger is None:
        return
    logger = _session_logger.getChild(component)
    lvl = getattr(logging, level, logging.DEBUG)
    logger.log(lvl, message)


def _write_header():
    sep = "=" * 80
    lines = [
        sep,
        f"  QWEN AI DEBUG SESSION  —  {datetime.now().isoformat()}",
        f"  Session ID : {_session_id}",
        f"  PID        : {os.getpid()}",
        f"  Python     : {sys.version.split()[0]}",
        f"  Log file   : {_session_log_file}",
        sep,
    ]
    for line in lines:
        _session_logger.info(line)


def _write_footer():
    elapsed = time.time() - _session_start
    sep = "=" * 80
    _session_logger.info(sep)
    _session_logger.info(
        f"  SESSION END  —  uptime {elapsed:.1f}s  "
        f"requests handled: {_request_counter}"
    )
    _session_logger.info(sep)


def _attach_to_library_loggers():
    """
    Forward all qwen_ai.* and uvicorn / fastapi loggers to our file handler.
    """
    if _file_handler is None:
        return

    for name in [
        "qwen_ai",
        "qwen_ai.adapter",
        "qwen_ai.stream_handler",
        "qwen_ai.tool_parser",
        "qwen_ai.vless_proxy",
        "qwen_ai.proxy_adapter",
        "qwen_ai.subscription",
        "qwen_ai.node_storage",
        "qwen_ai.node_tester",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
    ]:
        lib_logger = logging.getLogger(name)
        # Only add if not already attached (avoid duplicates on reload)
        if _file_handler not in lib_logger.handlers:
            lib_logger.addHandler(_file_handler)
            lib_logger.setLevel(logging.DEBUG)


def _safe_headers(headers: Any) -> str:
    """Return headers dict as string, masking Authorization values."""
    try:
        d = dict(headers)
        for k in list(d.keys()):
            if k.lower() in ("authorization", "cookie", "x-api-key"):
                v = str(d[k])
                d[k] = v[:12] + "…" + v[-6:] if len(v) > 18 else "***"
        return json.dumps(d, ensure_ascii=False)
    except Exception:
        return str(headers)


def _safe_json(obj: Any) -> str:
    """Serialize obj to JSON string, truncating if very large."""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        if len(s) > 4000:
            return s[:4000] + "  … [truncated]"
        return s
    except Exception:
        return str(obj)[:2000]
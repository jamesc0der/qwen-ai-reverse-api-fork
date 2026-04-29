"""Proxy Adapter - Add Vless proxy support to the requests library"

Support Vless proxy by creating a custom HTTPAdapter.
"""

import asyncio
import socket
import ssl
import threading
import queue
from typing import Optional, Dict, Any, Callable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from .debug_logger import log_raw, log_exception, log_proxy_selected, log_proxy_result
from urllib3.util.connection import create_connection
from urllib3.util.ssl_ import create_urllib3_context

from .vless_proxy import VlessProxy, VlessProxyPool, get_proxy_pool, init_proxy_pool_from_env


class VlessProxyConnection:
    "Vless Proxy Connection Wrapper"
    
    def __init__(self, proxy: VlessProxy, target_host: str, target_port: int):
        self.proxy = proxy
        self.target_host = target_host
        self.target_port = target_port
        self._socket: Optional[socket.socket] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
    
    def connect(self, timeout: float = 30) -> socket.socket:
        """
        Establish a Vless proxy connection and return a socket.
        
        Returns:
            Connected socket
        """
        if self._connected:
            return self._socket
        
        # Create an event loop to run in a thread
        result_queue = queue.Queue()
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            
            try:
                reader, writer = loop.run_until_complete(
                    asyncio.wait_for(
                        self.proxy.create_connection(self.target_host, self.target_port),
                        timeout=timeout
                    )
                )
                
                self._reader = reader
                self._writer = writer
                
                # Get the underlying socket
                transport = writer.transport
                if hasattr(transport, 'get_extra_info'):
                    sock = transport.get_extra_info('socket')
                    if sock:
                        self._socket = sock
                    else:
                        # For SSL transports, obtain the raw socket.
                        sock = transport.get_extra_info('ssl_object')
                        if sock:
                            self._socket = sock
                
                # If the socket is not obtained, create a wrapper.
                if self._socket is None:
                    self._socket = VlessSocketWrapper(reader, writer)
                
                self._connected = True
                result_queue.put(('success', None))
                
                # Keep the event loop running
                loop.run_forever()
                
            except Exception as e:
                result_queue.put(('error', e))
            finally:
                loop.close()
        
        self._thread = threading.Thread(target=run_async, daemon=True)
        self._thread.start()
        
        # Waiting for connection results
        status, error = result_queue.get(timeout=timeout + 5)
        if status == 'error':
            raise ConnectionError(f'Failed to establish Vless connection: {error}')
        
        return self._socket
    
    def close(self):
        """Close connection"""
        if self._writer:
            try:
                self._writer.close()
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._writer.wait_closed(),
                        self._loop
                    )
            except:
                pass
        
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        self._connected = False


class VlessSocketWrapper:
    """Vless Socket Wrapper - Wraps asyncio StreamReader/Writer as a socket-like object"""
    
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._loop = asyncio.get_event_loop()
        self._closed = False
        
        # Configure socket options
        self.family = socket.AF_INET
        self.type = socket.SOCK_STREAM
    
    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        """Received data"""
        if self._closed:
            return b''
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._reader.read(bufsize),
                self._loop
            )
            return future.result(timeout=30)
        except Exception as e:
            raise socket.error(f'Recv error: {e}')
    
    def recv_into(self, buffer: bytearray, nbytes: int = 0, flags: int = 0) -> int:
        """Receive data into buffer"""
        if self._closed:
            return 0
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._reader.read(nbytes or len(buffer)),
                self._loop
            )
            data = future.result(timeout=30)
            buffer[:len(data)] = data
            return len(data)
        except Exception as e:
            raise socket.error(f'Recv error: {e}')
    
    def send(self, data: bytes, flags: int = 0) -> int:
        """Sending data"""
        if self._closed:
            raise socket.error('Socket is closed')
        
        try:
            self._writer.write(data)
            future = asyncio.run_coroutine_threadsafe(
                self._writer.drain(),
                self._loop
            )
            future.result(timeout=30)
            return len(data)
        except Exception as e:
            raise socket.error(f'Send error: {e}')
    
    def sendall(self, data: bytes, flags: int = 0):
        """Send all data"""
        self.send(data, flags)
    
    def close(self):
        """Close the socket"""
        self._closed = True
        try:
            self._writer.close()
        except:
            pass
    
    def settimeout(self, timeout: float):
        """Set timeout (not implemented, handled externally)"""
        pass
    
    def setblocking(self, flag: bool):
        """Set blocking mode"""
        pass
    
    def shutdown(self, how: int):
        """Part of closing the socket"""
        pass
    
    def fileno(self) -> int:
        """Returns file descriptor"""
        return -1
    
    def getpeername(self):
        """Get peer address"""
        return ('0.0.0.0', 0)
    
    def getsockname(self):
        """Get local address"""
        return ('0.0.0.0', 0)


class VlessHTTPAdapter(HTTPAdapter):
    """
    HTTP Adapter that supports Vless proxy
    
    How to use:
        session = requests.Session()
        adapter = VlessHTTPAdapter(proxy_pool=pool)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
    """
    
    def __init__(self, proxy_pool: Optional[VlessProxyPool] = None, 
                 proxy_strategy: str = 'round_robin',
                 max_retries: int = 3,
                 **kwargs):
        """
        Initialize Vless HTTP Adapter
        
        Args:
            proxy_pool: Vless proxy pool; if set to None, the global proxy pool will be used.
            proxy_strategy: Proxy selection strategy ('round_robin' or 'random')
            max_retries: Maximum number of retries
        """
        self.proxy_pool = proxy_pool or get_proxy_pool()
        self.proxy_strategy = proxy_strategy
        self.max_retries = max_retries
        super().__init__(**kwargs)
    
    def get_connection(self, url: str, proxies: Optional[Dict[str, str]] = None):
        """
        Get connection

        If a Vless proxy is configured, use the proxy to connect.
        """
        # Check if a Vless proxy is available
        proxy = self.proxy_pool.get_proxy(self.proxy_strategy) if self.proxy_pool.count > 0 else None

        if proxy:
            # Using Vless proxy
            parsed = urlparse(url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            log_proxy_selected(proxy.identifier, self.proxy_strategy)

            try:
                log_raw("DEBUG", "PROXY_ADAPTER", f"Creating Vless connection to {host}:{port} via {proxy.identifier}")
                conn = VlessProxyConnection(proxy, host, port)
                sock = conn.connect()

                # Mark proxy usage successful
                proxy.mark_success()
                log_proxy_result(proxy.identifier, True)

                # Returns a wrapped link
                return VlessConnectionWrapper(sock, conn, parsed.scheme == 'https')
                
            except Exception as e:
                proxy.mark_fail()
                log_proxy_result(proxy.identifier, False, error=str(e))
                raise ConnectionError(f'Vless proxy connection failed: {e}')

        # No proxy, using default connection
        log_raw("DEBUG", "PROXY_ADAPTER", f"No proxy available, using direct connection to {urlparse(url).hostname}")
        return super().get_connection(url, proxies)
    
    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        """Send Request"""
        # If Vless proxy is enabled, disable urllib3 proxy handling.
        if self.proxy_pool.count > 0:
            proxies = None
        
        return super().send(request, stream, timeout, verify, cert, proxies)


class VlessConnectionWrapper:
    "Vless Connector Wrapper - Adapts to urllib3 Connector Interface"
    
    def __init__(self, sock: socket.socket, conn: VlessProxyConnection, is_https: bool):
        self.sock = sock
        self._vless_conn = conn
        self.is_https = is_https
        self._ssl_context: Optional[ssl.SSLContext] = None
    
    def connect(self):
        """Connected (Already connected, return directly)"""
        return self
    
    def close(self):
        """Close connection"""
        self._vless_conn.close()
        try:
            self.sock.close()
        except:
            pass
    
    def send(self, data: bytes):
        """Sending data"""
        return self.sock.send(data)
    
    def recv(self, amt: int) -> bytes:
        """Received data"""
        return self.sock.recv(amt)
    
    def settimeout(self, timeout: float):
        """Set Timeout"""
        self.sock.settimeout(timeout)
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


class ProxyManager:
    """Proxy Manager - Unified Management of Various Proxy Types"""
    
    def __init__(self):
        self.vless_pool: Optional[VlessProxyPool] = None
        self.http_proxy: Optional[str] = None
        self.https_proxy: Optional[str] = None
        self._adapter: Optional[VlessHTTPAdapter] = None
    
    def init_from_env(self) -> 'ProxyManager':
        """Initialize from environment variables."""
        import os
        
        # Initialize the Vless proxy pool
        self.vless_pool = init_proxy_pool_from_env()
        
        # Read HTTP proxy settings
        self.http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
        self.https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        
        return self
    
    def init_vless_from_file(self, filepath: str) -> 'ProxyManager':
        """Load Vless proxy from file."""
        if self.vless_pool is None:
            self.vless_pool = get_proxy_pool()
        self.vless_pool.add_proxies_from_file(filepath)
        return self
    
    def add_vless_proxy(self, uri: str) -> bool:
        """Add a single Vless agent."""
        if self.vless_pool is None:
            self.vless_pool = get_proxy_pool()
        return self.vless_pool.add_proxy(uri)
    
    def get_requests_proxies(self) -> Optional[Dict[str, str]]:
        """
        Get the proxy configuration used by the requests library.
        
        Returns:
            Proxy configuration dictionary or None
        """
        proxies = {}
        
        if self.http_proxy:
            proxies['http'] = self.http_proxy
        if self.https_proxy:
            proxies['https'] = self.https_proxy
        
        return proxies if proxies else None
    
    def create_session(self, use_vless: bool = True) -> requests.Session:
        """
        Create a requests session with a proxy configured.

        Args:
            use_vless: Whether to use a Vless proxy

        Returns:
            Configured Session
        """
        log_raw("DEBUG", "PROXY_MANAGER", f"Creating session with use_vless={use_vless}")
        session = requests.Session()

        if use_vless and self.vless_pool and self.vless_pool.count > 0:
            # Using Vless proxy
            log_raw("DEBUG", "PROXY_MANAGER", f"Using Vless proxy pool with {self.vless_pool.count} proxies")
            adapter = VlessHTTPAdapter(proxy_pool=self.vless_pool)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            self._adapter = adapter
        else:
            # Using a regular HTTP proxy
            proxies = self.get_requests_proxies()
            if proxies:
                log_raw("DEBUG", "PROXY_MANAGER", f"Using HTTP proxy: {proxies}")
                session.proxies = proxies
            else:
                log_raw("DEBUG", "PROXY_MANAGER", "Using direct connection (no proxy)")

        return session
    
    def get_stats(self) -> Dict[str, Any]:
        """Get Agent Statistics"""
        stats = {
            'http_proxy': self.http_proxy,
            'https_proxy': self.https_proxy,
        }
        
        if self.vless_pool:
            stats['vless'] = self.vless_pool.get_stats()
        else:
            stats['vless'] = {'total': 0, 'healthy': 0, 'unhealthy': 0, 'proxies': []}
        
        return stats


# Global Proxy Manager
_global_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    """Get Global Proxy Manager"""
    global _global_proxy_manager
    if _global_proxy_manager is None:
        _global_proxy_manager = ProxyManager()
    return _global_proxy_manager


def init_proxy_manager() -> ProxyManager:
    """Initialize the global proxy manager (from environment variables)"""
    global _global_proxy_manager
    _global_proxy_manager = ProxyManager().init_from_env()
    return _global_proxy_manager
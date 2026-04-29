"""Vless Proxy Client - Supports v2ray-compatible Vless protocol

Supports TCP and WebSocket transport methods for Vless protocol
"""

import json
import base64
import hashlib
import hmac
import struct
import socket
import ssl
import asyncio
import random
import string
import os
from typing import Optional, Dict, Any, Tuple, Union, List
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger(__name__)

from .debug_logger import log_raw, log_exception, log_proxy_selected, log_proxy_result


class VlessURI:
    """Parsing Vless URI"""
    
    def __init__(self, uri: str):
        self.uri = uri
        self.uuid: Optional[str] = None
        self.address: Optional[str] = None
        self.port: Optional[int] = None
        self.security: str = 'none'
        self.network: str = 'tcp'
        self.host: Optional[str] = None
        self.path: Optional[str] = None
        self.tls: bool = False
        self.sni: Optional[str] = None
        self.alpn: Optional[str] = None
        self.fp: Optional[str] = None
        self.pbk: Optional[str] = None
        self.sid: Optional[str] = None
        self.spx: Optional[str] = None
        self._parse()
    
    def _parse(self):
        """Parsing Vless URI"""
        try:
            # vless://uuid@address:port?params#remark
            if not self.uri.startswith('vless://'):
                raise ValueError('Invalid Vless URI format')
            
            # Remove the vless:// prefix
            content = self.uri[8:]
            
            # Separate notes
            if '#' in content:
                content, _ = content.split('#', 1)
            
            # Separate parameters
            if '?' in content:
                main_part, params_part = content.split('?', 1)
            else:
                main_part = content
                params_part = ''
            
            # Parse the main body: uuid@address:port
            if '@' not in main_part:
                raise ValueError('Invalid Vless URI: missing @')
            
            uuid_part, server_part = main_part.split('@', 1)
            self.uuid = uuid_part
            
            # Resolve server address and port
            if ':' not in server_part:
                raise ValueError('Invalid Vless URI: missing port')
            
            # Handling IPv6 addresses
            if server_part.startswith('['):
                end_idx = server_part.find(']')
                if end_idx == -1:
                    raise ValueError('Invalid Vless URI: invalid IPv6 address')
                self.address = server_part[1:end_idx]
                port_part = server_part[end_idx + 1:]
                if port_part.startswith(':'):
                    self.port = int(port_part[1:])
                else:
                    raise ValueError('Invalid Vless URI: missing port after IPv6')
            else:
                addr_part, port_part = server_part.rsplit(':', 1)
                self.address = addr_part
                self.port = int(port_part)
            
            # Parse parameters
            if params_part:
                params = parse_qs(params_part)
                
                self.security = params.get('security', ['none'])[0]
                self.network = params.get('type', ['tcp'])[0]
                self.host = params.get('host', [None])[0]
                self.path = params.get('path', ['/'])[0]
                self.sni = params.get('sni', [None])[0]
                self.alpn = params.get('alpn', [None])[0]
                self.fp = params.get('fp', [None])[0]
                self.pbk = params.get('pbk', [None])[0]
                self.sid = params.get('sid', [None])[0]
                self.spx = params.get('spx', [None])[0]
                
                if self.security in ['tls', 'xtls', 'reality']:
                    self.tls = True
                
        except Exception as e:
            raise ValueError(f'Failed to parse Vless URI: {e}')
    
    def __repr__(self):
        return f"VlessURI({self.address}:{self.port}, network={self.network}, tls={self.tls})"


class VlessProxy:
    """Vless Proxy Client"""
    
    # Vless protocol constants
    VERSION = 0
    COMMAND_TCP = 1
    COMMAND_UDP = 2
    COMMAND_MUX = 3
    
    # Address Type
    ADDR_TYPE_IPV4 = 1
    ADDR_TYPE_DOMAIN = 2
    ADDR_TYPE_IPV6 = 3
    
    def __init__(self, uri: str):
        """
        Initialize Vless proxy
        
        Args:
            uri: Vless URI, format: vless://uuid@address:port?params#remark
        """
        self.config = VlessURI(uri)
        self._lock = asyncio.Lock()
        self._last_used = 0
        self._fail_count = 0
        self._healthy = True
    
    @property
    def is_healthy(self) -> bool:
        "Check if the agent is healthy."
        return self._healthy and self._fail_count < 3
    
    @property
    def identifier(self) -> str:
        """Get Proxy Identifier"""
        return f"{self.config.address}:{self.config.port}"
    
    def mark_success(self):
        """The tag request was successful ..."""
        self._fail_count = 0
        self._healthy = True
        self._last_used = asyncio.get_event_loop().time()
    
    def mark_fail(self):
        """Failed to mark"""
        self._fail_count += 1
        if self._fail_count >= 3:
            self._healthy = False
    
    def _make_request_header(self, target_host: str, target_port: int) -> bytes:
        """
        Build Vless request headers
        
        Protocol format:
        +------------------+------------------+--------------------------------+
        |      1 Byte      |     16 Bytes     |           M Bytes              |
        +------------------+------------------+--------------------------------+
        |      Version     |      UUID        |          Request Header        |
        +------------------+------------------+--------------------------------+
        
        Request Header:
        +------------------+------------------+---------------+------------------+
        |      1 Byte      |      1 Byte      |    1 Byte     |    S Bytes       |
        +------------------+------------------+---------------+------------------+
        |      Command     |   Address Type   |  Address      |     Port         |
        +------------------+------------------+---------------+------------------+
        """
        # Verify UUID
        try:
            uuid_bytes = bytes.fromhex(self.config.uuid.replace('-', ''))
            if len(uuid_bytes) != 16:
                raise ValueError('Invalid UUID length')
        except Exception as e:
            raise ValueError(f'Invalid UUID format: {e}')
        
        # Build request headers
        header = bytearray()
        
        # Version (1 byte)
        header.append(self.VERSION)
        
        # UUID (16 bytes)
        header.extend(uuid_bytes)
        
        # Command (1 byte) - TCP
        header.append(self.COMMAND_TCP)
        
        # Address Type and Address
        try:
            # Trying as IPv4
            socket.inet_pton(socket.AF_INET, target_host)
            header.append(self.ADDR_TYPE_IPV4)
            header.extend(socket.inet_pton(socket.AF_INET, target_host))
        except OSError:
            try:
                # Trying as IPv6
                socket.inet_pton(socket.AF_INET6, target_host)
                header.append(self.ADDR_TYPE_IPV6)
                header.extend(socket.inet_pton(socket.AF_INET6, target_host))
            except OSError:
                # As a domain name
                domain_bytes = target_host.encode('utf-8')
                if len(domain_bytes) > 255:
                    raise ValueError('Domain name too long')
                header.append(self.ADDR_TYPE_DOMAIN)
                header.append(len(domain_bytes))
                header.extend(domain_bytes)
        
        # Port (2 bytes, big-endian)
        header.extend(struct.pack('>H', target_port))
        
        return bytes(header)
    
    async def create_connection(self, target_host: str, target_port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """
        Create a connection to the target host via the Vless proxy.
        
        Args:
            target_host: Target host address
            target_port: Target host port
            
        Returns:
            (reader, writer) tuple
        """
        try:
            # Connect to Vless server
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.address, self.config.port),
                timeout=10
            )
            
            # If TLS is enabled, wrap the connection
            if self.config.tls:
                ssl_context = ssl.create_default_context()
                if self.config.sni:
                    ssl_context.server_hostname = self.config.sni
                
                # Create an SSL connection
                loop = asyncio.get_event_loop()
                transport = writer.transport
                protocol = transport.get_protocol()
                
                # Upgrade connection to SSL
                ssl_transport = await loop.start_tls(
                    transport, protocol, ssl_context,
                    server_hostname=self.config.sni or self.config.address
                )
                
                # Get a new reader and writer
                reader = asyncio.StreamReader()
                reader.set_transport(ssl_transport)
                writer = asyncio.StreamWriter(ssl_transport, protocol, reader, loop)
            
            # Send Vless request header
            request_header = self._make_request_header(target_host, target_port)
            writer.write(request_header)
            await writer.drain()
            
            # Read the response (Vless protocol response is empty or contains status)
            # The Vless protocol does not respond upon success and immediately begins transmitting data.
            
            return reader, writer
            
        except asyncio.TimeoutError:
            raise ConnectionError(f'Connection to Vless server {self.config.address}:{self.config.port} timed out')
        except Exception as e:
            raise ConnectionError(f'Failed to create Vless connection: {e}')
    
    async def test_connection(self, target_host: str = 'www.google.com', target_port: int = 443, timeout: int = 10) -> bool:
        """
        Test proxy connection

        Args:
            target_host: The target host for testing
            target_port: The target port for testing
            timeout: Timeout duration (seconds)

        Returns:
            Is the connection successful?
        """
        log_raw("DEBUG", "VLESS_PROXY", f"Testing connection to {target_host}:{target_port} via {self.identifier}")
        try:
            reader, writer = await asyncio.wait_for(
                self.create_connection(target_host, target_port),
                timeout=timeout
            )
            
            # Send a simple HTTP request to verify the connection.
            http_request = f'HEAD / HTTP/1.1\r\nHost: {target_host}\r\nConnection: close\r\n\r\n'
            writer.write(http_request.encode())
            await writer.drain()
            
            # Attempt to read the response
            response = await asyncio.wait_for(reader.read(1024), timeout=5)
            
            writer.close()
            await writer.wait_closed()
            
            if response:
                self.mark_success()
                log_proxy_result(self.identifier, True)
                return True
            log_proxy_result(self.identifier, False, error="No response received")
            return False

        except Exception as e:
            log_proxy_result(self.identifier, False, error=str(e))
            self.mark_fail()
            return False


class VlessProxyPool:
    "Vless Proxy Pool - Managing Multiple Vless Proxy Pools"
    
    def __init__(self):
        self._proxies: list[VlessProxy] = []
        self._current_index = 0
        self._lock = asyncio.Lock()
    
    def add_proxy(self, uri: str) -> bool:
        """
        Add Vless proxy to pool
        
        Args:
            hunger: Vless URI
            
        Returns:
            Was the addition successful?
        """
        try:
            proxy = VlessProxy(uri)
            self._proxies.append(proxy)
            log_raw("INFO", "VLESS_POOL", f'Added Vless proxy: {proxy.identifier}')
            return True
        except Exception as e:
            log_raw("ERROR", "VLESS_POOL", f'Failed to add Vless proxy: {e}')
            return False
    
    def add_proxies_from_uris(self, uris: list[str]) -> Tuple[int, int]:
        """
        Add a proxy from multiple URIs
        
        Args:
            uris: Vless URI list
            
        Returns:
            (Number of successes, number of failures)
        """
        success = 0
        failed = 0
        for uri in uris:
            if self.add_proxy(uri.strip()):
                success += 1
            else:
                failed += 1
        return success, failed
    
    def add_proxies_from_env(self, env_var: str = 'VLESS_PROXIES') -> Tuple[int, int]:
        """
        Add an agent from environment variables
        
        Args:
            env_var: Environment variable name
            
        Returns:
            (Number of successes, number of failures)
        """
        import os
        uris_str = os.environ.get(env_var, '')
        if not uris_str:
            return 0, 0
        
        # Supports multiple separators: newline, comma, semicolon
        uris = []
        for separator in ['\n', ',', ';']:
            if separator in uris_str:
                uris = [u.strip() for u in uris_str.split(separator) if u.strip()]
                break
        
        if not uris:
            uris = [uris_str.strip()]
        
        return self.add_proxies_from_uris(uris)
    
    def add_proxies_from_file(self, filepath: str) -> Tuple[int, int]:
        """
        Add agent from file
        
        Args:
            filepath: File path, one Vless URI per line.
            
        Returns:
            (Number of successes, number of failures)
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                uris = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            return self.add_proxies_from_uris(uris)
        except Exception as e:
            logger.error(f'Failed to read proxy file {filepath}: {e}')
            return 0, 0
    
    @property
    def count(self) -> int:
        """Get the total number of agents"""
        return len(self._proxies)
    
    @property
    def healthy_count(self) -> int:
        """Acquire the number of health agents"""
        return sum(1 for p in self._proxies if p.is_healthy)
    
    def get_proxy(self, strategy: str = 'round_robin') -> Optional[VlessProxy]:
        """
        Get a proxy
        
        Args:
            strategy: Choose a strategy ('round_robin' or 'random')
            
        Returns:
            VlessProxy instance or None
        """
        if not self._proxies:
            return None
        
        # Filter Health Agent
        healthy_proxies = [p for p in self._proxies if p.is_healthy]
        if not healthy_proxies:
            # If you don't have a healthy agent, try using all agents.
            healthy_proxies = self._proxies
        
        if strategy == 'random':
            return random.choice(healthy_proxies)
        else:  # round_robin
            with self._lock:
                proxy = healthy_proxies[self._current_index % len(healthy_proxies)]
                self._current_index += 1
                return proxy
    
    async def test_all_proxies(self, target_host: str = 'www.google.com', target_port: int = 443) -> Dict[str, bool]:
        """
        Test all proxies
        
        Args:
            target_host: The target host for testing
            target_port: The target port for testing
            
        Returns:
            Mapping of proxy identifiers to test results
        """
        results = {}
        tasks = []
        
        for proxy in self._proxies:
            task = proxy.test_connection(target_host, target_port)
            tasks.append((proxy.identifier, task))
        
        for identifier, task in tasks:
            try:
                result = await task
                results[identifier] = result
            except Exception as e:
                logger.error(f'Proxy test error for {identifier}: {e}')
                results[identifier] = False
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """Get proxy pool statistics"""
        return {
            'total': self.count,
            'healthy': self.healthy_count,
            'unhealthy': self.count - self.healthy_count,
            'proxies': [
                {
                    'identifier': p.identifier,
                    'healthy': p.is_healthy,
                    'fail_count': p._fail_count,
                    'network': p.config.network,
                    'tls': p.config.tls
                }
                for p in self._proxies
            ]
        }


class SubscriptionProxyPool:
    "Subscription-based Vless proxy pool - Integrating subscription acquisition, storage, and node management"""
    
    def __init__(self, 
                 subscription_manager=None,
                 node_storage=None,
                 node_tester=None,
                 pattern: str = "CF Preferred-Telecom"):
        """
        Initialize the subscription proxy pool
        
        Args:
            subscription_manager: Subscription manager instance
            node_storage: Node storage instance
            node_tester: Instance of a node tester
            pattern: Default node matching rule
        """
        self.pattern = pattern
        self._subscription_manager = subscription_manager
        self._node_storage = node_storage
        self._node_tester = node_tester
        self._current_index = 0
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def init(self):
        "Initialize subscription and storage"
        if self._initialized:
            return
        
        from .subscription import get_subscription_manager, init_subscriptions_from_env
        from .node_storage import get_node_storage, init_node_storage
        from .node_tester import get_node_tester, init_node_tester
        
        # Initialize each component
        if self._subscription_manager is None:
            self._subscription_manager = await init_subscriptions_from_env()
        
        if self._node_storage is None:
            self._node_storage = await init_node_storage()
        
        if self._node_tester is None:
            self._node_tester = await init_node_tester()
        
        self._initialized = True
        logger.info("SubscriptionProxyPool initialized")
    
    async def refresh_subscriptions(self, test_nodes: bool = True) -> Dict[str, Any]:
        """
        Refresh subscriptions and test nodes
        
        Args:
            test_nodes: Whether to test nodes
            
        Returns:
            Refresh Results Statistics
        """
        await self.init()
        log_raw("INFO", "SUBSCRIPTION_POOL", f"Starting subscription refresh (test_nodes={test_nodes})")

        # Get the latest subscription
        log_raw("INFO", "SUBSCRIPTION_POOL", "Refreshing subscriptions...")
        sub_results = await self._subscription_manager.fetch_all()
        
        # Merge into storage
        all_nodes = []
        for nodes in sub_results.values():
            all_nodes.extend(nodes)
        
        added, updated, _ = await self._node_storage.merge_with_subscription(all_nodes)

        result = {
            'fetched_nodes': len(all_nodes),
            'added': added,
            'updated': updated,
            'tested': 0,
            'available': 0
        }
        log_raw("INFO", "SUBSCRIPTION_POOL", f"Merged {len(all_nodes)} nodes: {added} added, {updated} updated")

        # Test Node
        if test_nodes and all_nodes:
            log_raw("INFO", "SUBSCRIPTION_POOL", "Testing nodes...")
            test_results = await self._node_tester.test_nodes(all_nodes)
            
            # Update storage
            await self._node_tester._update_storage_with_results(test_results)
            
            result['tested'] = len(test_results)
            result['available'] = sum(1 for r in test_results if r.success)

        # keep
        await self._node_storage.save()

        log_raw("INFO", "SUBSCRIPTION_POOL", f"Refresh complete: {result}")
        return result
    
    def get_available_nodes(self, pattern: Optional[str] = None) -> List['VlessNode']:
        """Get available nodes"""
        if not self._initialized or self._node_storage is None:
            return []
        
        pattern = pattern or self.pattern
        return self._node_storage.get_nodes_by_pattern(pattern)
    
    def get_random_node(self, pattern: Optional[str] = None) -> Optional['VlessNode']:
        """Randomly select an available node"""
        import random
        
        nodes = self.get_available_nodes(pattern)
        available = [n for n in nodes if n.is_available]
        
        if not available:
            # If no nodes are available, try to get any node.
            available = nodes
        
        if not available:
            return None
        
        return random.choice(available)
    
    def get_proxy(self, pattern: Optional[str] = None) -> Optional[VlessProxy]:
        """
        Obtain an agent (based on a polling strategy)
        
        Args:
            pattern: Node matching rules
            
        Returns:
            VlessProxy instance or None
        """
        nodes = self.get_available_nodes(pattern)
        available = [n for n in nodes if n.is_available]
        
        if not available:
            available = nodes
        
        if not available:
            return None
        
        with self._lock:
            node = available[self._current_index % len(available)]
            self._current_index += 1
            
            try:
                return VlessProxy(node.uri)
            except Exception as e:
                logger.error(f"Failed to create VlessProxy: {e}")
                return None
    
    async def mark_node_result(self, identifier: str, success: bool, latency: float = 0):
        """Results of tagging nodes"""
        if self._node_storage:
            await self._node_storage.mark_node_result(identifier, success, latency)
        
        if self._subscription_manager:
            self._subscription_manager.mark_node_result(identifier, success, latency)
    
    def get_stats(self) -> Dict[str, Any]:
        """ Obtaining Statistical Information"""
        stats = {
            'pattern': self.pattern,
            'initialized': self._initialized
        }
        
        if self._node_storage:
            storage_stats = self._node_storage.get_stats()
            stats['storage'] = storage_stats
        
        if self._subscription_manager:
            sub_stats = self._subscription_manager.get_stats()
            stats['subscription'] = sub_stats
        
        # Nodes under the current rules
        nodes = self.get_available_nodes()
        available = [n for n in nodes if n.is_available]
        stats['current_pattern'] = {
            'total': len(nodes),
            'available': len(available)
        }
        
        return stats
    
    @property
    def count(self) -> int:
        """Get the total number of agents"""
        return len(self.get_available_nodes())
    
    @property
    def healthy_count(self) -> int:
        """Acquire the number of health agents"""
        nodes = self.get_available_nodes()
        return len([n for n in nodes if n.is_available])


# Global proxy pool instance
_global_proxy_pool: Optional[VlessProxyPool] = None
_global_subscription_pool: Optional[SubscriptionProxyPool] = None


def get_proxy_pool() -> VlessProxyPool:
    """Get global proxy pool instance"""
    global _global_proxy_pool
    if _global_proxy_pool is None:
        _global_proxy_pool = VlessProxyPool()
    return _global_proxy_pool


def get_subscription_pool() -> SubscriptionProxyPool:
    """Get the global subscription proxy pool instance"""
    global _global_subscription_pool
    if _global_subscription_pool is None:
        pattern = os.environ.get('VLESS_SUBSCRIPTION_PATTERN', 'CF Optimized - Telecom')
        _global_subscription_pool = SubscriptionProxyPool(pattern=pattern)
    return _global_subscription_pool


def init_proxy_pool_from_env() -> VlessProxyPool:
    """Initialize the proxy pool from environment variables."""
    pool = get_proxy_pool()
    pool.add_proxies_from_env('VLESS_PROXIES')
    
    # Also check the VLESS_PROXY_FILE environment variable
    import os
    proxy_file = os.environ.get('VLESS_PROXY_FILE')
    if proxy_file:
        pool.add_proxies_from_file(proxy_file)
    
    return pool


async def init_subscription_pool_from_env() -> SubscriptionProxyPool:
    """Initialize the subscription proxy pool from environment variables."""
    pool = get_subscription_pool()
    await pool.init()
    
    # Check if an immediate refresh is needed
    import os
    auto_refresh = os.environ.get('VLESS_AUTO_REFRESH_ON_START', 'true').lower() == 'true'
    if auto_refresh:
        await pool.refresh_subscriptions(test_nodes=True)
    
    return pool

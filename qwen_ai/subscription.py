"""Subscription Management Module - Retrieving and Parsing Vless Nodes from Subscription URLs"

It supports retrieving nodes from the subscription URL, filtering by rules, and storing them locally.
"""

import base64
import json
import re
import asyncio
import aiohttp
import logging
from typing import List, Dict, Optional, Callable, Any, Set
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse, unquote
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class VlessNode:
    "Vless Node Data Class"
    uri: str # Original URI
    name: str # Node name/alias
    address: str # Server address
    port: int # Port
    uuid: str                         # UUID
    network: str = "tcp" # Transfer type
    security: str = "none" # Security type
    host: Optional[str] = None # Hostname
    path: Optional[str] = None # Path
    sni: Optional[str] = None         # SNI
    tls: bool = False # Whether TLS is enabled
    # Metadata
    source_subscription: str = "" # Source subscription URL
    remarks_pattern: str = "" # Matching rules
    added_time: str = field(default_factory=lambda: datetime.now().isoformat())
    last_tested: Optional[str] = None # Last test time
    is_available: bool = True # Whether it is available
    fail_count: int = 0 # Number of failures
    success_count: int = 0 # Number of successes
    average_latency: float = 0.0 # Average latency
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VlessNode':
        """Create from dictionary"""
        return cls(**data)
    
    @property
    def identifier(self) -> str:
        """Unique identifier for the node"""
        return f"{self.address}:{self.port}"
    
    def mark_success(self, latency: float):
        """Marked successfully"""
        self.success_count += 1
        self.fail_count = 0
        self.is_available = True
        self.last_tested = datetime.now().isoformat()
        # Update average latency
        if self.average_latency == 0:
            self.average_latency = latency
        else:
            self.average_latency = (self.average_latency * (self.success_count - 1) + latency) / self.success_count
    
    def mark_fail(self):
        """Marking failed"""
        self.fail_count += 1
        self.last_tested = datetime.now().isoformat()
        if self.fail_count >= 3:
            self.is_available = False


class SubscriptionManager:
    "Subscription Manager"
    
    def __init__(self):
        self.subscriptions: Dict[str, 'Subscription'] = {}
        self.all_nodes: Dict[str, VlessNode] = {}  # identifier -> node
        self.available_nodes: Dict[str, List[VlessNode]] = {}  # pattern -> nodes
        self._lock = asyncio.Lock()
    
    def add_subscription(self, url: str, name: str = "", 
                        remarks_patterns: Optional[List[str]] = None,
                        auto_update_interval: int = 3600) -> 'Subscription':
        """
        Add to subscription
        
        Args:
            url: Subscription URL
            name: Subscription Name
            remarks_patterns: A list of node name matching rules, such as ["CF Preferred - Telecom", "CF Preferred - Mobile"].
            auto_update_interval: Automatic update interval (seconds)
        """
        sub = Subscription(
            url=url,
            name=name,
            remarks_patterns=remarks_patterns or [],
            auto_update_interval=auto_update_interval,
            manager=self
        )
        self.subscriptions[url] = sub
        return sub
    
    async def fetch_all(self) -> Dict[str, List[VlessNode]]:
        """Retrieve all subscribed nodes"""
        results = {}
        for url, sub in self.subscriptions.items():
            try:
                nodes = await sub.fetch()
                results[url] = nodes
                # Update node storage
                await self._update_nodes(nodes, sub.remarks_patterns)
            except Exception as e:
                logger.error(f"Failed to fetch subscription {url}: {e}")
                results[url] = []
        return results
    
    async def _update_nodes(self, nodes: List[VlessNode], patterns: List[str]):
        """Update node storage"""
        async with self._lock:
            for node in nodes:
                # Check if it already exists
                if node.identifier in self.all_nodes:
                    # Preserve status information
                    existing = self.all_nodes[node.identifier]
                    node.is_available = existing.is_available
                    node.fail_count = existing.fail_count
                    node.success_count = existing.success_count
                    node.average_latency = existing.average_latency
                
                self.all_nodes[node.identifier] = node
                
                # Categorized by Rules
                for pattern in patterns:
                    if pattern in node.name or re.search(pattern, node.name):
                        if pattern not in self.available_nodes:
                            self.available_nodes[pattern] = []
                        # Avoid repetition
                        if not any(n.identifier == node.identifier for n in self.available_nodes[pattern]):
                            self.available_nodes[pattern].append(node)
                        node.remarks_pattern = pattern
    
    def get_nodes_by_pattern(self, pattern: str, only_available: bool = True) -> List[VlessNode]:
        """
        Obtain nodes according to the rules.
        
        Args:
            pattern: Matching rule
            only_available: Whether to return only available nodes.
        """
        nodes = self.available_nodes.get(pattern, [])
        if only_available:
            return [n for n in nodes if n.is_available]
        return nodes
    
    def get_random_node(self, pattern: str) -> Optional[VlessNode]:
        """Randomly select a node"""
        import random
        nodes = self.get_nodes_by_pattern(pattern, only_available=True)
        if not nodes:
            return None
        return random.choice(nodes)
    
    def get_all_available_nodes(self) -> List[VlessNode]:
        """Get all available nodes."""
        return [n for n in self.all_nodes.values() if n.is_available]
    
    def mark_node_result(self, identifier: str, success: bool, latency: float = 0):
        """Results of tagging nodes"""
        if identifier in self.all_nodes:
            node = self.all_nodes[identifier]
            if success:
                node.mark_success(latency)
            else:
                node.mark_fail()
            # Synchronize and update nodes in the category list
            for pattern, nodes in self.available_nodes.items():
                for n in nodes:
                    if n.identifier == identifier:
                        if success:
                            n.mark_success(latency)
                        else:
                            n.mark_fail()
    
    def get_stats(self) -> Dict[str, Any]:
        """ Obtaining Statistical Information"""
        total = len(self.all_nodes)
        available = len(self.get_all_available_nodes())
        
        pattern_stats = {}
        for pattern, nodes in self.available_nodes.items():
            available_count = len([n for n in nodes if n.is_available])
            pattern_stats[pattern] = {
                'total': len(nodes),
                'available': available_count,
                'unavailable': len(nodes) - available_count
            }
        
        return {
            'total_nodes': total,
            'available_nodes': available,
            'unavailable_nodes': total - available,
            'subscriptions': len(self.subscriptions),
            'patterns': pattern_stats
        }


class Subscription:
    "Single subscription"
    
    def __init__(self, url: str, name: str = "", 
                 remarks_patterns: Optional[List[str]] = None,
                 auto_update_interval: int = 3600,
                 manager: Optional[SubscriptionManager] = None):
        self.url = url
        self.name = name or url
        self.remarks_patterns = remarks_patterns or []
        self.auto_update_interval = auto_update_interval
        self.manager = manager
        self.last_update: Optional[datetime] = None
        self.nodes: List[VlessNode] = []
        
    async def fetch(self, force: bool = False) -> List[VlessNode]:
        """
        Get Subscribed Content
        
        Args:
            force: Whether to force a refresh
        """
        # Check if an update is needed
        if not force and self.last_update:
            elapsed = (datetime.now() - self.last_update).total_seconds()
            if elapsed < self.auto_update_interval:
                return self.nodes
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    
                    content = await response.text()
                    self.nodes = self._parse_content(content)
                    self.last_update = datetime.now()
                    
                    logger.info(f"Fetched {len(self.nodes)} nodes from {self.name}")
                    return self.nodes
                    
        except Exception as e:
            logger.error(f"Failed to fetch subscription {self.name}: {e}")
            raise
    
    def _parse_content(self, content: str) -> List[VlessNode]:
        """Analyze Subscribed Content"""
        nodes = []
        
        # Attempt Base64 decoding
        decoded = self._try_base64_decode(content)
        if decoded:
            lines = decoded.strip().split('\n')
        else:
            lines = content.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Parse Vless URI
            if line.startswith('vless://'):
                try:
                    node = self._parse_vless_uri(line)
                    if node:
                        # Check if the rule matches
                        if self._matches_patterns(node.name):
                            node.source_subscription = self.url
                            nodes.append(node)
                except Exception as e:
                    logger.debug(f"Failed to parse Vless URI: {e}")
        
        return nodes
    
    def _try_base64_decode(self, content: str) -> Optional[str]:
        """Attempting Base64 decoding"""
        try:
            # Handling potential URL security Base64
            content = content.strip()
            # Fill
            padding = 4 - len(content) % 4
            if padding != 4:
                content += '=' * padding
            
            decoded = base64.b64decode(content).decode('utf-8')
            return decoded
        except:
            return None
    
    def _parse_vless_uri(self, uri: str) -> Optional[VlessNode]:
        """Resolve the Vless URI into a node object"""
        try:
            # vless://uuid@address:port?params#remarks
            if not uri.startswith('vless://'):
                return None
            
            # Remove prefix
            content = uri[8:]
            
            # Separate notes
            remarks = ""
            if '#' in content:
                content, remarks = content.split('#', 1)
                remarks = unquote(remarks)
            
            # Separate parameters
            params_str = ""
            if '?' in content:
                content, params_str = content.split('?', 1)
            
            # Analysis of the main body
            if '@' not in content:
                return None
            
            uuid, server_part = content.split('@', 1)
            
            # Resolve address and port
            if ':' not in server_part:
                return None
            
            # Handling IPv6
            if server_part.startswith('['):
                end_idx = server_part.find(']')
                if end_idx == -1:
                    return None
                address = server_part[1:end_idx]
                port_part = server_part[end_idx + 1:]
                if port_part.startswith(':'):
                    port = int(port_part[1:])
                else:
                    return None
            else:
                address, port_str = server_part.rsplit(':', 1)
                port = int(port_str)
            
            # Parse parameters
            network = "tcp"
            security = "none"
            host = None
            path = None
            sni = None
            tls = False
            
            if params_str:
                from urllib.parse import parse_qs
                params = parse_qs(params_str)
                
                network = params.get('type', ['tcp'])[0]
                security = params.get('security', ['none'])[0]
                host = params.get('host', [None])[0]
                path = params.get('path', [None])[0]
                sni = params.get('sni', [None])[0]
                
                if security in ['tls', 'xtls', 'reality']:
                    tls = True
            
            return VlessNode(
                uri=uri,
                name=remarks or f"{address}:{port}",
                address=address,
                port=port,
                uuid=uuid,
                network=network,
                security=security,
                host=host,
                path=path,
                sni=sni,
                tls=tls,
                source_subscription=self.url
            )
            
        except Exception as e:
            logger.debug(f"Parse Vless URI error: {e}")
            return None
    
    def _matches_patterns(self, name: str) -> bool:
        """Check if the node name matches the rule"""
        if not self.remarks_patterns:
            return True # Accept all rules if none exist
        
        for pattern in self.remarks_patterns:
            if pattern in name or re.search(pattern, name):
                return True
        return False


# Global Subscription Manager
_global_subscription_manager: Optional[SubscriptionManager] = None


def get_subscription_manager() -> SubscriptionManager:
    """Get Global Subscription Manager"""
    global _global_subscription_manager
    if _global_subscription_manager is None:
        _global_subscription_manager = SubscriptionManager()
    return _global_subscription_manager


async def init_subscriptions_from_env() -> SubscriptionManager:
    """Initialize the subscription from environment variables."""
    import os
    
    manager = get_subscription_manager()
    
    # Read the subscription URL
    sub_urls = os.environ.get('VLESS_SUBSCRIPTION_URLS', '')
    if sub_urls:
        # Supports multiple delimiters
        urls = []
        for sep in ['\n', ',', ';']:
            if sep in sub_urls:
                urls = [u.strip() for u in sub_urls.split(sep) if u.strip()]
                break
        if not urls:
            urls = [sub_urls.strip()]
        
        # Read matching rules
        patterns_str = os.environ.get('VLESS_SUBSCRIPTION_PATTERNS', '')
        patterns = []
        if patterns_str:
            for sep in ['\n', ',', ';']:
                if sep in patterns_str:
                    patterns = [p.strip() for p in patterns_str.split(sep) if p.strip()]
                    break
            if not patterns:
                patterns = [patterns_str.strip()]
        
        # Add to subscription
        for url in urls:
            manager.add_subscription(url=url, remarks_patterns=patterns)
        
        # Get it now
        await manager.fetch_all()
    
    return manager
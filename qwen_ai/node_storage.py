"""Node storage module - Local persistent storage available nodes

Supports JSON file storage, with automatic loading and saving.
"""

import json
import os
import asyncio
import logging
from typing import List, Dict, Optional, Any
from dataclasses import asdict
from datetime import datetime, timedelta

from .subscription import VlessNode

logger = logging.getLogger(__name__)


class NodeStorage:
    """Node Storage Manager"""
    
    DEFAULT_STORAGE_FILE = "vless_nodes.json"
    
    def __init__(self, storage_file: Optional[str] = None):
        self.storage_file = storage_file or self.DEFAULT_STORAGE_FILE
        self._cache: Dict[str, VlessNode] = {}
        self._lock = asyncio.Lock()
        self._last_save = datetime.min
        self._dirty = False
    
    async def load(self) -> Dict[str, VlessNode]:
        """Load nodes from files"""
        async with self._lock:
            if not os.path.exists(self.storage_file):
                logger.info(f"Storage file {self.storage_file} not found, starting with empty cache")
                self._cache = {}
                return self._cache
            
            try:
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                nodes = {}
                for identifier, node_data in data.get('nodes', {}).items():
                    try:
                        node = VlessNode.from_dict(node_data)
                        nodes[identifier] = node
                    except Exception as e:
                        logger.debug(f"Failed to load node {identifier}: {e}")
                
                self._cache = nodes
                logger.info(f"Loaded {len(nodes)} nodes from {self.storage_file}")
                return nodes
                
            except Exception as e:
                logger.error(f"Failed to load storage file: {e}")
                self._cache = {}
                return self._cache
    
    async def save(self, nodes: Optional[Dict[str, VlessNode]] = None, force: bool = False):
        """
        Save node to file
        
        Args:
            nodes: The nodes to be saved; if None, save as cache.
            force: Whether to force a save (ignore dirty tags)
        """
        async with self._lock:
            if nodes is not None:
                self._cache = nodes
            
            if not force and not self._dirty:
                # Check the automatic save interval
                elapsed = (datetime.now() - self._last_save).total_seconds()
                if elapsed < 60: # Save at least once every 60 seconds
                    return
            
            try:
                data = {
                    'version': '1.0',
                    'updated_at': datetime.now().isoformat(),
                    'nodes': {
                        identifier: node.to_dict()
                        for identifier, node in self._cache.items()
                    }
                }
                
                # First write to a temporary file, then perform an atomic replacement.
                temp_file = self.storage_file + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # Atomic Substitution
                if os.path.exists(self.storage_file):
                    os.replace(temp_file, self.storage_file)
                else:
                    os.rename(temp_file, self.storage_file)
                
                self._last_save = datetime.now()
                self._dirty = False
                logger.debug(f"Saved {len(self._cache)} nodes to {self.storage_file}")
                
            except Exception as e:
                logger.error(f"Failed to save storage file: {e}")
    
    async def update_node(self, node: VlessNode, auto_save: bool = True):
        """Update a single node"""
        async with self._lock:
            self._cache[node.identifier] = node
            self._dirty = True
        
        if auto_save:
            await self.save()
    
    async def update_nodes(self, nodes: List[VlessNode], auto_save: bool = True):
        "Batch update nodes"
        async with self._lock:
            for node in nodes:
                # Preserve the state of existing nodes
                if node.identifier in self._cache:
                    existing = self._cache[node.identifier]
                    node.is_available = existing.is_available
                    node.fail_count = existing.fail_count
                    node.success_count = existing.success_count
                    node.average_latency = existing.average_latency
                    node.last_tested = existing.last_tested
                
                self._cache[node.identifier] = node
            self._dirty = True
        
        if auto_save:
            await self.save()
    
    async def mark_node_result(self, identifier: str, success: bool, latency: float = 0, auto_save: bool = True):
        """Results of tagging nodes"""
        async with self._lock:
            if identifier in self._cache:
                node = self._cache[identifier]
                if success:
                    node.mark_success(latency)
                else:
                    node.mark_fail()
                self._dirty = True
        
        if auto_save:
            await self.save()
    
    def get_node(self, identifier: str) -> Optional[VlessNode]:
        """Get a single node"""
        return self._cache.get(identifier)
    
    def get_all_nodes(self) -> Dict[str, VlessNode]:
        """Get all nodes"""
        return self._cache.copy()
    
    def get_available_nodes(self) -> List[VlessNode]:
        """Get all available nodes."""
        return [n for n in self._cache.values() if n.is_available]
    
    def get_nodes_by_pattern(self, pattern: str) -> List[VlessNode]:
        """Retrieve nodes by name pattern"""
        import re
        return [
            n for n in self._cache.values()
            if pattern in n.name or re.search(pattern, n.name)
        ]
    
    async def remove_node(self, identifier: str, auto_save: bool = True):
        """Remove Node"""
        async with self._lock:
            if identifier in self._cache:
                del self._cache[identifier]
                self._dirty = True
        
        if auto_save:
            await self.save()
    
    async def clean_expired(self, max_age_days: int = 7, auto_save: bool = True) -> int:
        """
        Clean up expired nodes
        
        Args:
            max_age_days: Maximum number of days to retain data
            
        Returns:
            Number of nodes cleaned
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        to_remove = []
        
        async with self._lock:
            for identifier, node in self._cache.items():
                # Check the last test time
                if node.last_tested:
                    try:
                        last_tested = datetime.fromisoformat(node.last_tested)
                        if last_tested < cutoff and not node.is_available:
                            to_remove.append(identifier)
                    except:
                        pass
        
        for identifier in to_remove:
            await self.remove_node(identifier, auto_save=False)
        
        if to_remove and auto_save:
            await self.save()
        
        logger.info(f"Cleaned {len(to_remove)} expired nodes")
        return len(to_remove)
    
    async def merge_with_subscription(self, sub_nodes: List[VlessNode], auto_save: bool = True) -> tuple:
        """
        Merge subscription nodes and local storage
        
        Args:
            sub_nodes: Nodes obtained from the subscription
            
        Returns:
            (Number of new additions, number of updates, number of removals)
        """
        async with self._lock:
            added = 0
            updated = 0
            
            # Get the identifier from the subscription
            sub_identifiers = {n.identifier for n in sub_nodes}
            
            # Update or add nodes
            for node in sub_nodes:
                if node.identifier in self._cache:
                    # Update existing nodes (preserve state)
                    existing = self._cache[node.identifier]
                    node.is_available = existing.is_available
                    node.fail_count = existing.fail_count
                    node.success_count = existing.success_count
                    node.average_latency = existing.average_latency
                    node.last_tested = existing.last_tested
                    updated += 1
                else:
                    added += 1
                
                self._cache[node.identifier] = node
            
            # Mark nodes that are not in the subscription (but do not delete them, retain history).
            removed = 0
            for identifier in list(self._cache.keys()):
                if identifier not in sub_identifiers:
                    # Optional: Mark as unavailable or delete
                    # self._cache[identifier].is_available = False
                    pass
            
            self._dirty = True
        
        if auto_save:
            await self.save()
        
        logger.info(f"Merged subscription: {added} added, {updated} updated")
        return added, updated, removed
    
    def get_stats(self) -> Dict[str, Any]:
        """Get Storage Statistics"""
        total = len(self._cache)
        available = len(self.get_available_nodes())
        
        # Statistics by Source
        by_source = {}
        for node in self._cache.values():
            source = node.source_subscription or "unknown"
            if source not in by_source:
                by_source[source] = {'total': 0, 'available': 0}
            by_source[source]['total'] += 1
            if node.is_available:
                by_source[source]['available'] += 1
        
        return {
            'total_nodes': total,
            'available_nodes': available,
            'unavailable_nodes': total - available,
            'by_source': by_source,
            'storage_file': self.storage_file,
            'last_save': self._last_save.isoformat() if self._last_save != datetime.min else None
        }


# Global Storage Instance
_global_storage: Optional[NodeStorage] = None


def get_node_storage(storage_file: Optional[str] = None) -> NodeStorage:
    """Get Global Storage Instance"""
    global _global_storage
    if _global_storage is None:
        _global_storage = NodeStorage(storage_file)
    return _global_storage


async def init_node_storage(storage_file: Optional[str] = None) -> NodeStorage:
    """Initialize and load the node storage."""
    storage = get_node_storage(storage_file)
    await storage.load()
    return storage
"""Node Health Detection Module - Automatically Test Node Availability"

Supports concurrent testing, latency measurement, and result labeling.
"""

import asyncio
import time
import logging
from typing import List, Dict, Optional, Callable, Any, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import aiohttp

from .subscription import VlessNode, SubscriptionManager, get_subscription_manager
from .node_storage import NodeStorage, get_node_storage
from .vless_proxy import VlessProxy

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """Test Results"""
    identifier: str
    success: bool
    latency: float # milliseconds
    error: Optional[str] = None
    timestamp: float = 0
    
    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = time.time()


class NodeTester:
    "Node Tester"
    
    # Test Target
    TEST_TARGETS = [
        ('https://www.google.com', 'Google'),
        ('https://www.cloudflare.com', 'Cloudflare'),
        ('https://chat.qwen.ai', 'Qwen AI')
    ]
    
    def __init__(self, 
                 max_concurrent: int = 10,
                 test_timeout: int = 10,
                 retry_times: int = 2):
        """
        Initialize tester
        
        Args:
            max_concurrent: Maximum number of concurrent tests
            test_timeout: Test timeout period (seconds)
            retry_times: Number of retries on failure
        """
        self.max_concurrent = max_concurrent
        self.test_timeout = test_timeout
        self.retry_times = retry_times
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._storage: Optional[NodeStorage] = None
        self._subscription_manager: Optional[SubscriptionManager] = None
    
    async def init(self):
        """Initialize the storage and subscription manager."""
        self._storage = get_node_storage()
        await self._storage.load()
        self._subscription_manager = get_subscription_manager()
    
    async def test_node(self, node: VlessNode, target_url: Optional[str] = None) -> TestResult:
        """
        Test a single node
        
        Args:
            node: The node to be tested
            target_url: The target URL for testing; Google is used by default.
            
        Returns:
            Test Results
        """
        async with self._semaphore:
            target = target_url or self.TEST_TARGETS[0][0]
            
            for attempt in range(self.retry_times):
                try:
                    start_time = time.time()
                    
                    # Create Vless proxy connection
                    proxy = VlessProxy(node.uri)
                    
                    # Test connection
                    success = await proxy.test_connection(
                        target_host=self._extract_host(target),
                        target_port=443 if target.startswith('https') else 80,
                        timeout=self.test_timeout
                    )
                    
                    latency = (time.time() - start_time) * 1000 # Convert to milliseconds
                    
                    if success:
                        return TestResult(
                            identifier=node.identifier,
                            success=True,
                            latency=latency
                        )
                    
                    # If it fails, there is still a chance to try again
                    if attempt < self.retry_times - 1:
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    error_msg = str(e)
                    if attempt < self.retry_times - 1:
                        await asyncio.sleep(1)
                    else:
                        return TestResult(
                            identifier=node.identifier,
                            success=False,
                            latency=0,
                            error=error_msg
                        )
            
            return TestResult(
                identifier=node.identifier,
                success=False,
                latency=0,
                error="All retry attempts failed"
            )
    
    async def test_nodes(self, nodes: List[VlessNode], 
                        progress_callback: Optional[Callable[[int, int], None]] = None) -> List[TestResult]:
        """
        Batch test nodes
        
        Args:
            nodes: a list of nodes
            progress_callback: Progress callback function (current, total)
            
        Returns:
            Test Result List
        """
        results = []
        total = len(nodes)
        
        async def test_with_progress(node: VlessNode, index: int) -> TestResult:
            result = await self.test_node(node)
            if progress_callback:
                progress_callback(index + 1, total)
            return result
        
        # Create Task
        tasks = [
            test_with_progress(node, i)
            for i, node in enumerate(nodes)
        ]
        
        # Concurrent execution
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handling exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(TestResult(
                    identifier=nodes[i].identifier,
                    success=False,
                    latency=0,
                    error=str(result)
                ))
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def test_all_available_nodes(self, 
                                       pattern: Optional[str] = None,
                                       progress_callback: Optional[Callable[[int, int], None]] = None) -> List[TestResult]:
        """
        Test all available nodes
        
        Args:
            pattern: Node name matching rules
            progress_callback: progress callback
            
        Returns:
            Test Result List
        """
        if self._storage is None:
            await self.init()
        
        # Get the node to be tested
        if pattern:
            nodes = self._storage.get_nodes_by_pattern(pattern)
        else:
            nodes = self._storage.get_available_nodes()
        
        if not nodes:
            logger.warning("No nodes to test")
            return []
        
        logger.info(f"Testing {len(nodes)} nodes" + (f" with pattern '{pattern}'" if pattern else ""))
        
        results = await self.test_nodes(nodes, progress_callback)
        
        # Update storage
        await self._update_storage_with_results(results)
        
        return results
    
    async def test_and_update_subscriptions(self, 
                                            patterns: Optional[List[str]] = None,
                                            progress_callback: Optional[Callable[[int, int], None]] = None) -> Dict[str, List[TestResult]]:
        """
        Get the latest subscription and test
        
        Args:
            patterns: A list of node rules to be tested
            progress_callback: progress callback
            
        Returns:
            Test results for each rule
        """
        if self._subscription_manager is None:
            self._subscription_manager = get_subscription_manager()
        
        if self._storage is None:
            self._storage = get_node_storage()
            await self._storage.load()
        
        # 1. Get the latest subscription
        logger.info("Fetching subscriptions...")
        await self._subscription_manager.fetch_all()
        
        # 2. Merge into storage
        all_sub_nodes = []
        for sub in self._subscription_manager.subscriptions.values():
            all_sub_nodes.extend(sub.nodes)
        
        await self._storage.merge_with_subscription(all_sub_nodes)
        
        # 3. Test Node
        results_by_pattern = {}
        
        patterns_to_test = patterns or list(self._subscription_manager.available_nodes.keys())
        
        for pattern in patterns_to_test:
            nodes = self._subscription_manager.get_nodes_by_pattern(pattern, only_available=False)
            if not nodes:
                continue
            
            logger.info(f"Testing {len(nodes)} nodes for pattern '{pattern}'")
            results = await self.test_nodes(nodes, progress_callback)
            results_by_pattern[pattern] = results
            
            # Update storage
            await self._update_storage_with_results(results)
        
        return results_by_pattern
    
    async def _update_storage_with_results(self, results: List[TestResult]):
        """Update storage based on test results"""
        if self._storage is None:
            return
        
        for result in results:
            await self._storage.mark_node_result(
                identifier=result.identifier,
                success=result.success,
                latency=result.latency,
                auto_save=False # Batch save
            )
        
        # Batch save
        await self._storage.save()
    
    def _extract_host(self, url: str) -> str:
        """Extract hostname from URL"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or 'www.google.com'
    
    async def get_recommended_nodes(self, pattern: str, 
                                    min_success_rate: float = 0.8,
                                    max_latency: float = 1000,
                                    limit: int = 5) -> List[VlessNode]:
        """
        Get recommended nodes (high-quality nodes)
        
        Args:
            pattern: Node rules
            min_success_rate: Minimum success rate
            max_latency: Maximum latency (milliseconds)
            limit: Number of items returned
            
        Returns:
            Recommended Node List
        """
        if self._storage is None:
            await self.init()
        
        nodes = self._storage.get_nodes_by_pattern(pattern)
        
        # Filtering high-quality nodes
        qualified = []
        for node in nodes:
            if not node.is_available:
                continue
            
            total = node.success_count + node.fail_count
            if total == 0:
                # Untested nodes are also added to the candidate list
                qualified.append((node, 0, float('inf')))
                continue
            
            success_rate = node.success_count / total
            if success_rate >= min_success_rate and node.average_latency <= max_latency:
                qualified.append((node, success_rate, node.average_latency))
        
        # Sorting: Highest success rate first, then lowest latency
        qualified.sort(key=lambda x: (-x[1], x[2]))
        
        return [node for node, _, _ in qualified[:limit]]
    
    async def get_random_qualified_node(self, pattern: str) -> Optional[VlessNode]:
        "Randomly select a qualified node"
        import random
        
        nodes = await self.get_recommended_nodes(pattern, limit=10)
        if not nodes:
            # If no qualified node is found, try to obtain any available node.
            if self._storage is None:
                await self.init()
            nodes = self._storage.get_nodes_by_pattern(pattern)
            nodes = [n for n in nodes if n.is_available]
        
        if not nodes:
            return None
        
        return random.choice(nodes)
    
    def get_test_summary(self, results: List[TestResult]) -> Dict[str, Any]:
        """Get Test Summary"""
        total = len(results)
        success = sum(1 for r in results if r.success)
        failed = total - success
        
        latencies = [r.latency for r in results if r.success and r.latency > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        min_latency = min(latencies) if latencies else 0
        max_latency = max(latencies) if latencies else 0
        
        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': success / total if total > 0 else 0,
            'latency': {
                'avg': round(avg_latency, 2),
                'min': round(min_latency, 2),
                'max': round(max_latency, 2)
            }
        }


# Global Tester Example
_global_tester: Optional[NodeTester] = None


def get_node_tester(max_concurrent: int = 10) -> NodeTester:
    """Get the global tester"""
    global _global_tester
    if _global_tester is None:
        _global_tester = NodeTester(max_concurrent=max_concurrent)
    return _global_tester


async def init_node_tester(max_concurrent: int = 10) -> NodeTester:
    """Initialize the node tester."""
    tester = get_node_tester(max_concurrent)
    await tester.init()
    return tester
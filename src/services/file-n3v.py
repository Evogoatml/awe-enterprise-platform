# services/proxy_chain.py
import asyncio
import aiohttp
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable
from datetime import datetime, timedelta
import aiohttp_socks
import logging

logger = logging.getLogger(__name__)

@dataclass
class ProxyNode:
    """Single proxy in chain"""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"  # http, https, socks4, socks5
    country: Optional[str] = None  # US, UK, DE, etc
    provider: str = "unknown"
    type: str = "residential"  # residential, datacenter, mobile, isp
    
    # Health tracking
    last_used: Optional[datetime] = None
    fail_count: int = 0
    success_count: int = 0
    avg_response_time: float = 0.0
    is_banned_on: List[str] = field(default_factory=list)  # Platforms where banned
    is_healthy: bool = True
    
    @property
    def url(self) -> str:
        """Get proxy URL"""
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        
        if self.protocol in ['socks4', 'socks5']:
            return f"{self.protocol}://{auth}{self.host}:{self.port}"
        return f"http://{auth}{self.host}:{self.port}"
    
    @property
    def aiohttp_proxy(self) -> str:
        """Format for aiohttp"""
        return self.url

@dataclass
class ProxyChain:
    """Chain of proxies to route through"""
    name: str
    nodes: List[ProxyNode]
    strategy: str = "sequential"  # sequential, random, load_balance, geo_target
    cooldown_seconds: int = 60
    max_failures: int = 3
    
    def get_next_proxy(self, target_country: Optional[str] = None) -> Optional[ProxyNode]:
        """Get next available proxy in chain"""
        available = [n for n in self.nodes if n.is_healthy and self._is_available(n)]
        
        if target_country:
            available = [n for n in available if n.country == target_country]
        
        if not available:
            return None
        
        if self.strategy == "random":
            return random.choice(available)
        elif self.strategy == "sequential":
            # Round-robin
            sorted_nodes = sorted(available, key=lambda n: n.last_used or datetime.min)
            return sorted_nodes[0]
        elif self.strategy == "load_balance":
            # Pick least recently used
            return min(available, key=lambda n: n.last_used or datetime.min)
        
        return available[0]
    
    def _is_available(self, node: ProxyNode) -> bool:
        """Check if proxy is cooled down"""
        if not node.last_used:
            return True
        elapsed = (datetime.now() - node.last_used).total_seconds()
        return elapsed >= self.cooldown_seconds
    
    def mark_success(self, node: ProxyNode, response_time: float):
        """Mark successful use"""
        node.success_count += 1
        node.last_used = datetime.now()
        # Update avg response time
        node.avg_response_time = (node.avg_response_time * (node.success_count - 1) + response_time) / node.success_count
    
    def mark_failure(self, node: ProxyNode, platform: Optional[str] = None):
        """Mark failed use"""
        node.fail_count += 1
        node.last_used = datetime.now()
        
        if platform:
            node.is_banned_on.append(platform)
        
        if node.fail_count >= self.max_failures:
            node.is_healthy = False
            logger.warning(f"Proxy {node.host} marked unhealthy after {node.fail_count} failures")

class ProxyRotator:
    """Main proxy management system"""
    
    def __init__(self):
        self.chains: Dict[str, ProxyChain] = {}
        self.current_chain: Optional[str] = None
        self.health_checker = ProxyHealthChecker()
        self.stats = {}
        
    def add_chain(self, chain: ProxyChain):
        """Add proxy chain"""
        self.chains[chain.name] = chain
        if not self.current_chain:
            self.current_chain = chain.name
    
    def get_proxy_for_request(self, 
                              chain_name: Optional[str] = None,
                              target_country: Optional[str] = None,
                              platform: Optional[str] = None) -> Optional[ProxyNode]:
        """Get proxy for a request"""
        chain = self.chains.get(chain_name or self.current_chain)
        if not chain:
            return None
        
        # Try to get proxy not banned on this platform
        for _ in range(10):  # Try 10 times
            proxy = chain.get_next_proxy(target_country)
            if not proxy:
                break
            if platform and platform in proxy.is_banned_on:
                continue
            return proxy
        
        return None
    
    async def execute_with_proxy(self, 
                                  url: str,
                                  method: str = "GET",
                                  headers: dict = None,
                                  data: dict = None,
                                  chain: Optional[str] = None,
                                  platform: Optional[str] = None,
                                  timeout: int = 30) -> tuple:
        """Execute HTTP request with proxy chain"""
        
        proxy = self.get_proxy_for_request(chain, platform=platform)
        if not proxy:
            raise Exception("No available proxies")
        
        start_time = datetime.now()
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                proxy_url = proxy.aiohttp_proxy
                
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    
                    elapsed = (datetime.now() - start_time).total_seconds()
                    
                    if response.status < 400:
                        proxy_chain = self.chains[chain or self.current_chain]
                        proxy_chain.mark_success(proxy, elapsed)
                        return await response.read(), response.status, proxy
                    else:
                        proxy_chain.mark_failure(proxy, platform)
                        raise Exception(f"HTTP {response.status}")
                        
        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            proxy_chain = self.chains[chain or self.current_chain]
            proxy_chain.mark_failure(proxy, platform)
            logger.error(f"Proxy request failed: {e}")
            raise

class ProxyHealthChecker:
    """Background health monitoring"""
    
    CHECK_URLS = {
        'ip_check': 'https://ipinfo.io/json',
        'reddit': 'https://www.reddit.com/r/all.json',
        'twitter': 'https://api.twitter.com/1.1/help/configuration.json',
        'google': 'https://www.google.com'
    }
    
    async def check_proxy(self, proxy: ProxyNode, platform: Optional[str] = None) -> dict:
        """Check if proxy is working"""
        check_url = self.CHECK_URLS.get(platform, self.CHECK_URLS['ip_check'])
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    check_url,
                    proxy=proxy.aiohttp_proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    
                    if platform == 'ip_check':
                        data = await response.json()
                        return {
                            'healthy': True,
                            'ip': data.get('ip'),
                            'country': data.get('country'),
                            'response_time': response.headers.get('X-Response-Time')
                        }
                    
                    return {
                        'healthy': response.status < 400,
                        'status': response.status
                    }
                    
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e)
            }
    
    async def health_check_all(self, chains: Dict[str, ProxyChain]):
        """Check all proxies in all chains"""
        tasks = []
        
        for chain_name, chain in chains.items():
            for node in chain.nodes:
                task = self.check_and_update(node, chain_name)
                tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def check_and_update(self, node: ProxyNode, chain_name: str):
        """Check single proxy and update status"""
        result = await self.check_proxy(node)
        
        if not result['healthy']:
            node.fail_count += 1
            if node.fail_count >= 3:
                node.is_healthy = False
                logger.warning(f"Proxy {node.host} unhealthy")
        else:
            node.is_healthy = True
            node.fail_count = 0
            if 'country' in result:
                node.country = result['country']
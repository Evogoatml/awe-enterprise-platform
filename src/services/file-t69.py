class MultiHopChain:
    """Route through multiple proxies (Tor-style)"""
    
    def __init__(self, entry_proxies: List[ProxyNode], 
                 exit_proxies: List[ProxyNode]):
        self.entry = entry_proxies
        self.exit = exit_proxies
        
    async def execute_multihop(self, url: str, headers: dict = None) -> bytes:
        """Route through entry -> exit"""
        
        # First hop - entry node
        entry = random.choice(self.entry)
        
        # Second hop - exit node
        exit_node = random.choice(self.exit)
        
        # Build chain: client -> entry -> exit -> target
        # This requires proxy chaining support
        
        # Simplified: Use entry as proxy, which routes to exit
        # (requires proxy server that supports forwarding)
        
        session = aiohttp.ClientSession()
        
        # Set up chain
        proxy_chain = f"{entry.url},{exit_node.url}"
        
        async with session.get(
            url,
            proxy=entry.url,  # Entry handles forwarding to exit
            headers=headers
        ) as response:
            return await response.read()

class TorLikeChain:
    """Simulate Tor with 3-hop proxy chains"""
    
    def __init__(self, proxy_pool: List[ProxyNode]):
        self.pool = proxy_pool
        
    def build_circuit(self) -> List[ProxyNode]:
        """Build 3-hop circuit (guard -> middle -> exit)"""
        return [
            self._select_guard(),
            self._select_middle(),
            self._select_exit()
        ]
    
    def _select_guard(self) -> ProxyNode:
        """Entry node - stable, long-lived"""
        # Pick node with best uptime
        stable = sorted(self.pool, 
                       key=lambda n: n.success_count / max(n.fail_count, 1),
                       reverse=True)
        return stable[0]
    
    def _select_middle(self) -> ProxyNode:
        """Middle node - any healthy node"""
        healthy = [n for n in self.pool if n.is_healthy]
        return random.choice(healthy)
    
    def _select_exit(self) -> ProxyNode:
        """Exit node - different country for anonymity"""
        # Pick node from different country than guard
        countries = list(set(n.country for n in self.pool if n.country))
        country = random.choice(countries)
        exits = [n for n in self.pool if n.country == country]
        return random.choice(exits) if exits else random.choice(self.pool)
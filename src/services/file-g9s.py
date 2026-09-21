# services/proxy_providers.py

class BrightDataLoader:
    """Load proxies from Bright Data (formerly Luminati)"""
    
    def __init__(self, api_key: str, zone: str):
        self.api_key = api_key
        self.zone = zone
        self.base_url = "https://brightdata.com/api"
    
    def get_residential_chain(self, country: Optional[str] = None, 
                              session_type: str = " rotating") -> ProxyChain:
        """Get Bright Data residential proxies"""
        
        # Bright Data uses super proxy format
        host = "brd.superproxy.io"
        port = 22225
        
        username = f"brd-customer-{self.zone}"
        if country:
            username += f"-country-{country.lower()}"
        
        nodes = []
        for i in range(10):  # Create 10 sessions
            node = ProxyNode(
                host=host,
                port=port,
                username=username,
                password=self.api_key,
                protocol="http",
                country=country,
                provider="brightdata",
                type="residential"
            )
            nodes.append(node)
        
        return ProxyChain(
            name=f"brightdata_{country or 'global'}",
            nodes=nodes,
            strategy="random",
            cooldown_seconds=30
        )

class OxylabsLoader:
    """Load proxies from Oxylabs"""
    
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
    
    def get_mobile_chain(self, country: Optional[str] = None) -> ProxyChain:
        """Get mobile/4G proxies"""
        
        host = "mobile.oxyproxy.io"
        port = 7777
        
        user = f"customer-{self.username}"
        if country:
            user += f"-cc-{country.lower()}"
        user += "-sesstime-10"  # 10 min session
        
        nodes = []
        for i in range(5):
            node = ProxyNode(
                host=host,
                port=port,
                username=user,
                password=self.password,
                protocol="http",
                country=country,
                provider="oxylabs",
                type="mobile"
            )
            nodes.append(node)
        
        return ProxyChain(
            name=f"oxylabs_mobile_{country or 'global'}",
            nodes=nodes,
            strategy="sequential",
            cooldown_seconds=600  # 10 min for mobile
        )

class ProxyRackLoader:
    """Load from ProxyRack (cheaper option)"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def get_rotating_chain(self) -> ProxyChain:
        """Get rotating residential"""
        
        # ProxyRack rotating endpoint
        nodes = [ProxyNode(
            host="megaproxy.rotating.proxyrack.net",
            port=222,
            username=self.api_key,
            password="",
            protocol="http",
            provider="proxyrack",
            type="residential"
        )]
        
        return ProxyChain(
            name="proxyrack_rotating",
            nodes=nodes,
            strategy="random",
            cooldown_seconds=0  # Each request new IP
        )

class IPRoyalLoader:
    """Load from IPRoyal (static ISP proxies)"""
    
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
    
    def get_static_chain(self, countries: List[str]) -> ProxyChain:
        """Get static ISP proxies for account warming"""
        
        nodes = []
        for country in countries:
            # IPRoyal format
            host = f"{country}.iproyal.com"
            node = ProxyNode(
                host=host,
                port=12323,
                username=self.username,
                password=self.password,
                protocol="socks5",
                country=country,
                provider="iproyal",
                type="isp"
            )
            nodes.append(node)
        
        return ProxyChain(
            name="iproyal_static_isp",
            nodes=nodes,
            strategy="load_balance",
            cooldown_seconds=300
        )
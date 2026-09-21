# services/platform_bots.py (updated with proxy chains)

class RedditBotVoussoir(PlatformBot):
    def __init__(self, credentials, proxy_chain: Optional[ProxyChain] = None):
        super().__init__(credentials, None)  # We handle proxy differently
        self.proxy_chain = proxy_chain
        self.rotator = ProxyRotator()
        
        if proxy_chain:
            self.rotator.add_chain(proxy_chain)
    
    async def post(self, content: dict) -> str:
        """Post with automatic proxy rotation"""
        
        # Get fresh proxy for this request
        proxy = self.rotator.get_proxy_for_request(
            chain=self.proxy_chain.name if self.proxy_chain else None,
            platform='reddit',
            target_country='US'  # Target US Reddit users
        )
        
        if not proxy:
            raise Exception("No healthy proxies available")
        
        # Configure voussoir with proxy
        import requests
        session = requests.Session()
        session.proxies = {
            'http': proxy.url,
            'https': proxy.url
        }
        
        # Pass session to voussoir
        self.client.session = session
        
        try:
            # Execute post
            result = await self._do_post(content)
            
            # Mark success
            self.proxy_chain.mark_success(proxy, 0)
            return result
            
        except Exception as e:
            # Mark failure
            self.proxy_chain.mark_failure(proxy, 'reddit')
            
            # Retry with different proxy
            logger.warning(f"Post failed with proxy {proxy.host}, retrying...")
            return await self.post(content)  # Recursive retry
    
    async def warmup_account(self):
        """Build karma using rotating proxies"""
        
        # Use different proxy for each warmup action
        subs = ['aww', 'pics', 'funny', 'AskReddit']
        
        for sub in subs:
            proxy = self.rotator.get_proxy_for_request(
                chain=self.proxy_chain.name,
                platform='reddit'
            )
            
            # Upvote a post
            try:
                self.client.session.proxies = {'http': proxy.url, 'https': proxy.url}
                post = self.client.get_subreddit(sub).random()
                post.upvote()
                
                self.proxy_chain.mark_success(proxy, 0)
                await asyncio.sleep(random.randint(30, 120))
                
            except:
                self.proxy_chain.mark_failure(proxy, 'reddit')
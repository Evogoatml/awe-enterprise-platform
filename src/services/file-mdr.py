class RedditManager:
    """Multi-account Reddit manager using voussoir"""
    
    def __init__(self):
        self.accounts = {}
        self.account_pool = []
        
    def load_accounts(self, account_list: list):
        """Load multiple aged accounts"""
        for creds in account_list:
            bot = RedditBotVoussoir(creds, creds.get('proxy'))
            self.accounts[creds['username']] = bot
            self.account_pool.append(creds['username'])
    
    async def rotate_post(self, content: dict):
        """Auto-rotate through accounts to avoid rate limits"""
        # Get least recently used account
        username = self.account_pool.pop(0)
        bot = self.accounts[username]
        
        # Check health
        if not await bot.check_ban_status():
            logger.warning(f"Account {username} banned, removing")
            del self.accounts[username]
            return await self.rotate_post(content)
        
        # Post
        post_id = await bot.post(content)
        
        # Move to end of queue
        self.account_pool.append(username)
        
        return post_id, username
    
    async def comment_boost(self, post_id: str, comments: list):
        """Add engagement comments from alts"""
        for comment_text in comments:
            # Use different account for each comment
            username = random.choice(self.account_pool)
            bot = self.accounts[username]
            
            try:
                await bot.comment(post_id, comment_text)
                await asyncio.sleep(random.randint(60, 300))
            except:
                continue
    
    async def karma_farm(self, username: str):
        """Build karma on new account before posting"""
        bot = self.accounts[username]
        
        # Upvote popular posts in SFW subs
        popular = bot.client.get_subreddit('popular').hot(limit=10)
        for post in popular:
            await bot.upvote(post.id)
            await asyncio.sleep(5)
        
        # Make safe comments
        # ...
# utils/account_lifecycle.py
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List
import asyncio

class AccountStage(Enum):
    WARMING = "warming"        # Building karma/reputation
    ACTIVE = "active"          # Full posting
    RESTING = "resting"        # Cooldown to avoid ban
    BANNED = "banned"          # Platform banned
    RETIRED = "retired"        # Graceful shutdown

class AccountLifecycleManager:
    """Manage account from birth to retirement"""
    
    def __init__(self, db, platform_manager):
        self.db = db
        self.platforms = platform_manager
        self.warming_strategies = {
            'reddit': self._warm_reddit,
            'twitter': self._warm_twitter,
            'telegram': self._warm_telegram
        }
        
    async def create_account(self, platform: str, credentials: Dict) -> str:
        """Onboard new account"""
        
        account_id = await self.db.execute("""
            INSERT INTO accounts 
            (platform, username, credentials, stage, created_at, warming_started)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            RETURNING id
        """, platform, credentials['username'], credentials, AccountStage.WARMING.value)
        
        # Start warming process
        asyncio.create_task(self._warmup_account(account_id, platform))
        
        return account_id
    
    async def _warmup_account(self, account_id: str, platform: str):
        """Gradual warming sequence"""
        
        strategy = self.warming_strategies.get(platform)
        if not strategy:
            return
        
        # Phase 1: Lurking (days 1-3)
        await self._update_stage(account_id, AccountStage.WARMING, "lurking")
        await strategy.lurk(duration_days=3)
        
        # Phase 2: Light engagement (days 4-7)
        await self._update_stage(account_id, AccountStage.WARMING, "engaging")
        await strategy.engage(duration_days=4, intensity='low')
        
        # Phase 3: Content creation (days 8-14)
        await self._update_stage(account_id, AccountStage.WARMING, "creating")
        await strategy.create_content(duration_days=7, intensity='medium')
        
        # Graduate to active
        await self._update_stage(account_id, AccountStage.ACTIVE, "full_posting")
        
    async def _update_stage(self, account_id: str, stage: AccountStage, sub_stage: str):
        await self.db.execute("""
            UPDATE accounts 
            SET stage = $2, sub_stage = $3, stage_updated_at = NOW()
            WHERE id = $1
        """, account_id, stage.value, sub_stage)
    
    async def check_health(self, account_id: str) -> Dict:
        """Check if account still healthy"""
        
        account = await self.db.fetchrow(
            "SELECT * FROM accounts WHERE id = $1", 
            account_id
        )
        
        if not account:
            return {'healthy': False, 'reason': 'not_found'}
        
        # Check if banned
        bot = self.platforms.get_bot(account['platform'], account['username'])
        is_banned = not await bot.check_ban_status()
        
        if is_banned:
            await self._update_stage(account_id, AccountStage.BANNED, "detected")
            return {'healthy': False, 'reason': 'banned'}
        
        # Check activity patterns
        recent_posts = await self.db.fetchval("""
            SELECT COUNT(*) FROM posts 
            WHERE account_id = $1 AND posted_at > NOW() - INTERVAL '24 hours'
        """, account_id)
        
        if recent_posts > 50:  # Too active
            await self._rest_account(account_id, hours=24)
            return {'healthy': True, 'action': 'forced_rest', 'reason': 'overposting'}
        
        return {'healthy': True, 'posts_24h': recent_posts}
    
    async def _rest_account(self, account_id: str, hours: int):
        """Put account in resting state"""
        await self._update_stage(account_id, AccountStage.RESTING, f"cooldown_{hours}h")
        
        await asyncio.sleep(hours * 3600)
        
        await self._update_stage(account_id, AccountStage.ACTIVE, "resumed")

class RedditWarmingStrategy:
    """Reddit-specific warming"""
    
    def __init__(self, bot):
        self.bot = bot
        
    async def lurk(self, duration_days: int):
        """Read posts, build history"""
        for day in range(duration_days):
            # Subscribe to SFW subreddits
            subs = ['aww', 'pics', 'funny', 'AskReddit']
            for sub in subs:
                await self.bot.subscribe(sub)
                await asyncio.sleep(random.randint(300, 900))
            
            await asyncio.sleep(86400)  # Wait a day
    
    async def engage(self, duration_days: int, intensity: str):
        """Upvote and comment"""
        actions = {'low': 5, 'medium': 15, 'high': 30}[intensity]
        
        for day in range(duration_days):
            for _ in range(actions):
                # Upvote random popular post
                await self.bot.upvote_random_popular()
                await asyncio.sleep(random.randint(600, 1800))
    
    async def create_content(self, duration_days: int, intensity: str):
        """Post safe content"""
        posts = {'low': 1, 'medium': 3, 'high': 5}[intensity]
        
        for day in range(duration_days):
            for _ in range(posts):
                # Post in SFW subs
                await self.bot.post_safe_content()
                await asyncio.sleep(random.randint(3600, 7200))
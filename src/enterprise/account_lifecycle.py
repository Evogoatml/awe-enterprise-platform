# enterprise/account_lifecycle.py
import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Dict, List, Optional, Callable, Any
import json

class AccountStage(Enum):
    CREATED = auto()           # Just created, not touched
    WARMING = auto()           # Building reputation
    WARMING_LURK = auto()      # Phase 1: Just watching
    WARMING_ENGAGE = auto()    # Phase 2: Light engagement
    WARMING_CREATE = auto()    # Phase 3: Safe content
    ACTIVE = auto()            # Full posting allowed
    RESTING = auto()           # Temporary cooldown
    SUSPICIOUS = auto()        # Possible detection
    BANNED = auto()            # Confirmed banned
    RETIRED = auto()           # Graceful end of life

class AccountHealth(Enum):
    EXCELLENT = 5
    GOOD = 4
    FAIR = 3
    POOR = 2
    CRITICAL = 1
    BANNED = 0

@dataclass
class AccountMetrics:
    """Track account health metrics"""
    karma_score: float = 0.0
    account_age_days: int = 0
    total_posts: int = 0
    total_engagement: int = 0
    reported_posts: int = 0
    removed_posts: int = 0
    
    # Rate limiting tracking
    rate_limit_hits: int = 0
    last_rate_limit: Optional[datetime] = None
    
    # Platform-specific
    shadow_ban_score: float = 0.0  # 0-1 likelihood
    
    @property
    def health_score(self) -> float:
        """Calculate overall health 0-100"""
        score = 100.0
        
        # Penalize removed posts heavily
        score -= self.removed_posts * 20
        
        # Penalize reports
        score -= self.reported_posts * 10
        
        # Penalize rate limits
        score -= self.rate_limit_hits * 5
        
        # Bonus for engagement
        score += min(self.total_engagement * 0.1, 20)
        
        # Age bonus (older = more trusted)
        score += min(self.account_age_days * 0.5, 15)
        
        return max(0, min(100, score))

class AccountLifecycleManager:
    """Manage full lifecycle of platform accounts"""
    
    def __init__(self, db, platform_manager, config: Dict):
        self.db = db
        self.platforms = platform_manager
        self.config = config
        
        # Warming strategies per platform
        self.warming_strategies = {
            'reddit': RedditWarmingStrategy(),
            'twitter': TwitterWarmingStrategy(),
            'telegram': TelegramWarmingStrategy()
        }
        
        # Active monitoring
        self.monitoring_tasks: Dict[str, asyncio.Task] = {}
        
    async def create_account(self, 
                              platform: str,
                              credentials: Dict,
                              purchase_info: Optional[Dict] = None) -> str:
        """Onboard new account"""
        
        # Validate credentials work
        test_result = await self._test_credentials(platform, credentials)
        if not test_result['valid']:
            raise AccountCreationError(
                f"Invalid credentials: {test_result['error']}"
            )
        
        # Store in database
        account_id = await self.db.execute("""
            INSERT INTO accounts 
            (platform, username, credentials, stage, 
             health_score, created_at, warming_started,
             purchase_info, proxy_assigned)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW(), $6, $7)
            RETURNING id
        """, 
            platform,
            credentials['username'],
            json.dumps(credentials),
            AccountStage.CREATED.value,
            50,  # Initial health
            json.dumps(purchase_info) if purchase_info else None,
            await self._assign_proxy(platform)
        )
        
        # Start warming process
        asyncio.create_task(self._warmup_account(account_id, platform))
        
        # Start health monitoring
        self.monitoring_tasks[account_id] = asyncio.create_task(
            self._monitor_account(account_id)
        )
        
        return account_id
    
    async def _test_credentials(self, platform: str, credentials: Dict) -> Dict:
        """Verify credentials work before storing"""
        try:
            bot = self.platforms.create_bot(platform, credentials)
            await bot.start()
            me = await bot.get_me()
            await bot.close()
            
            return {
                'valid': True,
                'account_info': me
            }
        except Exception as e:
            return {
                'valid': False,
                'error': str(e)
            }
    
    async def _assign_proxy(self, platform: str) -> Optional[str]:
        """Assign appropriate proxy for account"""
        # Get least-used proxy for this platform
        proxy = await self.db.fetchrow("""
            SELECT id, proxy_url FROM proxies 
            WHERE platform = $1 
            AND assigned_accounts < max_accounts
            ORDER BY assigned_accounts ASC
            LIMIT 1
        """, platform)
        
        if proxy:
            await self.db.execute("""
                UPDATE proxies 
                SET assigned_accounts = assigned_accounts + 1
                WHERE id = $1
            """, proxy['id'])
            return proxy['proxy_url']
        
        return None
    
    async def _warmup_account(self, account_id: str, platform: str):
        """Execute warming sequence"""
        
        strategy = self.warming_strategies.get(platform)
        if not strategy:
            logger.warning(f"No warming strategy for {platform}")
            await self._transition(account_id, AccountStage.ACTIVE)
            return
        
        try:
            # Phase 1: Lurking (Days 1-3)
            await self._transition(account_id, AccountStage.WARMING_LURK)
            await strategy.lurk(account_id, self.db, duration_days=3)
            
            # Phase 2: Engagement (Days 4-7)
            await self._transition(account_id, AccountStage.WARMING_ENGAGE)
            await strategy.engage(account_id, self.db, duration_days=4)
            
            # Phase 3: Content Creation (Days 8-14)
            await self._transition(account_id, AccountStage.WARMING_CREATE)
            await strategy.create_content(account_id, self.db, duration_days=7)
            
            # Graduate to active
            await self._transition(account_id, AccountStage.ACTIVE)
            
            # Update health score
            await self._update_health(account_id)
            
        except Exception as e:
            logger.error(f"Warming failed for {account_id}: {e}")
            await self._transition(account_id, AccountStage.SUSPICIOUS)
    
    async def _transition(self, account_id: str, new_stage: AccountStage):
        """Transition account to new stage"""
        
        await self.db.execute("""
            UPDATE accounts 
            SET stage = $2, stage_changed_at = NOW()
            WHERE id = $1
        """, account_id, new_stage.value)
        
        # Log transition
        await self.db.execute("""
            INSERT INTO account_stage_history 
            (account_id, stage, transitioned_at)
            VALUES ($1, $2, NOW())
        """, account_id, new_stage.value)
        
        logger.info(f"Account {account_id} transitioned to {new_stage.name}")
    
    async def _monitor_account(self, account_id: str):
        """Continuous health monitoring"""
        while True:
            try:
                await self._health_check(account_id)
                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"Monitor error for {account_id}: {e}")
                await asyncio.sleep(60)
    
    async def _health_check(self, account_id: str):
        """Check account health"""
        
        account = await self.db.fetchrow(
            "SELECT * FROM accounts WHERE id = $1", 
            account_id
        )
        
        if not account:
            return
        
        # Check if banned
        bot = self.platforms.get_bot(
            account['platform'], 
            json.loads(account['credentials'])
        )
        
        try:
            is_banned = not await bot.check_ban_status()
            
            if is_banned:
                await self._handle_ban(account_id, "Health check detected ban")
                return
            
            # Check for shadow ban indicators
            shadow_ban_indicators = await self._check_shadow_ban(account_id, bot)
            
            if shadow_ban_indicators['likelihood'] > 0.7:
                await self._transition(account_id, AccountStage.SUSPICIOUS)
                
                # Reduce activity
                await self._reduce_activity(account_id)
            
            # Update metrics
            await self._update_metrics(account_id, bot)
            
            # Auto-rest if needed
            if account['stage'] == AccountStage.ACTIVE.value:
                metrics = await self._get_metrics(account_id)
                
                if metrics.rate_limit_hits > 3:
                    await self._transition(account_id, AccountStage.RESTING)
                    asyncio.create_task(self._auto_rest(account_id, hours=24))
                    
        except Exception as e:
            logger.error(f"Health check error: {e}")
    
    async def _check_shadow_ban(self, account_id: str, bot) -> Dict:
        """Check for shadow ban indicators"""
        
        indicators = {
            'likelihood': 0.0,
            'factors': []
        }
        
        # Check if posts appear in new queue
        recent_posts = await self.db.fetch("""
            SELECT platform_post_id FROM posts 
            WHERE account_id = $1 
            ORDER BY posted_at DESC 
            LIMIT 5
        """, account_id)
        
        for post in recent_posts:
            visible = await bot.check_post_visibility(post['platform_post_id'])
            if not visible:
                indicators['likelihood'] += 0.2
                indicators['factors'].append('post_not_visible')
        
        # Check engagement rate drop
        engagement_trend = await self._get_engagement_trend(account_id)
        if engagement_trend == 'dropping':
            indicators['likelihood'] += 0.3
            indicators['factors'].append('engagement_drop')
        
        return indicators
    
    async def _handle_ban(self, account_id: str, reason: str):
        """Handle confirmed ban"""
        
        await self._transition(account_id, AccountStage.BANNED)
        
        # Log ban details
        await self.db.execute("""
            INSERT INTO account_bans 
            (account_id, detected_at, reason, recovery_attempted)
            VALUES ($1, NOW(), $2, FALSE)
        """, account_id, reason)
        
        # Cancel monitoring
        if account_id in self.monitoring_tasks:
            self.monitoring_tasks[account_id].cancel()
            del self.monitoring_tasks[account_id]
        
        # Attempt recovery if possible
        asyncio.create_task(self._attempt_recovery(account_id))
        
        # Alert
        logger.critical(f"Account {account_id} BANNED: {reason}")
    
    async def _attempt_recovery(self, account_id: str):
        """Attempt to recover banned account"""
        
        # Check if appeal is possible
        account = await self.db.fetchrow(
            "SELECT platform FROM accounts WHERE id = $1", 
            account_id
        )
        
        if account['platform'] == 'reddit':
            # Reddit sometimes allows appeals
            success = await self._reddit_appeal(account_id)
            
            if success:
                await self._transition(account_id, AccountStage.WARMING)
                await self.db.execute("""
                    UPDATE account_bans 
                    SET recovered_at = NOW()
                    WHERE account_id = $1
                """, account_id)
            else:
                await self._transition(account_id, AccountStage.RETIRED)
    
    async def _auto_rest(self, account_id: str, hours: int):
        """Auto-rest account"""
        
        await asyncio.sleep(hours * 3600)
        
        # Check if still healthy
        account = await self.db.fetchrow(
            "SELECT stage FROM accounts WHERE id = $1", 
            account_id
        )
        
        if account and account['stage'] == AccountStage.RESTING.value:
            await self._transition(account_id, AccountStage.ACTIVE)
    
    async def get_healthy_accounts(self, 
                                    platform: str, 
                                    min_health: int = 70,
                                    count: int = 1) -> List[Dict]:
        """Get accounts ready for posting"""
        
        accounts = await self.db.fetch("""
            SELECT * FROM accounts 
            WHERE platform = $1 
            AND stage = 'active'
            AND health_score >= $2
            AND (last_used IS NULL OR last_used < NOW() - INTERVAL '1 hour')
            ORDER BY health_score DESC, RANDOM()
            LIMIT $3
        """, platform, min_health, count)
        
        # Update last_used
        for acc in accounts:
            await self.db.execute("""
                UPDATE accounts SET last_used = NOW() WHERE id = $1
            """, acc['id'])
        
        return accounts
    
    async def retire_account(self, account_id: str, reason: str):
        """Gracefully retire account"""
        
        await self._transition(account_id, AccountStage.RETIRED)
        
        # Cancel monitoring
        if account_id in self.monitoring_tasks:
            self.monitoring_tasks[account_id].cancel()
        
        # Release proxy
        await self.db.execute("""
            UPDATE proxies 
            SET assigned_accounts = assigned_accounts - 1
            WHERE id = (SELECT proxy_assigned FROM accounts WHERE id = $1)
        """, account_id)
        
        logger.info(f"Account {account_id} retired: {reason}")

class RedditWarmingStrategy:
    """Reddit-specific warming sequence"""
    
    async def lurk(self, account_id: str, db, duration_days: int):
        """Phase 1: Just watch and subscribe"""
        
        account = await db.fetchrow(
            "SELECT credentials FROM accounts WHERE id = $1", 
            account_id
        )
        creds = json.loads(account['credentials'])
        
        # Initialize bot
        from voussoir.reddit import Reddit
        reddit = Reddit(**creds)
        
        # Subscribe to SFW subreddits
        sfw_subs = ['aww', 'pics', 'funny', 'AskReddit', 'todayilearned']
        
        for sub in sfw_subs:
            try:
                reddit.subscribe(sub)
                await asyncio.sleep(random.randint(60, 300))
            except:
                pass
        
        # Random browsing pattern
        for day in range(duration_days):
            # Simulate browsing
            for _ in range(random.randint(10, 30)):
                await asyncio.sleep(random.randint(300, 900))
            
            # Update progress
            await db.execute("""
                UPDATE accounts 
                SET warming_progress = $2 
                WHERE id = $1
            """, account_id, f"lurking day {day+1}/{duration_days}")
        
        reddit.close()
    
    async def engage(self, account_id: str, db, duration_days: int):
        """Phase 2: Light engagement"""
        
        account = await db.fetchrow(
            "SELECT credentials FROM accounts WHERE id = $1", 
            account_id
        )
        creds = json.loads(account['credentials'])
        
        from voussoir.reddit import Reddit
        reddit = Reddit(**creds)
        
        actions_per_day = random.randint(5, 15)
        
        for day in range(duration_days):
            for _ in range(actions_per_day):
                action = random.choice(['upvote', 'comment'])
                
                if action == 'upvote':
                    # Upvote popular post
                    try:
                        sub = reddit.get_subreddit('popular')
                        post = sub.hot(limit=1).__next__()
                        post.upvote()
                    except:
                        pass
                else:
                    # Safe comment
                    try:
                        comments = [
                            "Nice!",
                            "Thanks for sharing",
                            "Interesting",
                            "Cool"
                        ]
                        # Comment on safe post
                    except:
                        pass
                
                await asyncio.sleep(random.randint(600, 1800))
            
            await db.execute("""
                UPDATE accounts 
                SET warming_progress = $2,
                    karma_score = karma_score + $3
                WHERE id = $1
            """, account_id, f"engaging day {day+1}/{duration_days}", 
                random.randint(1, 5))
        
        reddit.close()
    
    async def create_content(self, account_id: str, db, duration_days: int):
        """Phase 3: Safe content creation"""
        
        # Post in safe subs with original content
        safe_subs = ['aww', 'mildlyinteresting', 'pics']
        
        for day in range(duration_days):
            if random.random() > 0.3:  # 70% chance to post
                try:
                    sub = random.choice(safe_subs)
                    # Post cute animal pic or similar safe content
                    # ...
                    pass
                except:
                    pass
            
            await asyncio.sleep(86400)  # Wait a day

class TwitterWarmingStrategy:
    """Twitter-specific warming"""
    
    async def lurk(self, account_id: str, db, duration_days: int):
        """Follow accounts, build timeline"""
        pass  # Implementation...
    
    async def engage(self, account_id: str, db, duration_days: int):
        """Like and retweet"""
        pass  # Implementation...
    
    async def create_content(self, account_id: str, db, duration_days: int):
        """Tweet safe content"""
        pass  # Implementation...

class TelegramWarmingStrategy:
    """Telegram needs minimal warming"""
    
    async def lurk(self, account_id: str, db, duration_days: int):
        pass
    
    async def engage(self, account_id: str, db, duration_days: int):
        pass
    
    async def create_content(self, account_id: str, db, duration_days: int):
        """Join channels, understand culture"""
        pass

class AccountCreationError(Exception):
    pass
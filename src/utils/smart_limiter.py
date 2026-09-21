# enterprise/smart_limiter.py
import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
from collections import deque
import logging

logger = logging.getLogger(__name__)

class ActionType(Enum):
    POST = "post"
    COMMENT = "comment"
    UPVOTE = "upvote"
    DM = "dm"
    SCRAPE = "scrape"
    LOGIN = "login"

class LimiterMode(Enum):
    CONSERVATIVE = "conservative"  # Survival mode
    BALANCED = "balanced"          # Default
    AGGRESSIVE = "aggressive"      # When winning
    EMERGENCY = "emergency"        # Minimal only

@dataclass
class ActionBudget:
    """Dynamic budget for actions"""
    daily_max: int
    hourly_max: int
    min_interval: int  # seconds between actions
    
    # Adaptive adjustments
    current_hourly: int = field(default=0)
    current_daily: int = field(default=0)
    current_interval: float = field(default=0.0)
    
    # Performance tracking
    actions_today: deque = field(default_factory=lambda: deque(maxlen=1000))
    actions_this_hour: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def __post_init__(self):
        self.current_hourly = self.hourly_max
        self.current_daily = self.daily_max
        self.current_interval = self.min_interval

class SmartLimiter:
    """
    Intelligent action limiter that:
    - Throttles down when performance drops
    - Scales up when winning
    - Never stops completely (maintains minimum presence)
    - Respects platform-specific patterns
    """
    
    def __init__(self, db, metrics_collector):
        self.db = db
        self.metrics = metrics_collector
        
        # Base limits per platform/action
        self.base_limits: Dict[str, Dict[str, ActionBudget]] = {
            'reddit': {
                ActionType.POST.value: ActionBudget(daily_max=20, hourly_max=3, min_interval=1800),
                ActionType.COMMENT.value: ActionBudget(daily_max=50, hourly_max=10, min_interval=300),
                ActionType.UPVOTE.value: ActionBudget(daily_max=100, hourly_max=20, min_interval=60),
            },
            'twitter': {
                ActionType.POST.value: ActionBudget(daily_max=50, hourly_max=10, min_interval=600),
                ActionType.COMMENT.value: ActionBudget(daily_max=100, hourly_max=20, min_interval=120),
            },
            'telegram': {
                ActionType.POST.value: ActionBudget(daily_max=100, hourly_max=30, min_interval=300),
            }
        }
        
        # Current mode per platform
        self.modes: Dict[str, LimiterMode] = {}
        
        # Performance windows for decision making
        self.performance_window: Dict[str, deque] = {}
        
        # Emergency brake tracking
        self.emergency_until: Dict[str, datetime] = {}
        
        # Minimum viable action (never go below this)
        self.minimum_viable = {
            ActionType.POST.value: 1,      # At least 1 post per day
            ActionType.COMMENT.value: 3,   # Some engagement
        }
    
    async def check_and_execute(self,
                                  platform: str,
                                  action_type: ActionType,
                                  account_id: str,
                                  func: Callable,
                                  *args,
                                  **kwargs) -> Any:
        """
        Check if action allowed, execute if yes
        
        Returns result or raises LimiterBlocked
        """
        
        # Check emergency brake
        if self._is_emergency(platform):
            raise LimiterBlocked(f"Emergency brake active for {platform}")
        
        # Get current budget
        budget = await self._get_current_budget(platform, action_type, account_id)
        
        # Check if allowed
        allowed, reason = await self._is_action_allowed(platform, action_type, budget, account_id)
        
        if not allowed:
            # Check if we should do minimum anyway
            if await self._should_minimum_viable(platform, action_type):
                logger.info(f"Minimum viable action override for {platform}/{action_type}")
            else:
                raise LimiterBlocked(f"Action blocked: {reason}")
        
        # Execute with timing tracking
        start = datetime.now()
        try:
            result = await func(*args, **kwargs)
            
            # Record success
            await self._record_action(platform, action_type, account_id, 'success', start)
            
            # Check if we should scale up
            await self._evaluate_scaling(platform, action_type, 'success')
            
            return result
            
        except Exception as e:
            # Record failure
            await self._record_action(platform, action_type, account_id, 'failed', start, error=str(e))
            
            # Scale down on failure
            await self._evaluate_scaling(platform, action_type, 'failed')
            
            raise
    
    async def _get_current_budget(self, 
                                   platform: str, 
                                   action_type: ActionType,
                                   account_id: str) -> ActionBudget:
        """Get dynamically adjusted budget"""
        
        base = self.base_limits.get(platform, {}).get(action_type.value)
        if not base:
            # Default conservative
            base = ActionBudget(daily_max=10, hourly_max=2, min_interval=3600)
        
        # Apply mode modifier
        mode = self.modes.get(platform, LimiterMode.BALANCED)
        
        if mode == LimiterMode.CONSERVATIVE:
            base.current_hourly = int(base.hourly_max * 0.3)
            base.current_daily = int(base.daily_max * 0.5)
            base.current_interval = base.min_interval * 2
            
        elif mode == LimiterMode.BALANCED:
            base.current_hourly = base.hourly_max
            base.current_daily = base.daily_max
            base.current_interval = base.min_interval
            
        elif mode == LimiterMode.AGGRESSIVE:
            # Only if recent performance good
            if await self._recent_performance_good(platform):
                base.current_hourly = int(base.hourly_max * 1.5)
                base.current_daily = int(base.daily_max * 1.2)
                base.current_interval = base.min_interval * 0.7
            else:
                # Fall back to balanced
                base.current_hourly = base.hourly_max
                base.current_daily = base.daily_max
                base.current_interval = base.min_interval
        
        # Account-specific adjustments
        account_health = await self._get_account_health(account_id)
        if account_health < 50:
            base.current_hourly = int(base.current_hourly * 0.5)
            base.current_interval *= 1.5
        
        return base
    
    async def _is_action_allowed(self,
                                  platform: str,
                                  action_type: ActionType,
                                  budget: ActionBudget,
                                  account_id: str) -> tuple:
        """Check if action should be allowed"""
        
        now = datetime.now()
        
        # Check daily limit
        daily_count = await self._get_daily_count(platform, action_type, account_id)
        if daily_count >= budget.current_daily:
            return False, f"Daily limit reached ({daily_count}/{budget.current_daily})"
        
        # Check hourly limit
        hourly_count = await self._get_hourly_count(platform, action_type, account_id)
        if hourly_count >= budget.current_hourly:
            return False, f"Hourly limit reached ({hourly_count}/{budget.current_hourly})"
        
        # Check interval
        last_action = await self._get_last_action_time(platform, action_type, account_id)
        if last_action:
            elapsed = (now - last_action).total_seconds()
            if elapsed < budget.current_interval:
                return False, f"Interval not met ({elapsed:.0f}s < {budget.current_interval}s)"
        
        # Check platform health (circuit breaker integration)
        platform_health = await self._get_platform_health(platform)
        if platform_health < 0.3:
            return False, f"Platform health too low ({platform_health:.2f})"
        
        return True, "allowed"
    
    async def _should_minimum_viable(self, platform: str, action_type: ActionType) -> bool:
        """Check if we should do minimum action anyway"""
        
        # Only for critical actions
        if action_type.value not in self.minimum_viable:
            return False
        
        # Check if we've done minimum today
        minimum = self.minimum_viable[action_type.value]
        done_today = await self._get_daily_count(platform, action_type, 'all')
        
        if done_today < minimum:
            # It's been 24 hours and we haven't hit minimum - force it
            hours_since_last = await self._hours_since_last_action(platform, action_type)
            if hours_since_last > 20:
                return True
        
        return False
    
    async def _evaluate_scaling(self, platform: str, action_type: ActionType, result: str):
        """Adjust limits based on performance"""
        
        # Track in window
        key = f"{platform}:{action_type.value}"
        if key not in self.performance_window:
            self.performance_window[key] = deque(maxlen=20)
        
        self.performance_window[key].append({
            'result': result,
            'time': datetime.now()
        })
        
        # Calculate success rate
        window = self.performance_window[key]
        if len(window) < 5:
            return  # Not enough data
        
        success_rate = sum(1 for w in window if w['result'] == 'success') / len(window)
        
        # Adjust mode based on success rate
        if success_rate > 0.9:
            # Winning - can be more aggressive
            if self.modes.get(platform) != LimiterMode.AGGRESSIVE:
                logger.info(f"Scaling UP {platform} to AGGRESSIVE (success: {success_rate:.2%})")
                self.modes[platform] = LimiterMode.AGGRESSIVE
                
        elif success_rate > 0.7:
            # Healthy - stay balanced
            self.modes[platform] = LimiterMode.BALANCED
            
        elif success_rate > 0.5:
            # Struggling - be conservative
            if self.modes.get(platform) != LimiterMode.CONSERVATIVE:
                logger.warning(f"Scaling DOWN {platform} to CONSERVATIVE (success: {success_rate:.2%})")
                self.modes[platform] = LimiterMode.CONSERVATIVE
                
        else:
            # Failing - emergency mode
            logger.error(f"EMERGENCY mode for {platform} (success: {success_rate:.2%})")
            self.modes[platform] = LimiterMode.EMERGENCY
            self.emergency_until[platform] = datetime.now() + timedelta(hours=6)
    
    async def _recent_performance_good(self, platform: str) -> bool:
        """Check if recent actions are performing well"""
        
        # Get recent EPC/revenue data
        recent = await self.db.fetch("""
            SELECT AVG(epc) as avg_epc, COUNT(*) as actions
            FROM posts p
            JOIN campaigns c ON p.campaign_id = c.id
            WHERE c.platform = $1
            AND p.posted_at > NOW() - INTERVAL '1 hour'
        """, platform)
        
        if not recent or recent[0]['actions'] < 3:
            return False  # Not enough data
        
        avg_epc = recent[0]['avg_epc'] or 0
        
        # Good if EPC > $1.00
        return avg_epc > 1.0
    
    def _is_emergency(self, platform: str) -> bool:
        """Check if platform in emergency mode"""
        if platform not in self.emergency_until:
            return False
        
        if datetime.now() > self.emergency_until[platform]:
            # Emergency expired
            del self.emergency_until[platform]
            self.modes[platform] = LimiterMode.CONSERVATIVE
            return False
        
        return True
    
    async def force_mode(self, platform: str, mode: LimiterMode, duration_minutes: int = 60):
        """Manually force a mode"""
        self.modes[platform] = mode
        
        if mode == LimiterMode.EMERGENCY:
            self.emergency_until[platform] = datetime.now() + timedelta(minutes=duration_minutes)
        
        logger.info(f"Manually set {platform} to {mode.value} for {duration_minutes}min")
    
    async def get_status(self, platform: Optional[str] = None) -> Dict:
        """Get limiter status"""
        
        if platform:
            return {
                'platform': platform,
                'mode': self.modes.get(platform, LimiterMode.BALANCED).value,
                'emergency': self._is_emergency(platform),
                'budgets': {
                    at.value: {
                        'daily': self.base_limits.get(platform, {}).get(at.value, ActionBudget(0,0,0)).current_daily,
                        'hourly': self.base_limits.get(platform, {}).get(at.value, ActionBudget(0,0,0)).current_hourly,
                    }
                    for at in ActionType
                }
            }
        
        return {
            p: await self.get_status(p) 
            for p in self.base_limits.keys()
        }
    
    # Database helpers (simplified)
    async def _get_daily_count(self, platform: str, action_type: ActionType, account_id: str) -> int:
        result = await self.db.fetchval("""
            SELECT COUNT(*) FROM action_log 
            WHERE platform = $1 AND action_type = $2 
            AND account_id = $3 AND DATE(created_at) = CURRENT_DATE
        """, platform, action_type.value, account_id)
        return result or 0
    
    async def _get_hourly_count(self, platform: str, action_type: ActionType, account_id: str) -> int:
        result = await self.db.fetchval("""
            SELECT COUNT(*) FROM action_log 
            WHERE platform = $1 AND action_type = $2 
            AND account_id = $3 AND created_at > NOW() - INTERVAL '1 hour'
        """, platform, action_type.value, account_id)
        return result or 0
    
    async def _get_last_action_time(self, platform: str, action_type: ActionType, account_id: str) -> Optional[datetime]:
        result = await self.db.fetchval("""
            SELECT created_at FROM action_log 
            WHERE platform = $1 AND action_type = $2 AND account_id = $3
            ORDER BY created_at DESC LIMIT 1
        """, platform, action_type.value, account_id)
        return result
    
    async def _record_action(self, platform: str, action_type: ActionType, account_id: str, 
                            result: str, start_time: datetime, error: Optional[str] = None):
        await self.db.execute("""
            INSERT INTO action_log (platform, action_type, account_id, result, 
                                   created_at, duration_ms, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, platform, action_type.value, account_id, result, 
             datetime.now(), 
             (datetime.now() - start_time).total_seconds() * 1000,
             error)
    
    async def _get_account_health(self, account_id: str) -> int:
        result = await self.db.fetchval("""
            SELECT health_score FROM accounts WHERE id = $1
        """, account_id)
        return result or 50
    
    async def _get_platform_health(self, platform: str) -> float:
        # Integration with circuit breaker
        return 0.8  # Placeholder
    
    async def _hours_since_last_action(self, platform: str, action_type: ActionType) -> float:
        last = await self.db.fetchval("""
            SELECT created_at FROM action_log 
            WHERE platform = $1 AND action_type = $2
            ORDER BY created_at DESC LIMIT 1
        """, platform, action_type.value)
        
        if not last:
            return 999
        
        return (datetime.now() - last).total_seconds() / 3600

class LimiterBlocked(Exception):
    pass

# Integration with platform bots
class RateLimitedBot:
    """Wrapper that adds smart limiting"""
    
    def __init__(self, bot, limiter: SmartLimiter, platform: str, account_id: str):
        self.bot = bot
        self.limiter = limiter
        self.platform = platform
        self.account_id = account_id
    
    async def post(self, content: Dict):
        """Post with rate limiting"""
        return await self.limiter.check_and_execute(
            self.platform,
            ActionType.POST,
            self.account_id,
            self.bot.post,
            content
        )
    
    async def comment(self, post_id: str, text: str):
        """Comment with rate limiting"""
        return await self.limiter.check_and_execute(
            self.platform,
            ActionType.COMMENT,
            self.account_id,
            self.bot.comment,
            post_id, text
        )
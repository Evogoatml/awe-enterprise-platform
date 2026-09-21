# services/optimization.py
from enum import Enum

class ActionType(Enum):
    INCREASE_VOLUME = "increase_volume"
    DECREASE_VOLUME = "decrease_volume"
    PAUSE = "pause"
    ROTATE_TAGS = "rotate_tags"
    CHANGE_POSTING_TIMES = "change_posting_times"
    SCALE_WINNER = "scale_winner"

class OptimizationEngine:
    def __init__(self, db, analytics):
        self.db = db
        self.analytics = analytics
        self.rules = self.load_optimization_rules()
        
    def load_optimization_rules(self):
        """Load ML rules or heuristic thresholds"""
        return {
            'epc_threshold_high': 2.00,
            'epc_threshold_low': 0.30,
            'conversion_rate_threshold': 2.0,
            'min_posts_before_decision': 20,
            'trend_lookback_days': 7
        }
    
    async def analyze_and_optimize(self, sub_affiliate_id: str):
        """Main optimization loop"""
        metrics = await self.analytics.calculate_metrics(sub_affiliate_id)
        
        # Decision tree
        actions = []
        
        # Rule 1: High EPC = Scale
        if metrics['epc'] > self.rules['epc_threshold_high']:
            actions.append({
                'type': ActionType.SCALE_WINNER,
                'reason': f"High EPC: ${metrics['epc']:.2f}",
                'parameters': {'increase_by_percent': 50}
            })
        
        # Rule 2: Low conversion = Rotate content
        elif metrics['conversion_rate'] < 1.0 and metrics['epc'] < self.rules['epc_threshold_low']:
            actions.append({
                'type': ActionType.ROTATE_TAGS,
                'reason': f"Low conversion: {metrics['conversion_rate']:.2f}%, EPC: ${metrics['epc']:.2f}",
                'parameters': {'new_tags': await self.suggest_tags(sub_affiliate_id)}
            })
        
        # Rule 3: Down trending = Decrease or pause
        elif metrics['trend'] == 'down_trending':
            actions.append({
                'type': ActionType.DECREASE_VOLUME,
                'reason': "Performance declining",
                'parameters': {'decrease_by_percent': 30}
            })
        
        # Apply actions
        for action in actions:
            await self.apply_action(sub_affiliate_id, action)
            
        return actions
    
    async def suggest_tags(self, sub_affiliate_id: str) -> List[str]:
        """ML-based tag suggestion based on what's working"""
        # Query top performing content tags
        top_tags = await self.db.fetch("""
            SELECT UNNEST(tags) as tag, 
                   SUM(revenue) as total_revenue,
                   COUNT(*) as usage_count
            FROM posts p
            JOIN content_pool c ON p.content_id = c.id
            WHERE p.sub_affiliate_id = $1
            AND p.revenue > 0
            GROUP BY tag
            ORDER BY total_revenue / NULLIF(usage_count, 0) DESC
            LIMIT 10
        """, sub_affiliate_id)
        
        return [t['tag'] for t in top_tags]
    
    async def apply_action(self, sub_affiliate_id: str, action: dict):
        """Execute optimization action"""
        
        if action['type'] == ActionType.SCALE_WINNER:
            await self.db.execute("""
                UPDATE campaigns 
                SET posting_schedule = jsonb_set(
                    posting_schedule,
                    '{posts_per_day}',
                    (COALESCE(posting_schedule->>'posts_per_day','10')::int * 1.5)::text::jsonb
                )
                WHERE sub_affiliate_id = $1
            """, sub_affiliate_id)
            
        elif action['type'] == ActionType.ROTATE_TAGS:
            await self.db.execute("""
                UPDATE campaigns 
                SET tags = $2,
                    last_tag_rotation = NOW()
                WHERE sub_affiliate_id = $1
            """, sub_affiliate_id, action['parameters']['new_tags'])
        
        # Log action
        await self.db.execute("""
            INSERT INTO optimization_logs 
            (sub_affiliate_id, action, reason, auto_applied)
            VALUES ($1, $2, $3, $4)
        """, sub_affiliate_id, action['type'].value, action['reason'], True)
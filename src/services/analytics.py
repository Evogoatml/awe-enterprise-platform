# services/analytics.py
from datetime import datetime, timedelta
import pandas as pd

class AnalyticsEngine:
    def __init__(self, db, clickhouse):
        self.db = db
        self.clickhouse = clickhouse
        
    async def fetch_awe_stats(self, sub_affiliate_id: str, days: int = 7):
        """Pull from AWE XML feeds"""
        end = datetime.now()
        start = end - timedelta(days=days)
        
        urls = {
            'sales': f"https://remote-stat.awempire.com/export-xml-sales-sub-affiliate?"
                   f"startDate={start.strftime('%Y-%m-%d')}&"
                   f"endDate={end.strftime('%Y-%m-%d')}&"
                   f"subAffiliateId={sub_affiliate_id}&"
                   f"dailyGroup=true&"
                   f"partnerHash={PARTNER_HASH}",
            'clicks': f"https://remote-stat.awempire.com/export-xml-click-and-sales?"
                     f"startDate={start.strftime('%Y-%m-%d')}&"
                     f"endDate={end.strftime('%Y-%m-%d')}&"
                     f"dailyGroup=true&"
                     f"partnerHash={PARTNER_HASH}"
        }
        
        stats = {}
        for stat_type, url in urls.items():
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    xml = await resp.text()
                    stats[stat_type] = self.parse_xml(xml)
        
        return stats
    
    async def calculate_metrics(self, sub_affiliate_id: str) -> dict:
        """Calculate key performance metrics"""
        
        # Get data from multiple sources
        awe_stats = await self.fetch_awe_stats(sub_affiliate_id, days=7)
        db_stats = await self.db.fetch("""
            SELECT 
                COUNT(*) as total_posts,
                SUM(clicks) as total_clicks,
                SUM(conversions) as total_conversions,
                SUM(revenue) as total_revenue,
                AVG(engagement->>'upvotes') as avg_engagement
            FROM posts 
            WHERE sub_affiliate_id = $1 
            AND posted_at > NOW() - INTERVAL '7 days'
        """, sub_affiliate_id)
        
        # Calculate derived metrics
        posts = db_stats['total_posts']
        clicks = db_stats['total_clicks'] or 0
        conversions = db_stats['total_conversions'] or 0
        revenue = db_stats['total_revenue'] or 0
        
        return {
            'epc': revenue / clicks if clicks > 0 else 0,
            'conversion_rate': (conversions / clicks * 100) if clicks > 0 else 0,
            'revenue_per_post': revenue / posts if posts > 0 else 0,
            'roi': (revenue - self.estimate_costs(sub_affiliate_id)) / self.estimate_costs(sub_affiliate_id) * 100,
            'trend': await self.calculate_trend(sub_affiliate_id),
            'top_content': await self.get_top_performing_content(sub_affiliate_id)
        }
    
    async def calculate_trend(self, sub_affiliate_id: str) -> str:
        """Determine if performance is improving or declining"""
        # Compare last 7 days vs previous 7 days
        current = await self.get_revenue_period(sub_affiliate_id, 7)
        previous = await self.get_revenue_period(sub_affiliate_id, 14, 7)
        
        if current > previous * 1.2:
            return 'up_trending'
        elif current < previous * 0.8:
            return 'down_trending'
        return 'stable'
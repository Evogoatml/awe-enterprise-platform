# workers/scheduler.py
from rq import Queue
from redis import Redis
from datetime import datetime, timedelta

redis_conn = Redis()
queue = Queue(connection=redis_conn)

class CampaignScheduler:
    def __init__(self):
        self.queue = queue
        
    async def add_campaign(self, campaign_id: str):
        """Schedule campaign for execution"""
        campaign = await self.get_campaign(campaign_id)
        
        # Schedule based on posting_schedule
        for hour in campaign['posting_schedule']['hours']:
            # Calculate next occurrence of this hour
            next_run = self.get_next_occurrence(hour)
            
            queue.enqueue_at(
                next_run,
                execute_posting_cycle,
                campaign_id,
                retry=Retry(max=3)
            )
    
    async def execute_posting_cycle(self, campaign_id: str):
        """Main posting job"""
        campaign = await self.get_campaign(campaign_id)
        
        # 1. Get content
        content = await self.select_content(campaign)
        
        # 2. Generate caption with LLM
        caption = await self.generate_caption(content, campaign)
        
        # 3. Post to platform
        post_data = {
            'title': caption,
            'link': content['target_url'],
            'image': content['thumbnail_url'],
            'subreddit': campaign['target_subreddit']  # for Reddit
        }
        
        post_id = await platform_manager.post_to_campaign(campaign_id, post_data)
        
        # 4. Log post
        await self.log_post(campaign_id, content['id'], post_id)
        
        # 5. Schedule next post
        interval = campaign['posting_schedule']['interval_min']
        next_post = datetime.now() + timedelta(minutes=interval)
        queue.enqueue_at(next_post, execute_posting_cycle, campaign_id)

# workers/stats_sync.py
async def sync_awe_stats():
    """Hourly job to pull AWE stats"""
    for sub in await get_all_sub_affiliates():
        stats = await analytics_engine.fetch_awe_stats(sub['id'], days=1)
        await analytics_engine.store_stats(sub['id'], stats)

async def daily_optimization():
    """Daily job to run optimization"""
    await optimization_engine.run_daily_optimization()
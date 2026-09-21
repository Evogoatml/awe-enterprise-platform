# api/main.py
from fastapi import FastAPI, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AWE Affiliate Intelligence Platform")

# Services
content_service = ContentIngestionService(...)
platform_manager = PlatformManager()
analytics_engine = AnalyticsEngine(...)
optimization_engine = OptimizationEngine(...)

@app.on_event("startup")
async def startup():
    await content_service.start()
    await platform_manager.initialize_bots()

# Content endpoints
@app.get("/api/content/pool")
async def get_content_pool(
    status: str = "available",
    tags: List[str] = None,
    limit: int = 50,
    db = Depends(get_db)
):
    """Get available content for posting"""
    query = """
        SELECT * FROM content_pool 
        WHERE status = $1 
        AND ($2::text[] IS NULL OR tags && $2)
        ORDER BY performance_score DESC
        LIMIT $3
    """
    return await db.fetch(query, status, tags, limit)

@app.post("/api/content/refresh")
async def refresh_content(source: str = "all"):
    """Manually trigger content refresh"""
    if source in ["all", "rss"]:
        rss_items = await content_service.ingest_rss()
        await content_service.store_content(rss_items)
    
    if source in ["all", "video_api"]:
        video_items = await content_service.ingest_video_api(
            tags=['milf', 'teen', 'blonde'], 
            limit=100
        )
        await content_service.store_content(video_items)
    
    return {"status": "success", "items_fetched": len(rss_items) + len(video_items)}

# Campaign endpoints
@app.post("/api/campaigns")
async def create_campaign(campaign: CampaignCreate):
    """Create new posting campaign"""
    campaign_id = await db.execute("""
        INSERT INTO campaigns 
        (name, sub_affiliate_id, tags, posting_schedule, content_sources)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
    """, campaign.name, campaign.sub_affiliate_id, 
         campaign.tags, campaign.posting_schedule, 
         campaign.content_sources)
    
    # Schedule campaign in worker queue
    await scheduler.add_campaign(campaign_id)
    return {"id": campaign_id}

@app.get("/api/campaigns/{id}/performance")
async def get_campaign_performance(campaign_id: str):
    """Get detailed performance metrics"""
    metrics = await analytics_engine.calculate_metrics(campaign_id)
    return metrics

# Analytics endpoints
@app.get("/api/analytics/dashboard")
async def get_dashboard_data(days: int = 7):
    """Aggregate data for main dashboard"""
    data = {
        'revenue': await analytics_engine.get_total_revenue(days),
        'clicks': await analytics_engine.get_total_clicks(days),
        'conversions': await analytics_engine.get_total_conversions(days),
        'top_performers': await analytics_engine.get_top_performers(days),
        'hourly_heatmap': await analytics_engine.get_posting_heatmap(days),
        'sub_affiliate_breakdown': await analytics_engine.get_sub_affiliate_comparison(days)
    }
    return data

@app.get("/api/analytics/sub-affiliates/{id}")
async def get_sub_affiliate_detail(sub_affiliate_id: str, days: int = 7):
    """Detailed view for single sub-affiliate"""
    return {
        'metrics': await analytics_engine.calculate_metrics(sub_affiliate_id),
        'daily_breakdown': await analytics_engine.get_daily_stats(sub_affiliate_id, days),
        'content_performance': await analytics_engine.get_content_performance(sub_affiliate_id, days),
        'optimization_history': await analytics_engine.get_optimization_history(sub_affiliate_id)
    }

# Optimization endpoints
@app.post("/api/optimization/run")
async def run_optimization(sub_affiliate_id: Optional[str] = None):
    """Trigger optimization analysis"""
    if sub_affiliate_id:
        actions = await optimization_engine.analyze_and_optimize(sub_affiliate_id)
    else:
        # Run for all active sub-affiliates
        subs = await db.fetch("SELECT id FROM sub_affiliates WHERE status = 'active'")
        actions = []
        for sub in subs:
            sub_actions = await optimization_engine.analyze_and_optimize(sub['id'])
            actions.extend(sub_actions)
    
    return {"actions_taken": actions}

# WebSocket for real-time updates
@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    await websocket.accept()
    while True:
        # Push real-time stats every 30 seconds
        data = await analytics_engine.get_real_time_stats()
        await websocket.send_json(data)
        await asyncio.sleep(30)
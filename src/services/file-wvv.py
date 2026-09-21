# services/content_ingestion.py
import asyncio
import aiohttp
from typing import List, Dict
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ContentItem:
    id: str
    title: str
    performer: str
    tags: List[str]
    thumbnail: str
    target_url: str
    quality: str
    source: str
    fetched_at: datetime

class ContentIngestionService:
    def __init__(self, db, awe_config):
        self.db = db
        self.awe = awe_config
        self.session = None
        
    async def start(self):
        self.session = aiohttp.ClientSession()
        
    async def ingest_rss(self) -> List[ContentItem]:
        """Fetch from RSS feed"""
        async with self.session.get(self.awe['rss_url']) as resp:
            feed = await resp.text()
            # Parse RSS XML
            items = []
            # ... RSS parsing logic ...
            return items
    
    async def ingest_video_api(self, tags: List[str], limit: int = 50) -> List[ContentItem]:
        """Fetch from Video Promotion API"""
        items = []
        
        for tag in tags:
            url = (f"https://pt.ptawe.com/api/video-promotion/v1/list?"
                   f"psid={self.awe['psid']}&"
                   f"accessKey={self.awe['access_key']}&"
                   f"tags={tag}&limit={limit}&quality=hd")
            
            async with self.session.get(url) as resp:
                data = await resp.json()
                
                for video in data['data']['videos']:
                    items.append(ContentItem(
                        id=video['id'],
                        title=video['title'],
                        performer=video.get('uploader', 'Unknown'),
                        tags=video.get('tags', []),
                        thumbnail='https:' + video['profileImage'] if video['profileImage'].startswith('//') else video['profileImage'],
                        target_url=video['targetUrl'],
                        quality=video['quality'],
                        source='video_api',
                        fetched_at=datetime.now()
                    ))
        
        return items
    
    async def store_content(self, items: List[ContentItem]):
        """Store in database, deduplicate"""
        for item in items:
            # Check if exists
            exists = await self.db.fetchval(
                "SELECT 1 FROM content_pool WHERE external_id = $1",
                item.id
            )
            
            if not exists:
                await self.db.execute("""
                    INSERT INTO content_pool 
                    (external_id, source, title, performer, tags, 
                     thumbnail_url, target_url, quality, fetched_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """, item.id, item.source, item.title, item.performer,
                     item.tags, item.thumbnail, item.target_url, 
                     item.quality, item.fetched_at)
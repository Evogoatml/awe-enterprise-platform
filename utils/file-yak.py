# utils/content_fingerprint.py
import hashlib
import imagehash
from PIL import Image
import io
import numpy as np
from typing import Set, Tuple
import aiohttp

class ContentFingerprinter:
    """Prevent reposting same/similar content"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.seen_hashes: Set[str] = set()
        self.similarity_threshold = 5  # For perceptual hashing
        
    async def fingerprint_image(self, image_url: str) -> Tuple[str, str]:
        """Generate perceptual hash of image"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    image_data = await resp.read()
            
            # Perceptual hash (robust to minor changes)
            img = Image.open(io.BytesIO(image_data))
            phash = str(imagehash.phash(img))
            dhash = str(imagehash.dhash(img))
            
            # Also MD5 for exact match
            md5 = hashlib.md5(image_data).hexdigest()
            
            return phash, md5
            
        except Exception as e:
            return None, None
    
    async def is_duplicate(self, image_url: str, platform: str) -> bool:
        """Check if content already posted"""
        phash, md5 = await self.fingerprint_image(image_url)
        
        if not phash:
            return False
        
        # Check exact match
        exact_key = f"content:exact:{md5}:{platform}"
        if await self.redis.exists(exact_key):
            return True
        
        # Check perceptual similarity
        # Get recent hashes for this platform
        recent_key = f"content:phash:{platform}"
        recent_hashes = await self.redis.lrange(recent_key, 0, 1000)
        
        for recent_hash in recent_hashes:
            if self._hamming_distance(phash, recent_hash) < self.similarity_threshold:
                return True  # Too similar
        
        # Store new hash
        await self.redis.setex(exact_key, 86400 * 30, "1")  # 30 days
        await self.redis.lpush(recent_key, phash)
        await self.redis.ltrim(recent_key, 0, 1000)  # Keep last 1000
        
        return False
    
    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """Calculate hamming distance between two hashes"""
        return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
    
    async def fingerprint_text(self, text: str) -> str:
        """Generate semantic fingerprint of text"""
        # Normalize: lowercase, remove extra spaces, sort words
        normalized = ' '.join(sorted(text.lower().split()))
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

class ContentDeduplicator:
    """Multi-level deduplication"""
    
    def __init__(self, fingerprinter: ContentFingerprinter, db):
        self.fp = fingerprinter
        self.db = db
        
    async def check_content_pool(self, content_id: str) -> bool:
        """Check if content already in pool"""
        exists = await self.db.fetchval(
            "SELECT 1 FROM content_pool WHERE external_id = $1",
            content_id
        )
        return exists is not None
    
    async def check_recent_posts(self, performer: str, platform: str, hours: int = 24) -> bool:
        """Check if performer recently posted on platform"""
        recent = await self.db.fetchval("""
            SELECT 1 FROM posts p
            JOIN content_pool c ON p.content_id = c.id
            WHERE c.performer = $1 
            AND p.platform = $2
            AND p.posted_at > NOW() - INTERVAL '$3 hours'
        """, performer, platform, hours)
        
        return recent is not None
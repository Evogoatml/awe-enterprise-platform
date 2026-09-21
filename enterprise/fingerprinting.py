# enterprise/fingerprinting.py
import asyncio
import hashlib
import io
import json
import pickle
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Union
import numpy as np

import aiohttp
import imagehash
from PIL import Image
import cv2
import redis.asyncio as redis
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

@dataclass
class ContentSignature:
    """Complete fingerprint of content"""
    content_id: str
    source_url: Optional[str]
    
    # Image hashes
    perceptual_hash: Optional[str] = None  # pHash (64-bit)
    difference_hash: Optional[str] = None   # dHash
    wavelet_hash: Optional[str] = None    # wHash
    color_hash: Optional[str] = None      # Color distribution
    
    # Text signatures
    text_semantic_vector: Optional[List[float]] = None
    text_exact_hash: Optional[str] = None
    normalized_text: Optional[str] = None
    
    # Video signatures (if applicable)
    video_keyframe_hashes: Optional[List[str]] = None
    audio_fingerprint: Optional[str] = None
    
    # Metadata
    dimensions: Optional[Tuple[int, int]] = None
    file_size: Optional[int] = None
    dominant_colors: Optional[List[str]] = None
    
    created_at: datetime = datetime.now()
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @property
    def composite_key(self) -> str:
        """Unique identifier for this content"""
        return f"{self.perceptual_hash or self.text_exact_hash or self.content_id}"

class PerceptualHasher:
    """Multi-algorithm perceptual hashing"""
    
    def __init__(self):
        self.algorithms = {
            'phash': imagehash.phash,
            'dhash': imagehash.dhash,
            'whash': imagehash.whash,
            'ahash': imagehash.average_hash,
            'chash': self._color_hash
        }
    
    def _color_hash(self, image: Image.Image) -> imagehash.ImageHash:
        """Custom color histogram hash"""
        # Resize to small grid
        small = image.resize((8, 8))
        
        # Get color histogram
        histogram = small.histogram()
        
        # Create hash from histogram bins
        hash_str = ''.join(str(b % 16) for b in histogram[:64])
        return imagehash.hex_to_hash(hash_str)
    
    async def compute_image_hash(self, image_data: bytes) -> Dict[str, str]:
        """Compute all hash types"""
        try:
            img = Image.open(io.BytesIO(image_data))
            
            # Convert to RGB if necessary
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            hashes = {}
            for name, func in self.algorithms.items():
                try:
                    hash_obj = func(img)
                    hashes[name] = str(hash_obj)
                except Exception as e:
                    hashes[name] = None
            
            # Additional metadata
            hashes['dimensions'] = img.size
            
            # Dominant colors (k-means)
            hashes['dominant_colors'] = self._extract_dominant_colors(img)
            
            return hashes
            
        except Exception as e:
            return {'error': str(e)}
    
    def _extract_dominant_colors(self, image: Image.Image, k: int = 3) -> List[str]:
        """Extract k dominant colors using k-means"""
        # Resize for speed
        small = image.resize((150, 150))
        data = np.array(small)
        data = data.reshape((-1, 3))
        
        # Convert to float
        data = np.float32(data)
        
        # K-means criteria and apply
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(data, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)
        
        # Convert centers to hex colors
        colors = []
        for center in centers:
            r, g, b = int(center[0]), int(center[1]), int(center[2])
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        
        return colors
    
    def compute_similarity(self, hash1: str, hash2: str, algorithm: str = 'phash') -> float:
        """Compute similarity between two hashes (0-1)"""
        if not hash1 or not hash2:
            return 0.0
        
        try:
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)
            
            # Hamming distance
            distance = h1 - h2
            
            # Max distance for 64-bit hash is 64
            similarity = 1 - (distance / 64.0)
            return max(0, similarity)
            
        except Exception:
            return 0.0

class SemanticTextAnalyzer:
    """TF-IDF based text similarity"""
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            max_features=5000,
            stop_words='english',
            ngram_range=(1, 2),
            lowercase=True,
            strip_accents='unicode'
        )
        self._cache: Dict[str, np.ndarray] = {}
    
    def normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        # Lowercase
        text = text.lower()
        
        # Remove extra whitespace
        text = ' '.join(text.split())
        
        # Remove common filler words specific to adult content
        fillers = ['hot', 'sexy', 'beautiful', 'amazing', 'watch', 'click', 'now']
        for filler in fillers:
            text = text.replace(f' {filler} ', ' ')
        
        return text.strip()
    
    def compute_signature(self, text: str) -> Dict:
        """Compute semantic signature"""
        normalized = self.normalize_text(text)
        
        # Exact hash
        exact_hash = hashlib.sha256(normalized.encode()).hexdigest()[:32]
        
        # Semantic vector (simplified - in production use sentence-transformers)
        words = normalized.split()
        vector = np.zeros(100)  # Simplified - use actual embeddings
        
        for i, word in enumerate(words[:100]):
            vector[i % 100] = hash(word) % 1000 / 1000
        
        return {
            'normalized': normalized,
            'exact_hash': exact_hash,
            'semantic_vector': vector.tolist()
        }
    
    def compute_similarity(self, sig1: Dict, sig2: Dict) -> float:
        """Compute cosine similarity between texts"""
        v1 = np.array(sig1.get('semantic_vector', [])).reshape(1, -1)
        v2 = np.array(sig2.get('semantic_vector', [])).reshape(1, -1)
        
        if v1.size == 0 or v2.size == 0:
            return 0.0
        
        return float(cosine_similarity(v1, v2)[0][0])

class ContentDeduplicationEngine:
    """Enterprise-grade deduplication with multi-level checks"""
    
    def __init__(self, redis_client: redis.Redis, db_pool):
        self.redis = redis_client
        self.db = db_pool
        self.hasher = PerceptualHasher()
        self.text_analyzer = SemanticTextAnalyzer()
        
        # Thresholds
        self.EXACT_MATCH_THRESHOLD = 1.0
        self.PERCEPTUAL_THRESHOLD = 0.95  # 95% similar
        self.SEMANTIC_THRESHOLD = 0.90   # 90% text similar
        self.COLOR_THRESHOLD = 0.85      # 85% color match
        
        # Time windows
        self.GLOBAL_DEDUP_WINDOW = timedelta(days=90)  # 90 days global
        self.PER_PLATFORM_WINDOW = timedelta(days=30)  # 30 days per platform
        
    async def fingerprint_content(self, 
                                   content_id: str,
                                   image_url: Optional[str] = None,
                                   text: Optional[str] = None,
                                   video_url: Optional[str] = None) -> ContentSignature:
        """Generate complete fingerprint"""
        
        signature = ContentSignature(content_id=content_id, source_url=image_url or video_url)
        
        # Image fingerprinting
        if image_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url, timeout=10) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            hashes = await self.hasher.compute_image_hash(image_data)
                            
                            signature.perceptual_hash = hashes.get('phash')
                            signature.difference_hash = hashes.get('dhash')
                            signature.wavelet_hash = hashes.get('whash')
                            signature.color_hash = hashes.get('chash')
                            signature.dominant_colors = hashes.get('dominant_colors')
                            signature.dimensions = hashes.get('dimensions')
                            signature.file_size = len(image_data)
            except Exception as e:
                print(f"Image fingerprint failed: {e}")
        
        # Text fingerprinting
        if text:
            text_sig = self.text_analyzer.compute_signature(text)
            signature.text_exact_hash = text_sig['exact_hash']
            signature.normalized_text = text_sig['normalized']
            signature.text_semantic_vector = text_sig['semantic_vector']
        
        # Store signature
        await self._store_signature(signature)
        
        return signature
    
    async def _store_signature(self, signature: ContentSignature):
        """Store in Redis and DB"""
        # Redis for fast lookup
        key = f"fingerprint:{signature.composite_key}"
        await self.redis.setex(
            key,
            int(self.GLOBAL_DEDUP_WINDOW.total_seconds()),
            json.dumps(signature.to_dict())
        )
        
        # PostgreSQL for persistence
        await self.db.execute("""
            INSERT INTO content_signatures 
            (content_id, perceptual_hash, text_hash, signature_json, created_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (content_id) DO UPDATE SET
                perceptual_hash = EXCLUDED.perceptual_hash,
                text_hash = EXCLUDED.text_hash,
                signature_json = EXCLUDED.signature_json,
                created_at = NOW()
        """, 
            signature.content_id,
            signature.perceptual_hash,
            signature.text_exact_hash,
            json.dumps(signature.to_dict())
        )
    
    async def check_duplicate(self, 
                              signature: ContentSignature,
                              platform: Optional[str] = None,
                              check_level: str = 'strict') -> Dict:
        """
        Check if content is duplicate
        
        Levels:
        - strict: Any perceptual match blocks
        - medium: Allow minor variations (crop, resize)
        - loose: Only block exact matches
        """
        
        result = {
            'is_duplicate': False,
            'confidence': 0.0,
            'match_type': None,
            'matched_content': None,
            'recommendation': 'proceed'
        }
        
        # Level 1: Exact hash match (fastest)
        if signature.perceptual_hash:
            exact_match = await self._check_exact_hash(
                signature.perceptual_hash, platform
            )
            if exact_match:
                result['is_duplicate'] = True
                result['confidence'] = 1.0
                result['match_type'] = 'exact_hash'
                result['matched_content'] = exact_match
                result['recommendation'] = 'block'
                return result
        
        # Level 2: Perceptual similarity
        if signature.perceptual_hash:
            similar = await self._check_perceptual_similarity(
                signature.perceptual_hash, platform, check_level
            )
            if similar:
                result['is_duplicate'] = True
                result['confidence'] = similar['similarity']
                result['match_type'] = 'perceptual'
                result['matched_content'] = similar['content_id']
                result['recommendation'] = 'block' if check_level == 'strict' else 'warn'
                
                if check_level == 'strict':
                    return result
        
        # Level 3: Text semantic similarity
        if signature.text_semantic_vector:
            text_similar = await self._check_text_similarity(
                signature.text_semantic_vector, platform
            )
            if text_similar and text_similar['similarity'] > self.SEMANTIC_THRESHOLD:
                result['is_duplicate'] = True
                result['confidence'] = text_similar['similarity']
                result['match_type'] = 'semantic_text'
                result['matched_content'] = text_similar['content_id']
                result['recommendation'] = 'warn'
        
        # Level 4: Color histogram similarity (catches re-colors)
        if signature.color_hash:
            color_match = await self._check_color_similarity(
                signature.color_hash, signature.dominant_colors, platform
            )
            if color_match and color_match['similarity'] > self.COLOR_THRESHOLD:
                result['is_duplicate'] = True
                result['confidence'] = color_match['similarity']
                result['match_type'] = 'color_distribution'
                result['matched_content'] = color_match['content_id']
                result['recommendation'] = 'review'
        
        return result
    
    async def _check_exact_hash(self, phash: str, platform: Optional[str]) -> Optional[Dict]:
        """Check for exact perceptual hash match"""
        
        # Check Redis first
        key = f"fingerprint:{phash}"
        exists = await self.redis.get(key)
        
        if exists:
            data = json.loads(exists)
            
            # Check platform-specific if requested
            if platform:
                platform_key = f"posted:{platform}:{phash}"
                platform_exists = await self.redis.get(platform_key)
                if platform_exists:
                    return {
                        'content_id': data.get('content_id'),
                        'posted_at': platform_exists.decode(),
                        'platform': platform
                    }
            else:
                return {
                    'content_id': data.get('content_id'),
                    'global': True
                }
        
        # Check DB for older content
        row = await self.db.fetchrow("""
            SELECT content_id, created_at 
            FROM content_signatures 
            WHERE perceptual_hash = $1
            AND created_at > NOW() - INTERVAL '90 days'
            LIMIT 1
        """, phash)
        
        if row:
            return {
                'content_id': row['content_id'],
                'created_at': row['created_at']
            }
        
        return None
    
    async def _check_perceptual_similarity(self, 
                                           phash: str, 
                                           platform: Optional[str],
                                           level: str) -> Optional[Dict]:
        """Check for similar images using Hamming distance"""
        
        # Get recent hashes from DB (can't do Hamming in Redis easily)
        threshold = 5 if level == 'strict' else 10 if level == 'medium' else 15
        
        rows = await self.db.fetch("""
            SELECT content_id, perceptual_hash, created_at
            FROM content_signatures 
            WHERE created_at > NOW() - INTERVAL '30 days'
            AND perceptual_hash IS NOT NULL
        """)
        
        best_match = None
        best_similarity = 0
        
        for row in rows:
            if row['perceptual_hash']:
                similarity = self.hasher.compute_similarity(phash, row['perceptual_hash'])
                
                # Convert similarity to Hamming distance estimate
                hamming = int((1 - similarity) * 64)
                
                if hamming <= threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_match = row
        
        if best_match:
            return {
                'content_id': best_match['content_id'],
                'similarity': best_similarity,
                'hamming_distance': int((1 - best_similarity) * 64),
                'created_at': best_match['created_at']
            }
        
        return None
    
    async def _check_text_similarity(self, 
                                      vector: List[float], 
                                      platform: Optional[str]) -> Optional[Dict]:
        """Check for semantically similar text"""
        
        # Get recent text signatures
        rows = await self.db.fetch("""
            SELECT content_id, signature_json 
            FROM content_signatures 
            WHERE created_at > NOW() - INTERVAL '7 days'
            AND text_hash IS NOT NULL
        """)
        
        best_match = None
        best_similarity = 0
        
        for row in rows:
            sig = json.loads(row['signature_json'])
            other_vector = sig.get('text_semantic_vector')
            
            if other_vector:
                sim = self.text_analyzer.compute_similarity(
                    {'semantic_vector': vector},
                    {'semantic_vector': other_vector}
                )
                
                if sim > self.SEMANTIC_THRESHOLD and sim > best_similarity:
                    best_similarity = sim
                    best_match = row
        
        if best_match:
            return {
                'content_id': best_match['content_id'],
                'similarity': best_similarity
            }
        
        return None
    
    async def _check_color_similarity(self,
                                       color_hash: str,
                                       dominant_colors: List[str],
                                       platform: Optional[str]) -> Optional[Dict]:
        """Check for similar color distributions"""
        
        # This is a simplified check - in production use proper color histogram comparison
        rows = await self.db.fetch("""
            SELECT content_id, signature_json 
            FROM content_signatures 
            WHERE created_at > NOW() - INTERVAL '30 days'
            AND color_hash IS NOT NULL
        """)
        
        for row in rows:
            sig = json.loads(row['signature_json'])
            other_colors = sig.get('dominant_colors', [])
            
            # Jaccard similarity on color sets
            if other_colors and dominant_colors:
                intersection = len(set(dominant_colors) & set(other_colors))
                union = len(set(dominant_colors) | set(other_colors))
                
                similarity = intersection / union if union > 0 else 0
                
                if similarity > 0.6:  # At least 60% color overlap
                    return {
                        'content_id': row['content_id'],
                        'similarity': similarity
                    }
        
        return None
    
    async def mark_posted(self, 
                          signature: ContentSignature, 
                          platform: str,
                          post_id: str):
        """Mark content as posted on platform"""
        
        # Set platform-specific key with TTL
        key = f"posted:{platform}:{signature.composite_key}"
        await self.redis.setex(
            key,
            int(self.PER_PLATFORM_WINDOW.total_seconds()),
            json.dumps({
                'post_id': post_id,
                'posted_at': datetime.now().isoformat()
            })
        )
        
        # Update DB
        await self.db.execute("""
            INSERT INTO content_platform_posts 
            (content_id, platform, post_id, posted_at)
            VALUES ($1, $2, $3, NOW())
        """, signature.content_id, platform, post_id)
    
    async def get_content_variants(self, 
                                    signature: ContentSignature,
                                    n_variants: int = 5) -> List[Dict]:
        """Get visually similar but not identical content (for A/B testing)"""
        
        # Find content with similar color scheme but different composition
        rows = await self.db.fetch("""
            SELECT content_id, signature_json 
            FROM content_signatures 
            WHERE created_at > NOW() - INTERVAL '30 days'
            AND content_id != $1
            ORDER BY RANDOM()
            LIMIT 50
        """, signature.content_id)
        
        variants = []
        for row in rows:
            sig = json.loads(row['signature_json'])
            
            # Check color similarity but NOT perceptual similarity
            if sig.get('dominant_colors') and signature.dominant_colors:
                color_sim = len(
                    set(sig['dominant_colors']) & set(signature.dominant_colors)
                ) / len(set(sig['dominant_colors']) | set(signature.dominant_colors))
                
                # Different image but similar colors
                if color_sim > 0.5:
                    phash_sim = self.hasher.compute_similarity(
                        signature.perceptual_hash or '',
                        sig.get('perceptual_hash', '')
                    )
                    
                    if phash_sim < 0.8:  # Not too similar
                        variants.append({
                            'content_id': row['content_id'],
                            'color_similarity': color_sim,
                            'visual_difference': 1 - phash_sim
                        })
            
            if len(variants) >= n_variants:
                break
        
        return variants

# Integration with posting pipeline
class DeduplicationMiddleware:
    """Middleware to check content before posting"""
    
    def __init__(self, engine: ContentDeduplicationEngine):
        self.engine = engine
        
    async def process(self, content: Dict, platform: str) -> Dict:
        """Check and potentially block content"""
        
        # Generate fingerprint
        signature = await self.engine.fingerprint_content(
            content_id=content['id'],
            image_url=content.get('image_url'),
            text=content.get('caption')
        )
        
        # Check duplicate
        result = await self.engine.check_duplicate(
            signature, 
            platform=platform,
            check_level='strict'  # Be conservative
        )
        
        if result['is_duplicate']:
            if result['recommendation'] == 'block':
                raise DuplicateContentError(
                    f"Content {content['id']} blocked: {result['match_type']} "
                    f"match with {result['matched_content']}"
                )
            elif result['recommendation'] == 'warn':
                # Log but allow
                print(f"WARNING: Similar content detected ({result['confidence']:.2f})")
        
        # Get variants for A/B test
        if content.get('ab_test'):
            variants = await self.engine.get_content_variants(signature, n_variants=3)
            content['variants'] = variants
        
        return {
            'content': content,
            'signature': signature,
            'duplicate_check': result
        }
    
    async def post_posted(self, 
                          signature: ContentSignature, 
                          platform: str,
                          post_id: str):
        """Mark as posted after successful publish"""
        await self.engine.mark_posted(signature, platform, post_id)

class DuplicateContentError(Exception):
    pass
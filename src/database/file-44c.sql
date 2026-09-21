-- Core Tables
CREATE TABLE sub_affiliates (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100),
    platform VARCHAR(50), -- reddit, twitter, telegram, etc
    niche VARCHAR(50),
    status VARCHAR(20), -- active, paused, banned
    created_at TIMESTAMP DEFAULT NOW(),
    settings JSONB
);

CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200),
    sub_affiliate_id VARCHAR(50) REFERENCES sub_affiliates(id),
    tags TEXT[], -- ['milf', 'teen', 'blonde']
    posting_schedule JSONB, -- {hours: [14, 20, 22], interval_min: 30}
    content_sources TEXT[], -- ['rss', 'video_api']
    auto_optimize BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE content_pool (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50), -- rss, video_api, manual
    external_id VARCHAR(200), -- video ID from AWE
    title TEXT,
    performer VARCHAR(100),
    tags TEXT[],
    thumbnail_url TEXT,
    video_url TEXT,
    target_url TEXT, -- affiliate link
    quality VARCHAR(10),
    duration INT,
    fetched_at TIMESTAMP DEFAULT NOW(),
    used_count INT DEFAULT 0,
    performance_score FLOAT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'available' -- available, used, banned
);

CREATE TABLE posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID REFERENCES campaigns(id),
    sub_affiliate_id VARCHAR(50) REFERENCES sub_affiliates(id),
    content_id UUID REFERENCES content_pool(id),
    platform VARCHAR(50),
    platform_post_id VARCHAR(200), -- Reddit post ID, Tweet ID, etc
    caption TEXT,
    media_urls TEXT[],
    posted_at TIMESTAMP,
    clicks INT DEFAULT 0,
    conversions INT DEFAULT 0,
    revenue FLOAT DEFAULT 0,
    engagement JSONB -- {upvotes: 10, comments: 3, shares: 1}
);

CREATE TABLE analytics_daily (
    date DATE PRIMARY KEY,
    sub_affiliate_id VARCHAR(50),
    clicks INT DEFAULT 0,
    unique_clicks INT DEFAULT 0,
    conversions INT DEFAULT 0,
    revenue FLOAT DEFAULT 0,
    costs FLOAT DEFAULT 0, -- proxy, hosting, etc
    content_pieces_posted INT DEFAULT 0,
    top_performers JSONB,
    hourly_breakdown JSONB
);

CREATE TABLE optimization_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMP DEFAULT NOW(),
    sub_affiliate_id VARCHAR(50),
    action VARCHAR(50), -- increase_volume, decrease_volume, pause, rotate_tags
    reason TEXT,
    metrics_before JSONB,
    metrics_after JSONB,
    auto_applied BOOLEAN DEFAULT false
);
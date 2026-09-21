CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(50) NOT NULL,
    username VARCHAR(100) UNIQUE NOT NULL,
    credentials JSONB NOT NULL,
    stage VARCHAR(50) DEFAULT 'created',
    sub_stage VARCHAR(50),
    health_score INT DEFAULT 50,
    karma_score INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    warming_started TIMESTAMP,
    warming_progress TEXT,
    last_used TIMESTAMP,
    stage_changed_at TIMESTAMP DEFAULT NOW(),
    proxy_assigned VARCHAR(255),
    purchase_info JSONB,
    metadata JSONB
);

CREATE TABLE account_stage_history (
    id SERIAL PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    stage VARCHAR(50),
    transitioned_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE account_bans (
    id SERIAL PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    detected_at TIMESTAMP,
    reason TEXT,
    recovery_attempted BOOLEAN DEFAULT FALSE,
    recovered_at TIMESTAMP,
    recovery_method VARCHAR(100)
);

CREATE TABLE account_metrics (
    id SERIAL PRIMARY KEY,
    account_id UUID REFERENCES accounts(id),
    recorded_at TIMESTAMP DEFAULT NOW(),
    total_posts INT DEFAULT 0,
    total_engagement INT DEFAULT 0,
    reported_posts INT DEFAULT 0,
    removed_posts INT DEFAULT 0,
    rate_limit_hits INT DEFAULT 0,
    shadow_ban_score FLOAT DEFAULT 0
);
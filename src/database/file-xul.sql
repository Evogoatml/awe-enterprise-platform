CREATE TABLE action_log (
    id SERIAL PRIMARY KEY,
    platform VARCHAR(50),
    action_type VARCHAR(50),
    account_id UUID REFERENCES accounts(id),
    result VARCHAR(20), -- success, failed, blocked
    created_at TIMESTAMP DEFAULT NOW(),
    duration_ms INT,
    error TEXT
);

CREATE INDEX idx_action_log_time ON action_log(platform, action_type, created_at);
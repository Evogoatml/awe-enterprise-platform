# In your Campaign model, add Reddit-specific config
CAMPAIGN_CONFIG = {
    'sub_affiliate_id': '072344512-rd',
    'platform': 'reddit',
    'reddit_config': {
        'library': 'voussoir',  # vs 'praw'
        'posting_strategy': 'link',  # link, image, text
        'target_subreddits': ['camgirls', 'nsfw_gifs', 'adultvideos'],
        'auto_comment': True,
        'comment_templates': [
            "She's amazing 🔥",
            "Full show is worth it",
            "One of my favorites"
        ],
        'karma_threshold': 100,  # Min karma before posting
        'account_rotation': True
    }
}

# Register in PlatformManager
platform_manager.register_bot(
    '072344512-rd', 
    RedditBotVoussoir(credentials, proxy)
)
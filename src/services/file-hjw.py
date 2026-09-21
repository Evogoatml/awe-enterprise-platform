class SubredditTargeter:
    """Find and target optimal subreddits"""
    
    def __init__(self, reddit_bot):
        self.bot = reddit_bot
        
    async def find_niche_subreddits(self, keywords: list) -> list:
        """Search for relevant NSFW subreddits"""
        subreddits = []
        
        for keyword in keywords:
            # Search Reddit for subreddits
            results = self.bot.client.search(
                f"subreddit:{keyword} nsfw:yes",
                sort='relevance'
            )
            for sub in results:
                subreddits.append({
                    'name': sub.display_name,
                    'subscribers': sub.subscribers,
                    'nsfw': sub.over18,
                    'activity': self.estimate_activity(sub)
                })
        
        # Filter by size (not too big = buried, not too small = dead)
        return [s for s in subreddits if 10000 < s['subscribers'] < 500000]
    
    async def estimate_activity(self, subreddit) -> str:
        """Estimate how active a subreddit is"""
        hot = list(subreddit.hot(limit=10))
        if len(hot) == 0:
            return 'dead'
        
        avg_score = sum(p.score for p in hot) / len(hot)
        
        if avg_score > 1000:
            return 'high'
        elif avg_score > 100:
            return 'medium'
        return 'low'
    
    async def get_optimal_posting_time(self, subreddit_name: str) -> list:
        """Analyze when subreddit is most active"""
        sub = self.bot.client.get_subreddit(subreddit_name)
        
        # Get top posts from last week
        top = sub.top(time_filter='week', limit=100)
        
        # Analyze posting times
        hours = {}
        for post in top:
            hour = datetime.fromtimestamp(post.created_utc).hour
            hours[hour] = hours.get(hour, 0) + 1
        
        # Return top 3 hours
        sorted_hours = sorted(hours.items(), key=lambda x: x[1], reverse=True)
        return [h[0] for h in sorted_hours[:3]]
# services/platform_bots.py
from voussoir.reddit import Reddit as VoussoirReddit
from voussoir.reddit import Submission

class RedditBotVoussoir(PlatformBot):
    """Reddit bot using voussoir/reddit instead of PRAW"""
    
    def __init__(self, credentials, proxy):
        super().__init__(credentials, proxy)
        self.client = None
        
    async def start(self):
        """Initialize voussoir client"""
        self.client = VoussoirReddit(
            username=self.credentials['username'],
            password=self.credentials['password'],
            client_id=self.credentials['client_id'],
            client_secret=self.credentials['client_secret'],
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'
        )
        
        # Set proxy if provided
        if self.proxy:
            self.client.session.proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
    
    async def post(self, content: dict) -> str:
        """Submit post using voussoir"""
        await self.rate_limiter.acquire()
        
        try:
            if content.get('post_type') == 'image':
                # Image post
                submission = self.client.submit(
                    subreddit=content['subreddit'],
                    title=content['title'],
                    file_path=content['image_path'],
                    nsfw=True
                )
            elif content.get('post_type') == 'link':
                # Link post to affiliate
                submission = self.client.submit(
                    subreddit=content['subreddit'],
                    title=content['title'],
                    url=content['link'],
                    nsfw=True
                )
            else:
                # Text post with link in body
                submission = self.client.submit(
                    subreddit=content['subreddit'],
                    title=content['title'],
                    body=f"{content['caption']}\n\n[Watch here]({content['link']})",
                    nsfw=True
                )
            
            return submission.id
            
        except Exception as e:
            logger.error(f"Post failed: {e}")
            raise
    
    async def comment(self, post_id: str, text: str):
        """Add comment to drive engagement"""
        submission = self.client.get_submission(post_id)
        comment = submission.reply(text)
        return comment.id
    
    async def upvote(self, post_id: str):
        """Upvote to boost visibility"""
        submission = self.client.get_submission(post_id)
        submission.upvote()
    
    async def check_ban_status(self) -> bool:
        """Check if account is functional"""
        try:
            me = self.client.get_me()
            return me is not None
        except:
            return False
    
    async def get_karma(self) -> dict:
        """Get account karma for health monitoring"""
        me = self.client.get_me()
        return {
            'link_karma': me.link_karma,
            'comment_karma': me.comment_karma,
            'total_karma': me.link_karma + me.comment_karma
        }
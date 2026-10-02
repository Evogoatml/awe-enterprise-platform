import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import aiohttp
from src.services.analytics import (
    AnalyticsConfigurationError,
    AnalyticsDataError,
    AnalyticsEngine,
)
from src.services.content_ingestion import (
    ContentIngestionService,
    IngestionConfigurationError,
    IngestionSchemaError,
)
from src.services.optimization import ActionType, OptimizationEngine
from src.workers.scheduler import CampaignScheduler, SchedulerConfigurationError


AS_OF = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
RSS_FIXTURE = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel><title>Example</title>
    <item>
      <guid>rss-guid-1</guid><title>Featured scene</title>
      <link>https://example.test/watch/1</link>
      <author>Example Performer</author>
      <category>drama</category><category>featured</category>
      <media:thumbnail url="https://example.test/image.jpg" />
    </item>
  </channel>
</rss>"""
VIDEO_FIXTURE = json.dumps(
    {
        "data": {
            "videos": [
                {
                    "id": "video-1",
                    "title": "Video scene",
                    "uploader": "Video Performer",
                    "tags": ["featured", "drama"],
                    "profileImage": "//example.test/video.jpg",
                    "targetUrl": "https://example.test/video/1",
                    "quality": "hd",
                }
            ]
        }
    }
)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def raise_for_status(self):
        if isinstance(self.body, Exception):
            raise self.body
        return None

    async def text(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = {
            url: list(response) if isinstance(response, list) else [response]
            for url, response in responses.items()
        }
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        responses = self.responses[url]
        return FakeResponse(responses.pop(0) if len(responses) > 1 else responses[0])


class FakeDatabase:
    def __init__(self, as_of=AS_OF, current_revenue=300, previous_revenue=100):
        self.as_of = as_of.replace(tzinfo=None)
        self.current_revenue = current_revenue
        self.previous_revenue = previous_revenue
        self.content = []
        self.applied_keys = set()
        self.statements = []

    async def execute(self, statement, *args):
        self.statements.append((statement, args))
        if "INSERT INTO content_pool" in statement:
            self.content.append(args)
            return "INSERT 0 1"
        if "WITH inserted AS" in statement:
            key = args[3]
            if key in self.applied_keys:
                return "UPDATE 0"
            self.applied_keys.add(key)
            return "UPDATE 1"
        raise AssertionError("Unexpected database statement")

    async def fetchrow(self, query, *args):
        if "COUNT(*) AS total_posts" in query:
            return {
                "total_posts": 25,
                "total_clicks": 100,
                "total_conversions": 10,
                "total_revenue": 300,
            }
        if "SUM(costs)" in query:
            return {"total_cost": 100}
        if "SUM(revenue)" in query:
            end = args[2]
            revenue = (
                self.current_revenue
                if end == self.as_of
                else self.previous_revenue
            )
            return {"total_revenue": revenue}
        raise AssertionError("Unexpected analytics query")

    async def fetch(self, query, *args):
        if "GROUP BY c.id, c.title" in query:
            return [{"content_id": "content-1", "title": "Featured scene", "revenue": 300}]
        if "ranked_tags" in query:
            return [{"tag": "featured"}, {"tag": "drama"}]
        raise AssertionError("Unexpected database query")


class FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue_at(self, when, function, *args, **kwargs):
        job = {"when": when, "function": function, "args": args, "kwargs": kwargs}
        self.jobs.append(job)
        return job


class FakePlatform:
    async def post_to_campaign(self, campaign_id, post_data):
        self.last_post = (campaign_id, post_data)
        return "platform-post-1"


class ContentPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_rss_and_video_api_are_normalized_and_schema_checked(self):
        rss_url = "https://example.test/feed.xml"
        api_url = "https://example.test/api/videos"
        session = FakeSession({rss_url: RSS_FIXTURE, api_url: VIDEO_FIXTURE})
        service = ContentIngestionService(
            None,
            {
                "rss_url": rss_url,
                "video_api_url": api_url,
                "psid": "partner",
                "access_key": "access",
            },
            session=session,
            clock=lambda: AS_OF,
            retry_delay_seconds=0,
        )

        rss_items = await service.ingest_rss()
        video_items = await service.ingest_video_api(["featured"])

        self.assertEqual(rss_items[0].id, "rss-guid-1")
        self.assertEqual(rss_items[0].tags, ("drama", "featured"))
        self.assertEqual(rss_items[0].performer, "Example Performer")
        self.assertEqual(rss_items[0].fetched_at, AS_OF.replace(tzinfo=None))
        self.assertEqual(video_items[0].thumbnail, "https://example.test/video.jpg")
        self.assertEqual(session.requests[1][1]["params"]["accessKey"], "access")
        self.assertEqual(session.requests[1][1]["params"]["tags"], "featured")

        with self.assertRaises(IngestionConfigurationError):
            await ContentIngestionService(None, {}, session=session).ingest_rss()
        invalid = FakeSession({api_url: '{"data": {"videos": [{}]}}'})
        invalid_service = ContentIngestionService(
            None,
            {"video_api_url": api_url, "psid": "p", "access_key": "k"},
            session=invalid,
        )
        with self.assertRaises(IngestionSchemaError):
            await invalid_service.ingest_video_api(["tag"])

    async def test_rss_rejects_invalid_and_entity_expanding_documents(self):
        for body in ("<rss>", "<!DOCTYPE x [<!ENTITY e 'bad'>]><rss>&e;</rss>"):
            service = ContentIngestionService(
                None,
                {"rss_url": "https://example.test/feed"},
                session=FakeSession({"https://example.test/feed": body}),
            )
            with self.assertRaises(IngestionSchemaError):
                await service.ingest_rss()

    async def test_ingestion_retries_transient_http_errors(self):
        feed_url = "https://example.test/feed.xml"
        unavailable = aiohttp.ClientResponseError(None, (), status=503)
        session = FakeSession({feed_url: [unavailable, RSS_FIXTURE]})
        service = ContentIngestionService(
            None,
            {"rss_url": feed_url},
            session=session,
            max_retries=1,
            retry_delay_seconds=0,
        )

        items = await service.ingest_rss()

        self.assertEqual(len(items), 1)
        self.assertEqual(len(session.requests), 2)

    async def test_analytics_metrics_are_deterministic_and_require_explicit_awe_config(self):
        database = FakeDatabase()
        analytics = AnalyticsEngine(database, clock=lambda: AS_OF)
        metrics = await analytics.calculate_metrics("sub-1", as_of=AS_OF)
        self.assertEqual(metrics["total_posts"], 25)
        self.assertEqual(metrics["epc"], 3.0)
        self.assertEqual(metrics["conversion_rate"], 10.0)
        self.assertEqual(metrics["revenue_per_post"], 12.0)
        self.assertEqual(metrics["roi"], 200.0)
        self.assertEqual(metrics["trend"], "up_trending")
        self.assertEqual(metrics["top_content"][0]["content_id"], "content-1")
        self.assertEqual(analytics.parse_xml("<root><row><sales>12</sales></row></root>"),
                         [{"sales": "12"}])
        with self.assertRaises(AnalyticsDataError):
            analytics.parse_xml("<!DOCTYPE x [<!ENTITY e 'bad'>]><root>&e;</root>")
        with self.assertRaises(AnalyticsConfigurationError):
            await analytics.fetch_awe_stats("sub-1")
        self.assertEqual(
            await AnalyticsEngine(
                FakeDatabase(current_revenue=100, previous_revenue=100),
                clock=lambda: AS_OF,
            ).calculate_trend("sub-1", as_of=AS_OF),
            "stable",
        )
        self.assertEqual(
            await AnalyticsEngine(
                FakeDatabase(current_revenue=50, previous_revenue=100),
                clock=lambda: AS_OF,
            ).calculate_trend("sub-1", as_of=AS_OF),
            "down_trending",
        )

    async def test_optimization_selects_typed_actions_and_is_idempotent(self):
        database = FakeDatabase()

        class AnalyticsFixture:
            async def calculate_metrics(self, _sub_affiliate_id, *, as_of=None):
                return {
                    "total_posts": 25,
                    "epc": 3.0,
                    "conversion_rate": 10.0,
                    "revenue_per_post": 12.0,
                    "roi": 200.0,
                    "trend": "up_trending",
                    "top_content": [],
                }

        optimizer = OptimizationEngine(database, AnalyticsFixture())
        actions = await optimizer.analyze_and_optimize("sub-1", as_of=AS_OF)
        self.assertEqual(actions[0]["type"], ActionType.SCALE_WINNER)
        self.assertEqual(len(database.applied_keys), 1)
        self.assertFalse(
            await optimizer.apply_action("sub-1", actions[0], as_of=AS_OF)
        )
        self.assertTrue(
            await optimizer.apply_action(
                "sub-1", actions[0], as_of=AS_OF.replace(day=16)
            )
        )

        class InsufficientAnalytics:
            async def calculate_metrics(self, _sub_affiliate_id, *, as_of=None):
                return {
                    "total_posts": 2,
                    "epc": 0,
                    "conversion_rate": 0,
                    "trend": "down_trending",
                    "top_content": [],
                }

        no_action = await OptimizationEngine(
            database, InsufficientAnalytics()
        ).analyze_and_optimize("sub-1", as_of=AS_OF)
        self.assertEqual(no_action, [])

        class WeakAnalytics:
            async def calculate_metrics(self, _sub_affiliate_id, *, as_of=None):
                return {
                    "total_posts": 25,
                    "epc": 0.1,
                    "conversion_rate": 0.5,
                    "trend": "stable",
                    "top_content": [],
                }

        weak_actions = await OptimizationEngine(
            database, WeakAnalytics()
        ).analyze_and_optimize("sub-1", as_of=AS_OF)
        self.assertEqual(weak_actions[0]["type"], ActionType.ROTATE_TAGS)
        self.assertEqual(weak_actions[0]["parameters"]["new_tags"], ["featured", "drama"])
        pause = {
            "type": ActionType.PAUSE,
            "reason": "Campaign paused by policy",
            "parameters": {},
        }
        change_times = {
            "type": ActionType.CHANGE_POSTING_TIMES,
            "reason": "Move to best-performing hours",
            "parameters": {"hours": [20, 14, 20]},
        }
        self.assertTrue(await optimizer.apply_action("sub-2", pause, as_of=AS_OF))
        self.assertTrue(
            await optimizer.apply_action("sub-2", change_times, as_of=AS_OF)
        )
        self.assertIn("SET status = 'paused'", database.statements[-2][0])
        self.assertEqual(database.statements[-1][1][-1], [14, 20])

    async def test_scheduler_schedules_and_executes_injected_posting_flow(self):
        database = FakeDatabase()
        queue = FakeQueue()
        campaign = {
            "id": "campaign-1",
            "posting_schedule": {"hours": [14, 20, 14]},
            "target_subreddit": "example",
        }
        platform = FakePlatform()
        logged_posts = []

        async def load_campaign(_campaign_id):
            return campaign

        async def select_content(_campaign):
            return {
                "id": "rss-guid-1",
                "title": "Featured scene",
                "target_url": "https://example.test/watch/1",
                "thumbnail_url": "https://example.test/image.jpg",
            }

        async def generate_caption(content, _campaign):
            return f"Watch {content['title']}"

        async def log_post(*args):
            logged_posts.append(args)

        scheduler = CampaignScheduler(
            queue=queue,
            campaign_loader=load_campaign,
            scheduler_factory_path="src.workers.scheduler:CampaignScheduler",
            content_selector=select_content,
            caption_generator=generate_caption,
            platform_manager=platform,
            post_logger=log_post,
            clock=lambda: AS_OF,
        )
        jobs = await scheduler.add_campaign("campaign-1")
        post_id = await scheduler.execute_posting_cycle("campaign-1")

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["function"].__name__, "run_scheduled_post")
        self.assertEqual(jobs[0]["kwargs"]["retry"].max, 3)
        self.assertEqual(post_id, "platform-post-1")
        self.assertEqual(platform.last_post[1]["subreddit"], "example")
        self.assertEqual(logged_posts, [("campaign-1", "rss-guid-1", post_id)])
        self.assertEqual(database.content, [])

        with self.assertLogs("src.workers.scheduler", level="ERROR"):
            with self.assertRaises(SchedulerConfigurationError):
                await CampaignScheduler(
                    queue=queue,
                    campaign_loader=load_campaign,
                    scheduler_factory_path="src.workers.scheduler:CampaignScheduler",
                ).execute_posting_cycle("campaign-1")

        campaign["status"] = "paused"
        self.assertEqual(await scheduler.add_campaign("campaign-1"), [])
        self.assertIsNone(await scheduler.execute_posting_cycle("campaign-1"))

    async def test_rq_entrypoint_propagates_failures_for_retry(self):
        from src.workers.scheduler import run_scheduled_post

        class FailedJob:
            async def execute_posting_cycle(self, _campaign_id):
                raise RuntimeError("transient posting failure")

        with patch(
            "src.workers.scheduler._load_factory", return_value=lambda: FailedJob()
        ):
            with self.assertLogs("src.workers.scheduler", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "transient posting failure"):
                    await asyncio.to_thread(
                        run_scheduled_post, "campaign-1", "test.module:factory"
                    )

    async def test_full_content_slice_runs_from_ingestion_to_posting(self):
        feed_url = "https://example.test/feed.xml"
        database = FakeDatabase()
        ingestion = ContentIngestionService(
            database,
            {"rss_url": feed_url},
            session=FakeSession({feed_url: RSS_FIXTURE}),
            clock=lambda: AS_OF,
        )
        content_items = await ingestion.ingest_rss()
        self.assertEqual(await ingestion.store_content(content_items), 1)

        analytics = AnalyticsEngine(database, clock=lambda: AS_OF)
        optimizer = OptimizationEngine(database, analytics)
        actions = await optimizer.analyze_and_optimize("sub-1", as_of=AS_OF)

        campaign = {
            "posting_schedule": {"hours": [14]},
            "target_subreddit": "example",
        }
        posted = []

        async def select_ingested_content(_campaign):
            item = content_items[0]
            return {
                "id": item.id,
                "title": item.title,
                "target_url": item.target_url,
                "thumbnail_url": item.thumbnail,
            }

        scheduler = CampaignScheduler(
            queue=FakeQueue(),
            campaign_loader=lambda _campaign_id: campaign,
            scheduler_factory_path="src.workers.scheduler:CampaignScheduler",
            content_selector=select_ingested_content,
            caption_generator=lambda content, _campaign: content["title"],
            platform_manager=FakePlatform(),
            post_logger=lambda *args: posted.append(args),
            clock=lambda: AS_OF,
        )
        post_id = await scheduler.execute_posting_cycle("campaign-1")

        self.assertEqual(actions[0]["type"], ActionType.SCALE_WINNER)
        self.assertTrue(any("WITH inserted AS" in sql for sql, _ in database.statements))
        self.assertEqual(post_id, "platform-post-1")
        self.assertEqual(posted[0][1], "rss-guid-1")

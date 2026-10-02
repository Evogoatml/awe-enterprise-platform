"""Queue-backed campaign scheduling with an injectable posting pipeline."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping

from rq import Queue, Retry

logger = logging.getLogger(__name__)


class SchedulerConfigurationError(RuntimeError):
    """Raised when the worker pipeline is not configured."""


def _load_factory(factory_path: str) -> Callable[[], Any]:
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise SchedulerConfigurationError(
            "scheduler_factory_path must use 'python.module:factory_name' format"
        )
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise SchedulerConfigurationError(
            f"Unable to load scheduler factory {factory_path!r}"
        ) from exc
    if not callable(factory):
        raise SchedulerConfigurationError("scheduler factory must be callable")
    return factory


def run_scheduled_post(campaign_id: str, scheduler_factory_path: str) -> None:
    """Synchronous RQ entry point; exceptions propagate so RQ can retry/fail the job."""
    logger.info(
        "campaign_job_started",
        extra={"event": "campaign_job_started", "campaign_id": campaign_id},
    )
    try:
        scheduler = _load_factory(scheduler_factory_path)()
        asyncio.run(scheduler.execute_posting_cycle(campaign_id))
    except Exception:
        logger.exception(
            "campaign_job_failed",
            extra={"event": "campaign_job_failed", "campaign_id": campaign_id},
        )
        raise
    logger.info(
        "campaign_job_completed",
        extra={"event": "campaign_job_completed", "campaign_id": campaign_id},
    )


class CampaignScheduler:
    """Schedule campaign slots and execute the injected content-to-post workflow.

    ``scheduler_factory_path`` is imported inside each RQ worker process. The
    factory must construct this scheduler with that process's database and
    platform integrations; unavailable integrations fail explicitly.
    """

    def __init__(
        self,
        *,
        queue: Queue,
        campaign_loader: Callable[[str], Any],
        scheduler_factory_path: str,
        content_selector: Callable[[Mapping[str, Any]], Any] | None = None,
        caption_generator: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None,
        platform_manager: Any = None,
        post_logger: Callable[[str, str, str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _load_factory(scheduler_factory_path)
        self.queue = queue
        self.campaign_loader = campaign_loader
        self.scheduler_factory_path = scheduler_factory_path
        self.content_selector = content_selector
        self.caption_generator = caption_generator
        self.platform_manager = platform_manager
        self.post_logger = post_logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def get_next_occurrence(hour: int, now: datetime) -> datetime:
        if not 0 <= hour <= 23:
            raise ValueError("posting hour must be between 0 and 23")
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        candidate = datetime.combine(
            now.date(), time(hour=hour), tzinfo=now.tzinfo
        )
        return candidate if candidate > now else candidate + timedelta(days=1)

    async def _resolve(self, result: Any) -> Any:
        return await result if inspect.isawaitable(result) else result

    async def add_campaign(self, campaign_id: str) -> list[Any]:
        if not campaign_id:
            raise ValueError("campaign_id must not be empty")
        campaign = await self._resolve(self.campaign_loader(campaign_id))
        if not isinstance(campaign, Mapping):
            raise SchedulerConfigurationError("campaign loader must return a mapping")
        if campaign.get("status") == "paused":
            logger.info(
                "campaign_jobs_skipped_paused",
                extra={"event": "campaign_jobs_skipped_paused", "campaign_id": campaign_id},
            )
            return []
        schedule = campaign.get("posting_schedule")
        if not isinstance(schedule, Mapping):
            raise SchedulerConfigurationError("campaign has no posting_schedule mapping")
        hours = schedule.get("hours", [])
        if not isinstance(hours, list) or any(
            not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23
            for hour in hours
        ):
            raise SchedulerConfigurationError(
                "posting_schedule.hours must be a list of hours from 0 to 23"
            )

        retry = Retry(max=3, interval=[60, 300, 900])
        jobs = []
        for hour in sorted(set(hours)):
            next_run = self.get_next_occurrence(hour, self.clock())
            jobs.append(
                await asyncio.to_thread(
                    self.queue.enqueue_at,
                    next_run,
                    run_scheduled_post,
                    campaign_id,
                    self.scheduler_factory_path,
                    retry=retry,
                )
            )
        logger.info(
            "campaign_jobs_scheduled",
            extra={
                "event": "campaign_jobs_scheduled",
                "campaign_id": campaign_id,
                "job_count": len(jobs),
            },
        )
        return jobs

    async def execute_posting_cycle(self, campaign_id: str) -> str | None:
        logger.info(
            "campaign_posting_cycle_started",
            extra={"event": "campaign_posting_cycle_started", "campaign_id": campaign_id},
        )
        try:
            campaign = await self._resolve(self.campaign_loader(campaign_id))
            if not isinstance(campaign, Mapping):
                raise SchedulerConfigurationError("campaign loader must return a mapping")
            if campaign.get("status") == "paused":
                logger.info(
                    "campaign_posting_cycle_skipped_paused",
                    extra={
                        "event": "campaign_posting_cycle_skipped_paused",
                        "campaign_id": campaign_id,
                    },
                )
                return None
            if self.content_selector is None or self.caption_generator is None:
                raise SchedulerConfigurationError(
                    "content selector and caption generator must be configured"
                )
            if self.platform_manager is None or self.post_logger is None:
                raise SchedulerConfigurationError(
                    "platform manager and post logger must be configured"
                )

            content = await self._resolve(self.content_selector(campaign))
            if not isinstance(content, Mapping):
                raise SchedulerConfigurationError(
                    "content selector must return a content mapping"
                )
            caption = await self._resolve(self.caption_generator(content, campaign))
            if not isinstance(caption, str) or not caption.strip():
                raise SchedulerConfigurationError("caption generator returned no caption")
            post_data = {
                "title": caption,
                "link": content.get("target_url"),
                "image": content.get("thumbnail_url", content.get("thumbnail", "")),
            }
            if campaign.get("target_subreddit"):
                post_data["subreddit"] = campaign["target_subreddit"]
            if not post_data["link"]:
                raise SchedulerConfigurationError("selected content has no target_url")

            post_id = await self._resolve(
                self.platform_manager.post_to_campaign(campaign_id, post_data)
            )
            if not isinstance(post_id, str) or not post_id:
                raise SchedulerConfigurationError("platform did not return a post id")
            content_id = str(content.get("id") or content.get("external_id") or "")
            if not content_id:
                raise SchedulerConfigurationError("selected content has no id")
            await self._resolve(self.post_logger(campaign_id, content_id, post_id))
        except Exception:
            logger.exception(
                "campaign_posting_cycle_failed",
                extra={"event": "campaign_posting_cycle_failed", "campaign_id": campaign_id},
            )
            raise

        logger.info(
            "campaign_posting_cycle_completed",
            extra={
                "event": "campaign_posting_cycle_completed",
                "campaign_id": campaign_id,
                "post_id": post_id,
                "content_id": content_id,
            },
        )
        return post_id

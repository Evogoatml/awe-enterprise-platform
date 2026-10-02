"""Fetch and normalize RSS and video-promotion content."""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit

import aiohttp

logger = logging.getLogger(__name__)
DEFAULT_VIDEO_API_URL = "https://pt.ptawe.com/api/video-promotion/v1/list"


class IngestionError(RuntimeError):
    """Base class for content ingestion failures."""


class IngestionConfigurationError(IngestionError):
    """Raised when a source is not configured."""


class IngestionSchemaError(IngestionError):
    """Raised when a source response does not match its documented schema."""


@dataclass(frozen=True)
class ContentItem:
    id: str
    title: str
    performer: str
    tags: tuple[str, ...]
    thumbnail: str
    target_url: str
    quality: str
    source: str
    fetched_at: datetime


class ContentIngestionService:
    def __init__(
        self,
        db: Any,
        awe_config: Mapping[str, Any],
        *,
        session: Any = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.25,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        self.db = db
        self.awe = awe_config
        self.session = session
        self._owns_session = session is None
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def start(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True

    async def close(self) -> None:
        if self.session is not None and self._owns_session:
            await self.session.close()
            self.session = None

    async def _get_text(self, url: str, *, params: Mapping[str, Any] | None = None) -> str:
        await self.start()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self.session.get(
                    url, params=params, timeout=self.timeout
                ) as response:
                    response.raise_for_status()
                    return await response.text()
            except aiohttp.ClientResponseError as exc:
                last_error = exc
                if exc.status < 500 and exc.status != 429:
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay_seconds * (attempt + 1))
        logger.warning(
            "content_source_request_failed",
            extra={
                "event": "content_source_request_failed",
                "host": self._source_host(url),
                "attempts": self.max_retries + 1,
                "error_type": type(last_error).__name__ if last_error else "unknown",
            },
        )
        raise IngestionError("Content source request failed") from None

    def _fetched_at(self) -> datetime:
        value = self.clock()
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def _source_host(url: str) -> str:
        try:
            return urlsplit(url).hostname or "unknown"
        except ValueError:
            return "unknown"

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].split(":")[-1]

    @classmethod
    def _child_text(cls, element: ET.Element, name: str) -> str:
        for child in element.iter():
            if cls._local_name(child.tag) == name and child.text:
                return child.text.strip()
        return ""

    @staticmethod
    def _http_url(value: str, field: str, base_url: str = "") -> str:
        resolved = urljoin(base_url, value.strip())
        try:
            parsed = urlsplit(resolved)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            raise IngestionSchemaError(f"{field} must be an HTTP(S) URL")
        return resolved

    @classmethod
    def _rss_item(
        cls, element: ET.Element, fetched_at: datetime, base_url: str
    ) -> ContentItem:
        title = cls._child_text(element, "title")
        raw_link = cls._child_text(element, "link")
        link = cls._http_url(raw_link, "RSS link", base_url) if raw_link else ""
        item_id = cls._child_text(element, "guid") or link
        if not item_id or not title or not link:
            raise IngestionSchemaError("RSS item requires a title and link or guid")

        tags = tuple(
            child.text.strip()
            for child in element
            if cls._local_name(child.tag) == "category" and child.text and child.text.strip()
        )
        thumbnail = ""
        for child in element.iter():
            if cls._local_name(child.tag) in {"thumbnail", "content"}:
                thumbnail = child.attrib.get("url", "")
                if thumbnail:
                    break
        if not thumbnail:
            for child in element:
                if cls._local_name(child.tag) == "enclosure":
                    thumbnail = child.attrib.get("url", "")
                    break
        if thumbnail:
            thumbnail = cls._http_url(thumbnail, "RSS thumbnail", link)

        return ContentItem(
            id=item_id,
            title=title,
            performer=cls._child_text(element, "creator")
            or cls._child_text(element, "author")
            or "Unknown",
            tags=tags,
            thumbnail=thumbnail,
            target_url=link,
            quality="unknown",
            source="rss",
            fetched_at=fetched_at,
        )

    async def ingest_rss(self) -> list[ContentItem]:
        """Fetch an RSS 2.0 feed and normalize each ``item`` entry."""
        rss_url = self.awe.get("rss_url")
        if not isinstance(rss_url, str) or not rss_url.strip():
            raise IngestionConfigurationError("rss_url is required for RSS ingestion")
        try:
            rss_url = self._http_url(rss_url.strip(), "rss_url")
        except IngestionSchemaError as exc:
            raise IngestionConfigurationError(
                "rss_url must be an HTTP(S) URL"
            ) from exc

        body = await self._get_text(rss_url)
        if "<!doctype" in body.lower() or "<!entity" in body.lower():
            raise IngestionSchemaError("RSS documents must not declare entities")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise IngestionSchemaError("RSS response is not valid XML") from exc
        elements = [node for node in root.iter() if self._local_name(node.tag) == "item"]
        fetched_at = self._fetched_at()
        try:
            return [
                self._rss_item(item, fetched_at, rss_url.strip())
                for item in elements
            ]
        except IngestionSchemaError as exc:
            raise IngestionSchemaError(f"Invalid RSS item: {exc}") from exc

    @staticmethod
    def _video_item(video: Any, fetched_at: datetime) -> ContentItem:
        if not isinstance(video, dict):
            raise IngestionSchemaError("video entries must be objects")
        required = ("id", "title", "targetUrl")
        if any(video.get(field) in (None, "") for field in required):
            raise IngestionSchemaError("video entries require id, title, and targetUrl")
        raw_tags = video.get("tags", [])
        if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
            raise IngestionSchemaError("video tags must be a list of strings")
        image = video.get("profileImage") or ""
        if not isinstance(image, str):
            raise IngestionSchemaError("profileImage must be a string")
        if image.startswith("//"):
            image = "https:" + image
        target_url = ContentIngestionService._http_url(
            str(video["targetUrl"]), "targetUrl"
        )
        if image:
            image = ContentIngestionService._http_url(
                image, "profileImage", target_url
            )
        return ContentItem(
            id=str(video["id"]),
            title=str(video["title"]),
            performer=str(video.get("uploader") or "Unknown"),
            tags=tuple(raw_tags),
            thumbnail=image,
            target_url=target_url,
            quality=str(video.get("quality") or "unknown"),
            source="video_api",
            fetched_at=fetched_at,
        )

    async def ingest_video_api(
        self, tags: list[str] | tuple[str, ...], limit: int = 50
    ) -> list[ContentItem]:
        """Fetch the configured video API using validated query parameters."""
        psid = self.awe.get("psid")
        access_key = self.awe.get("access_key")
        if not isinstance(psid, str) or not psid.strip():
            raise IngestionConfigurationError("psid is required for video API ingestion")
        if not isinstance(access_key, str) or not access_key.strip():
            raise IngestionConfigurationError("access_key is required for video API ingestion")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError("tags must contain non-empty strings")

        endpoint = self.awe.get("video_api_url", DEFAULT_VIDEO_API_URL)
        try:
            parsed_endpoint = urlsplit(endpoint) if isinstance(endpoint, str) else None
            valid_endpoint = (
                parsed_endpoint is not None
                and parsed_endpoint.scheme == "https"
                and bool(parsed_endpoint.hostname)
            )
        except ValueError:
            valid_endpoint = False
        if not valid_endpoint:
            raise IngestionConfigurationError("video_api_url must use HTTPS")
        fetched_at = self._fetched_at()
        items: list[ContentItem] = []
        for tag in tags:
            body = await self._get_text(
                endpoint,
                params={
                    "psid": psid,
                    "accessKey": access_key,
                    "tags": tag,
                    "limit": limit,
                    "quality": "hd",
                },
            )
            try:
                payload = json.loads(body)
                videos = payload["data"]["videos"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise IngestionSchemaError(
                    "Video API response must contain data.videos"
                ) from exc
            if not isinstance(videos, list):
                raise IngestionSchemaError("Video API data.videos must be a list")
            try:
                items.extend(
                    self._video_item(video, fetched_at) for video in videos
                )
            except IngestionSchemaError as exc:
                raise IngestionSchemaError(f"Invalid video API entry: {exc}") from exc
        return items

    async def store_content(self, items: list[ContentItem]) -> int:
        """Insert only unseen source identifiers; returns the insert count."""
        inserted = 0
        for item in items:
            result = await self.db.execute(
                """
                INSERT INTO content_pool
                    (external_id, source, title, performer, tags, thumbnail_url,
                     target_url, quality, fetched_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (source, external_id) DO NOTHING
                """,
                item.id,
                item.source,
                item.title,
                item.performer,
                list(item.tags),
                item.thumbnail,
                item.target_url,
                item.quality,
                item.fetched_at,
            )
            if result.endswith(" 1"):
                inserted += 1
        return inserted

"""Database-backed campaign metrics and optional AWE report ingestion."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, TypedDict

import aiohttp

logger = logging.getLogger(__name__)


class AnalyticsError(RuntimeError):
    """Base class for analytics failures."""


class AnalyticsConfigurationError(AnalyticsError):
    """Raised when an optional external analytics source is not configured."""


class AnalyticsDataError(AnalyticsError):
    """Raised when an analytics response cannot be interpreted."""


class Metrics(TypedDict):
    total_posts: int
    epc: float
    conversion_rate: float
    revenue_per_post: float
    roi: float
    trend: str
    top_content: list[dict[str, Any]]


class AnalyticsEngine:
    def __init__(
        self,
        db: Any,
        clickhouse: Any = None,
        *,
        awe_config: Mapping[str, Any] | None = None,
        session: Any = None,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.db = db
        self.clickhouse = clickhouse
        self.awe_config = awe_config or {}
        self.session = session
        self._owns_session = session is None
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def close(self) -> None:
        if self.session is not None and self._owns_session:
            await self.session.close()
            self.session = None

    def _now(self, as_of: datetime | None = None) -> datetime:
        value = as_of or self.clock()
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def parse_xml(xml: str) -> list[dict[str, str]]:
        """Parse a report into one mapping per XML row without external entities."""
        if "<!doctype" in xml.lower() or "<!entity" in xml.lower():
            raise AnalyticsDataError("XML reports must not declare entities")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise AnalyticsDataError("AWE report is not valid XML") from exc

        rows: list[dict[str, str]] = []
        for element in root:
            if list(element):
                rows.append(
                    {
                        child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
                        for child in element
                    }
                )
            else:
                rows.append({element.tag.rsplit("}", 1)[-1]: (element.text or "").strip()})
        return rows

    async def _fetch_report(self, url: str, params: Mapping[str, str]) -> str:
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        try:
            async with self.session.get(url, params=params, timeout=self.timeout) as response:
                response.raise_for_status()
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                "awe_report_request_failed",
                extra={
                    "event": "awe_report_request_failed",
                    "host": "remote-stat.awempire.com",
                    "error_type": type(exc).__name__,
                },
            )
            raise AnalyticsError("AWE report request failed") from None

    async def fetch_awe_stats(
        self, sub_affiliate_id: str, days: int = 7
    ) -> dict[str, list[dict[str, str]]]:
        """Fetch sales and click reports; requires an explicitly configured partner hash."""
        partner_hash = self.awe_config.get("partner_hash")
        if not isinstance(partner_hash, str) or not partner_hash.strip():
            raise AnalyticsConfigurationError(
                "partner_hash is required to fetch AWE reports"
            )
        if not sub_affiliate_id or days <= 0:
            raise ValueError("sub_affiliate_id and positive days are required")
        end = self._now()
        start = end - timedelta(days=days)
        params = {
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
            "subAffiliateId": sub_affiliate_id,
            "dailyGroup": "true",
            "partnerHash": partner_hash,
        }
        base_url = "https://remote-stat.awempire.com/"
        reports = {
            "sales": "export-xml-sales-sub-affiliate",
            "clicks": "export-xml-click-and-sales",
        }
        result: dict[str, list[dict[str, str]]] = {}
        for key, path in reports.items():
            xml = await self._fetch_report(base_url + path, params)
            result[key] = self.parse_xml(xml)
        return result

    async def _fetch_one(self, query: str, *args: Any) -> Any:
        if hasattr(self.db, "fetchrow"):
            return await self.db.fetchrow(query, *args)
        rows = await self.db.fetch(query, *args)
        return rows[0] if rows else {}

    @staticmethod
    def _value(row: Any, key: str, default: Any = 0) -> Any:
        if row is None:
            return default
        try:
            value = row[key]
        except (KeyError, TypeError, IndexError):
            value = getattr(row, key, default)
        return default if value is None else value

    async def calculate_metrics(
        self, sub_affiliate_id: str, *, as_of: datetime | None = None
    ) -> Metrics:
        """Calculate deterministic seven-day metrics from the persisted posts."""
        end = self._now(as_of)
        start = end - timedelta(days=7)
        row = await self._fetch_one(
            """
            SELECT COUNT(*) AS total_posts,
                   COALESCE(SUM(clicks), 0) AS total_clicks,
                   COALESCE(SUM(conversions), 0) AS total_conversions,
                   COALESCE(SUM(revenue), 0) AS total_revenue
            FROM posts
            WHERE sub_affiliate_id = $1 AND posted_at >= $2 AND posted_at < $3
            """,
            sub_affiliate_id,
            start,
            end,
        )
        posts = int(self._value(row, "total_posts"))
        clicks = float(self._value(row, "total_clicks"))
        conversions = float(self._value(row, "total_conversions"))
        revenue = float(self._value(row, "total_revenue"))
        costs = await self.estimate_costs(sub_affiliate_id, as_of=end)
        return {
            "total_posts": posts,
            "epc": revenue / clicks if clicks else 0.0,
            "conversion_rate": conversions / clicks * 100 if clicks else 0.0,
            "revenue_per_post": revenue / posts if posts else 0.0,
            "roi": (revenue - costs) / costs * 100 if costs else 0.0,
            "trend": await self.calculate_trend(sub_affiliate_id, as_of=end),
            "top_content": await self.get_top_performing_content(sub_affiliate_id),
        }

    async def estimate_costs(
        self, sub_affiliate_id: str, days: int = 7, *, as_of: datetime | None = None
    ) -> float:
        if days <= 0:
            raise ValueError("days must be positive")
        end = self._now(as_of)
        start = (end - timedelta(days=days)).date()
        end_date = end.date() + timedelta(days=1)
        row = await self._fetch_one(
            """
            SELECT COALESCE(SUM(costs), 0) AS total_cost
            FROM analytics_daily
            WHERE sub_affiliate_id = $1 AND date >= $2 AND date < $3
            """,
            sub_affiliate_id,
            start,
            end_date,
        )
        return float(self._value(row, "total_cost"))

    async def get_revenue_period(
        self,
        sub_affiliate_id: str,
        days: int,
        offset: int = 0,
        *,
        as_of: datetime | None = None,
    ) -> float:
        if days <= 0 or offset < 0:
            raise ValueError("days must be positive and offset must not be negative")
        end = self._now(as_of) - timedelta(days=offset)
        start = end - timedelta(days=days)
        row = await self._fetch_one(
            """
            SELECT COALESCE(SUM(revenue), 0) AS total_revenue
            FROM posts
            WHERE sub_affiliate_id = $1 AND posted_at >= $2 AND posted_at < $3
            """,
            sub_affiliate_id,
            start,
            end,
        )
        return float(self._value(row, "total_revenue"))

    async def calculate_trend(
        self, sub_affiliate_id: str, *, as_of: datetime | None = None
    ) -> str:
        end = self._now(as_of)
        current = await self.get_revenue_period(sub_affiliate_id, 7, as_of=end)
        previous = await self.get_revenue_period(
            sub_affiliate_id, 7, offset=7, as_of=end
        )
        if current > previous * 1.2:
            return "up_trending"
        if current < previous * 0.8:
            return "down_trending"
        return "stable"

    async def get_top_performing_content(
        self, sub_affiliate_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self.db.fetch(
            """
            SELECT c.id AS content_id, c.title,
                   COALESCE(SUM(p.revenue), 0) AS revenue,
                   COALESCE(SUM(p.clicks), 0) AS clicks
            FROM posts AS p
            JOIN content_pool AS c ON c.id = p.content_id
            WHERE p.sub_affiliate_id = $1
            GROUP BY c.id, c.title
            ORDER BY revenue DESC, c.id
            LIMIT $2
            """,
            sub_affiliate_id,
            limit,
        )
        return [dict(row) for row in rows]

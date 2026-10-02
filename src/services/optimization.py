"""Deterministic campaign action selection and idempotent application."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, TypedDict

from src.services.analytics import Metrics

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    INCREASE_VOLUME = "increase_volume"
    DECREASE_VOLUME = "decrease_volume"
    PAUSE = "pause"
    ROTATE_TAGS = "rotate_tags"
    CHANGE_POSTING_TIMES = "change_posting_times"
    SCALE_WINNER = "scale_winner"


class OptimizationAction(TypedDict):
    type: ActionType
    reason: str
    parameters: dict[str, Any]


class OptimizationEngine:
    def __init__(
        self,
        db: Any,
        analytics: Any,
        *,
        rules: Mapping[str, float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.analytics = analytics
        self.rules = {
            "epc_threshold_high": 2.00,
            "epc_threshold_low": 0.30,
            "conversion_rate_threshold": 2.0,
            "min_posts_before_decision": 20,
            **(rules or {}),
        }
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def analyze_and_optimize(
        self, sub_affiliate_id: str, *, as_of: datetime | None = None
    ) -> list[OptimizationAction]:
        if not sub_affiliate_id:
            raise ValueError("sub_affiliate_id must not be empty")
        metrics: Metrics = await self.analytics.calculate_metrics(
            sub_affiliate_id, as_of=as_of
        )
        actions: list[OptimizationAction] = []
        if metrics["total_posts"] < self.rules["min_posts_before_decision"]:
            logger.info(
                "optimization_skipped_insufficient_data",
                extra={
                    "event": "optimization_skipped_insufficient_data",
                    "sub_affiliate_id": sub_affiliate_id,
                    "post_count": metrics["total_posts"],
                },
            )
            return actions

        epc = self._metric(metrics, "epc")
        conversion_rate = self._metric(metrics, "conversion_rate")
        if epc > self.rules["epc_threshold_high"]:
            actions.append(
                {
                    "type": ActionType.SCALE_WINNER,
                    "reason": f"High EPC: ${epc:.2f}",
                    "parameters": {"increase_by_percent": 50},
                }
            )
        elif (
            conversion_rate < self.rules["conversion_rate_threshold"]
            and epc < self.rules["epc_threshold_low"]
        ):
            tags = await self.suggest_tags(sub_affiliate_id)
            if tags:
                actions.append(
                    {
                        "type": ActionType.ROTATE_TAGS,
                        "reason": f"Low conversion: {conversion_rate:.2f}%, EPC: ${epc:.2f}",
                        "parameters": {"new_tags": tags},
                    }
                )
        elif metrics["trend"] == "down_trending":
            actions.append(
                {
                    "type": ActionType.DECREASE_VOLUME,
                    "reason": "Performance declining",
                    "parameters": {"decrease_by_percent": 30},
                }
            )

        for action in actions:
            await self.apply_action(sub_affiliate_id, action, as_of=as_of)
        return actions

    @staticmethod
    def _metric(metrics: Mapping[str, Any], name: str) -> float:
        try:
            value = float(metrics[name])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"analytics metric {name!r} must be numeric") from exc
        if not math.isfinite(value):
            raise ValueError(f"analytics metric {name!r} must be finite")
        return value

    async def suggest_tags(self, sub_affiliate_id: str) -> list[str]:
        rows = await self.db.fetch(
            """
            SELECT tag
            FROM (
                SELECT UNNEST(c.tags) AS tag,
                       SUM(p.revenue) / NULLIF(COUNT(*), 0) AS revenue_per_use
                FROM posts AS p
                JOIN content_pool AS c ON p.content_id = c.id
                WHERE p.sub_affiliate_id = $1 AND p.revenue > 0
                GROUP BY tag
            ) AS ranked_tags
            ORDER BY revenue_per_use DESC, tag
            LIMIT 10
            """,
            sub_affiliate_id,
        )
        return [str(row["tag"]) for row in rows if row.get("tag")]

    def _idempotency_key(
        self, sub_affiliate_id: str, action: OptimizationAction, as_of: datetime | None
    ) -> str:
        instant = as_of or self.clock()
        if instant.tzinfo is not None:
            instant = instant.astimezone(timezone.utc)
        identity = json.dumps(
            {
                "day": instant.date().isoformat(),
                "sub_affiliate_id": sub_affiliate_id,
                "type": ActionType(action["type"]).value,
                "reason": action["reason"],
                "parameters": action["parameters"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    async def apply_action(
        self,
        sub_affiliate_id: str,
        action: OptimizationAction,
        *,
        as_of: datetime | None = None,
    ) -> bool:
        """Apply once per action/day; log and campaign mutation share one SQL statement."""
        if not sub_affiliate_id:
            raise ValueError("sub_affiliate_id must not be empty")
        try:
            action_type = ActionType(action["type"])
        except (KeyError, ValueError) as exc:
            raise ValueError("unsupported optimization action") from exc
        reason = action.get("reason")
        parameters = action.get("parameters")
        if not isinstance(reason, str) or not isinstance(parameters, dict):
            raise ValueError("an action requires a reason and parameter mapping")

        key = self._idempotency_key(sub_affiliate_id, action, as_of)
        values: tuple[Any, ...]
        if action_type in {ActionType.SCALE_WINNER, ActionType.INCREASE_VOLUME}:
            percent = self._bounded_percent(parameters.get("increase_by_percent"))
            statement = """
                WITH inserted AS (
                    INSERT INTO optimization_logs
                        (sub_affiliate_id, action, reason, auto_applied, idempotency_key)
                    SELECT $1, $2, $3, TRUE, $4
                    WHERE EXISTS (SELECT 1 FROM campaigns WHERE sub_affiliate_id = $1)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                )
                UPDATE campaigns
                SET posting_schedule = jsonb_set(
                    COALESCE(posting_schedule, '{}'::jsonb),
                    '{posts_per_day}',
                    to_jsonb(LEAST(100, GREATEST(1, CEIL(
                        COALESCE((posting_schedule->>'posts_per_day')::numeric, 10)
                        * (1 + $5::numeric / 100)
                    )::int)))
                )
                WHERE sub_affiliate_id = $1 AND EXISTS (SELECT 1 FROM inserted)
            """
            values = (
                sub_affiliate_id,
                action_type.value,
                reason,
                key,
                percent,
            )
        elif action_type == ActionType.DECREASE_VOLUME:
            percent = self._bounded_percent(parameters.get("decrease_by_percent"))
            statement = """
                WITH inserted AS (
                    INSERT INTO optimization_logs
                        (sub_affiliate_id, action, reason, auto_applied, idempotency_key)
                    SELECT $1, $2, $3, TRUE, $4
                    WHERE EXISTS (SELECT 1 FROM campaigns WHERE sub_affiliate_id = $1)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                )
                UPDATE campaigns
                SET posting_schedule = jsonb_set(
                    COALESCE(posting_schedule, '{}'::jsonb),
                    '{posts_per_day}',
                    to_jsonb(GREATEST(1, FLOOR(
                        COALESCE((posting_schedule->>'posts_per_day')::numeric, 10)
                        * (1 - $5::numeric / 100)
                    )::int))
                )
                WHERE sub_affiliate_id = $1 AND EXISTS (SELECT 1 FROM inserted)
            """
            values = (
                sub_affiliate_id,
                action_type.value,
                reason,
                key,
                percent,
            )
        elif action_type == ActionType.ROTATE_TAGS:
            tags = parameters.get("new_tags")
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError("new_tags must be a list of strings")
            statement = """
                WITH inserted AS (
                    INSERT INTO optimization_logs
                        (sub_affiliate_id, action, reason, auto_applied, idempotency_key)
                    SELECT $1, $2, $3, TRUE, $4
                    WHERE EXISTS (SELECT 1 FROM campaigns WHERE sub_affiliate_id = $1)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                )
                UPDATE campaigns
                SET tags = $5
                WHERE sub_affiliate_id = $1 AND EXISTS (SELECT 1 FROM inserted)
            """
            values = (
                sub_affiliate_id,
                action_type.value,
                reason,
                key,
                tags,
            )
        elif action_type == ActionType.PAUSE:
            statement = """
                WITH inserted AS (
                    INSERT INTO optimization_logs
                        (sub_affiliate_id, action, reason, auto_applied, idempotency_key)
                    SELECT $1, $2, $3, TRUE, $4
                    WHERE EXISTS (SELECT 1 FROM campaigns WHERE sub_affiliate_id = $1)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                )
                UPDATE campaigns
                SET status = 'paused'
                WHERE sub_affiliate_id = $1 AND EXISTS (SELECT 1 FROM inserted)
            """
            values = (sub_affiliate_id, action_type.value, reason, key)
        elif action_type == ActionType.CHANGE_POSTING_TIMES:
            hours = parameters.get("hours")
            if (
                not isinstance(hours, list)
                or any(
                    not isinstance(hour, int)
                    or isinstance(hour, bool)
                    or not 0 <= hour <= 23
                    for hour in hours
                )
            ):
                raise ValueError("hours must be a list of integers from 0 to 23")
            statement = """
                WITH inserted AS (
                    INSERT INTO optimization_logs
                        (sub_affiliate_id, action, reason, auto_applied, idempotency_key)
                    SELECT $1, $2, $3, TRUE, $4
                    WHERE EXISTS (SELECT 1 FROM campaigns WHERE sub_affiliate_id = $1)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                )
                UPDATE campaigns
                SET posting_schedule = jsonb_set(
                    COALESCE(posting_schedule, '{}'::jsonb),
                    '{hours}',
                    to_jsonb($5::int[])
                )
                WHERE sub_affiliate_id = $1 AND EXISTS (SELECT 1 FROM inserted)
            """
            values = (
                sub_affiliate_id,
                action_type.value,
                reason,
                key,
                sorted(set(hours)),
            )
        else:
            raise ValueError(f"action type {action_type.value!r} is not supported")

        result = await self.db.execute(statement, *values)
        applied = result.endswith(" 1") or (
            result.startswith("UPDATE ") and result.split()[-1].isdigit()
            and int(result.split()[-1]) > 0
        )
        logger.info(
            "optimization_action_%s",
            "applied" if applied else "already_applied_or_no_campaign",
            extra={
                "event": "optimization_action_applied" if applied else "optimization_action_skipped",
                "sub_affiliate_id": sub_affiliate_id,
                "action": action_type.value,
                "idempotency_key": key,
            },
        )
        return applied

    @staticmethod
    def _bounded_percent(value: Any) -> float:
        try:
            percent = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("action percentage must be numeric") from exc
        if not math.isfinite(percent) or not 0 < percent <= 100:
            raise ValueError("action percentage must be between 0 and 100")
        return percent

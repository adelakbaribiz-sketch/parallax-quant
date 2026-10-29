"""Append-only audit log for every order, risk decision, and compliance
check — retained for config.ComplianceConfig.audit_log_retention_days
(defaults to ~7 years, a common regulatory retention window, but confirm
the actual requirement for your jurisdiction and license with counsel).

Writes to Postgres (structured, queryable) via the same connection this
process already holds for other structured data — see data/storage.py."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

CREATE_AUDIT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    time        TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    subject_id  TEXT,
    detail      JSONB NOT NULL
);
"""

CREATE_AUDIT_LOG_INDEX = "CREATE INDEX IF NOT EXISTS ix_audit_log_time_type ON audit_log (time DESC, event_type);"


@dataclass
class AuditEvent:
    event_type: str
    actor: str
    detail: dict[str, Any]
    subject_id: str | None = None
    timestamp: float = field(default_factory=time.time)


class AuditLog:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def ensure_schema(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(CREATE_AUDIT_LOG_TABLE)
            await conn.execute(CREATE_AUDIT_LOG_INDEX)

    async def record(self, event: AuditEvent) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_log (event_type, actor, subject_id, detail) VALUES ($1, $2, $3, $4)",
                event.event_type,
                event.actor,
                event.subject_id,
                json.dumps(event.detail),
            )
        logger.info("audit_event", event_type=event.event_type, actor=event.actor, subject_id=event.subject_id)

    async def query(self, event_type: str | None, start_iso: str, end_iso: str, limit: int = 1000) -> list[dict]:
        async with self.pool.acquire() as conn:
            if event_type:
                rows = await conn.fetch(
                    "SELECT * FROM audit_log WHERE event_type = $1 AND time BETWEEN $2 AND $3 ORDER BY time DESC LIMIT $4",
                    event_type, start_iso, end_iso, limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM audit_log WHERE time BETWEEN $1 AND $2 ORDER BY time DESC LIMIT $3",
                    start_iso, end_iso, limit,
                )
        return [dict(r) for r in rows]

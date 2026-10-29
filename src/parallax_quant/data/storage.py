"""TimescaleDB persistence for ticks/order books. Creates the hypertable
and compression policy from config on first connect, then batches inserts
via asyncpg's `copy_records_to_table` for throughput."""

from __future__ import annotations

import os
from collections.abc import Sequence

import asyncpg
import structlog

from parallax_quant.config import DataLayerConfig
from parallax_quant.data.models import Tick

logger = structlog.get_logger(__name__)

CREATE_TICKS_TABLE = """
CREATE TABLE IF NOT EXISTS ticks (
    time        TIMESTAMPTZ       NOT NULL,
    exchange    TEXT              NOT NULL,
    symbol      TEXT              NOT NULL,
    bid         DOUBLE PRECISION  NOT NULL,
    ask         DOUBLE PRECISION  NOT NULL,
    bid_volume  DOUBLE PRECISION  NOT NULL,
    ask_volume  DOUBLE PRECISION  NOT NULL
);
"""

CREATE_HYPERTABLE = "SELECT create_hypertable('ticks', 'time', chunk_time_interval => INTERVAL '{chunk}', if_not_exists => TRUE);"

ENABLE_COMPRESSION = """
ALTER TABLE ticks SET (timescaledb.compress, timescaledb.compress_segmentby = 'exchange,symbol');
"""

ADD_COMPRESSION_POLICY = "SELECT add_compression_policy('ticks', INTERVAL '{after}', if_not_exists => TRUE);"

CREATE_SYMBOL_INDEX = "CREATE INDEX IF NOT EXISTS ix_ticks_exchange_symbol_time ON ticks (exchange, symbol, time DESC);"


class TimescaleStore:
    def __init__(self, config: DataLayerConfig) -> None:
        self.config = config
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        dsn = os.environ.get(self.config.timescaledb_dsn_env)
        if not dsn:
            raise ValueError(f"Env var {self.config.timescaledb_dsn_env} is not set")
        self._pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        await self._ensure_schema()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def _ensure_schema(self) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TICKS_TABLE)
            try:
                await conn.execute(CREATE_HYPERTABLE.format(chunk=self.config.hypertable_chunk_interval))
                await conn.execute(ENABLE_COMPRESSION)
                await conn.execute(ADD_COMPRESSION_POLICY.format(after=self.config.compression_after))
            except asyncpg.PostgresError as exc:
                # TimescaleDB extension may not be installed in a plain
                # Postgres dev environment — degrade to a normal table
                # rather than crashing the whole data layer.
                logger.warning("timescaledb_extension_unavailable", error=str(exc))
            await conn.execute(CREATE_SYMBOL_INDEX)

    async def write_ticks(self, ticks: Sequence[Tick]) -> None:
        if not ticks:
            return
        assert self._pool is not None
        records = [
            (
                __import__("datetime").datetime.fromtimestamp(t.timestamp, tz=__import__("datetime").timezone.utc),
                t.exchange,
                t.symbol,
                t.bid,
                t.ask,
                t.bid_volume,
                t.ask_volume,
            )
            for t in ticks
        ]
        async with self._pool.acquire() as conn:
            await conn.copy_records_to_table(
                "ticks",
                records=records,
                columns=["time", "exchange", "symbol", "bid", "ask", "bid_volume", "ask_volume"],
            )

    async def fetch_ticks(self, exchange: str, symbol: str, start_iso: str, end_iso: str) -> list[Tick]:
        assert self._pool is not None
        rows = await self._pool.fetch(
            """
            SELECT extract(epoch from time) AS ts, exchange, symbol, bid, ask, bid_volume, ask_volume
            FROM ticks
            WHERE exchange = $1 AND symbol = $2 AND time BETWEEN $3 AND $4
            ORDER BY time ASC
            """,
            exchange,
            symbol,
            start_iso,
            end_iso,
        )
        return [
            Tick(
                exchange=r["exchange"],
                symbol=r["symbol"],
                bid=r["bid"],
                ask=r["ask"],
                bid_volume=r["bid_volume"],
                ask_volume=r["ask_volume"],
                timestamp=r["ts"],
            )
            for r in rows
        ]

"""NTP-based clock sync so tick timestamps and latency measurements are
comparable across processes/machines. Arbitrage signals decay in
milliseconds — an unsynced local clock silently corrupts every latency
budget in the system."""

from __future__ import annotations

import asyncio
import time

import ntplib
import structlog

logger = structlog.get_logger(__name__)


class TimeSync:
    def __init__(self, ntp_server: str, sync_interval_seconds: int, max_drift_ms: float) -> None:
        self.ntp_server = ntp_server
        self.sync_interval_seconds = sync_interval_seconds
        self.max_drift_ms = max_drift_ms
        self._offset_seconds = 0.0
        self._client = ntplib.NTPClient()

    def now(self) -> float:
        """NTP-corrected unix timestamp."""
        return time.time() + self._offset_seconds

    async def sync_once(self) -> float:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, self._query_ntp)
        if response is None:
            return self._offset_seconds
        drift_ms = response.offset * 1000
        if abs(drift_ms) > self.max_drift_ms:
            logger.warning("clock_drift_exceeds_threshold", drift_ms=drift_ms, threshold_ms=self.max_drift_ms)
        self._offset_seconds = response.offset
        return self._offset_seconds

    def _query_ntp(self) -> ntplib.NTPStats | None:
        try:
            return self._client.request(self.ntp_server, version=3, timeout=5)
        except Exception as exc:  # noqa: BLE001 - NTP is best-effort, never fatal
            logger.warning("ntp_sync_failed", error=str(exc))
            return None

    async def run_periodic_sync(self, stop_event: asyncio.Event) -> None:
        await self.sync_once()
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.sync_interval_seconds)
            except TimeoutError:
                await self.sync_once()

    def measure_round_trip_ms(self, sent_at: float) -> float:
        return (self.now() - sent_at) * 1000

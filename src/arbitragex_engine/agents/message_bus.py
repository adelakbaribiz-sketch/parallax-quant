"""Pluggable message bus. `in_memory` (default, via asyncio.Queue) needs no
external service and is what the paper-trading default runs on; `rabbitmq`
is a real aio-pika implementation for a distributed multi-process
deployment, per config.AgentsConfig.message_bus.backend."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import structlog

from arbitragex_engine.config import MessageBusConfig

logger = structlog.get_logger(__name__)


class MessageBus(ABC):
    @abstractmethod
    async def publish(self, topic: str, message: dict[str, Any]) -> None: ...

    @abstractmethod
    async def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]: ...

    async def close(self) -> None:
        return None


class InMemoryMessageBus(MessageBus):
    """Single-process bus — every publish fans out to every subscriber
    queue for that topic. Sufficient for paper trading on one machine;
    swap to RabbitmqMessageBus for the distributed deployment described
    in docs/ARCHITECTURE.md."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        for queue in self._subscribers.get(topic, []):
            await queue.put(message)

    async def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(topic, []).append(queue)
        return queue


class RabbitmqMessageBus(MessageBus):
    def __init__(self, config: MessageBusConfig) -> None:
        self.config = config
        # Typed Any: aio_pika is imported lazily (optional dependency, only
        # needed when message_bus.backend = "rabbitmq") so its types aren't
        # available for a precise annotation without importing it eagerly.
        self._connection: Any = None
        self._channel: Any = None

    async def _ensure_connected(self) -> None:
        if self._channel is not None:
            return
        import aio_pika

        url = os.environ.get(self.config.rabbitmq_url_env)
        if not url:
            raise ValueError(f"Env var {self.config.rabbitmq_url_env} is not set")
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        import aio_pika

        await self._ensure_connected()
        exchange = await self._channel.declare_exchange(topic, aio_pika.ExchangeType.FANOUT, durable=True)
        await exchange.publish(aio_pika.Message(body=json.dumps(message).encode()), routing_key="")

    async def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        import aio_pika

        await self._ensure_connected()
        exchange = await self._channel.declare_exchange(topic, aio_pika.ExchangeType.FANOUT, durable=True)
        queue = await self._channel.declare_queue(exclusive=True)
        await queue.bind(exchange)

        out_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def _consume() -> None:
            async with queue.iterator() as it:
                async for msg in it:
                    async with msg.process():
                        await out_queue.put(json.loads(msg.body))

        asyncio.create_task(_consume())
        return out_queue

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()


def build_message_bus(config: MessageBusConfig) -> MessageBus:
    if config.backend == "in_memory":
        return InMemoryMessageBus()
    if config.backend == "rabbitmq":
        return RabbitmqMessageBus(config)
    raise NotImplementedError(
        f"message_bus.backend '{config.backend}' has no implementation yet. "
        "in_memory and rabbitmq are implemented; kafka is declared in the "
        "config schema as a documented future option — see docs/ROADMAP.md."
    )

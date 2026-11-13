"""One Slack message that grows with the agent's stream, edited at most every ~1.5 s."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.slack_format import split_message

logger = logging.getLogger(__name__)

MIN_UPDATE_INTERVAL = 1.5


class ThrottledMessage:
    def __init__(
        self,
        client: Any,
        *,
        channel: str,
        ts: str,
        thread_ts: str | None,
        min_interval: float = MIN_UPDATE_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.channel = channel
        self.ts = ts
        self.thread_ts = thread_ts
        self.min_interval = min_interval
        self.clock = clock
        self.sleep = sleep
        self._sent: str | None = None
        self._sent_at: float | None = None
        self._pending: str | None = None
        self._task: asyncio.Task[None] | None = None

    def set(self, text: str) -> None:
        """Show ``text`` soon; a newer call before the next edit replaces it."""
        if text in (self._sent, self._pending):
            return
        self._pending = text
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        if self._sent_at is not None:
            wait = self._sent_at + self.min_interval - self.clock()
            if wait > 0:
                await self.sleep(wait)
        text = self._pending
        self._pending = None
        if text is not None:
            await self._update(text)

    async def _update(self, text: str) -> None:
        if text == self._sent:
            return
        try:
            await self.client.chat_update(channel=self.channel, ts=self.ts, text=text)
        except Exception as exc:  # noqa: BLE001 - a failed edit must not end the stream
            logger.warning("chat.update failed: %s", exc)
            return
        self._sent = text
        self._sent_at = self.clock()

    async def finish(self, text: str) -> None:
        """Replace the message with the final text; overflow goes into follow-up messages."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._pending = None
        first, *rest = split_message(text)
        await self._update(first)
        for chunk in rest:
            try:
                await self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts, text=chunk)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat.postMessage for an overflow chunk failed: %s", exc)
                return

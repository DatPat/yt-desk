from __future__ import annotations

import asyncio
import json
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subs.discard(queue)

    def publish(self, event: str, data: Any) -> None:
        payload = {"event": event, "data": data}
        loop = self._loop
        if loop is None:
            return
        for queue in list(self._subs):
            def _put(q: asyncio.Queue = queue, item: dict = payload) -> None:
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except Exception:
                        pass
                    try:
                        q.put_nowait(item)
                    except Exception:
                        pass

            loop.call_soon_threadsafe(_put)

    async def stream(self):
        queue = self.subscribe()
        try:
            yield {"event": "hello", "data": json.dumps({"ok": True})}
            while True:
                item = await queue.get()
                yield {
                    "event": item["event"],
                    "data": json.dumps(item["data"], default=str),
                }
        finally:
            self.unsubscribe(queue)


hub = EventHub()

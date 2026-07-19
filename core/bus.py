"""Async message bus — the coordination fabric for the agent desk.

Agents communicate through addressed Messages instead of reading each
other's JSON files: topic broadcast (pub/sub), direct send, and
request/reply with correlation IDs. Every persisted message lands in the
agent_messages table, so a deliberation leaves a queryable transcript
(core.database.get_message_thread).

The bus is in-process and single-event-loop. SQLite persistence runs in
worker threads so the loop never blocks on disk.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field

from core.database import save_message


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Message:
    topic: str
    sender: str
    payload: dict
    recipient: str | None = None          # None = topic broadcast
    correlation_id: str | None = None     # groups one deliberation thread
    reply_to: str | None = None           # msg_id this message answers
    msg_id: str = field(default_factory=lambda: _new_id("msg"))
    ts: float = field(default_factory=time.time)


class MessageBus:
    """Actor-model routing: every agent registers one inbox queue; topics
    fan out to subscribed inboxes, direct sends target one inbox, and
    request() awaits the matching respond() via a pending-future map."""

    def __init__(self, persist=True, max_inbox=1000):
        self._inboxes: dict[str, asyncio.Queue] = {}
        self._topic_subs: dict[str, set[str]] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._persist = persist
        self._max_inbox = max_inbox

    # ── registration ──
    def register(self, agent: str) -> asyncio.Queue:
        if agent not in self._inboxes:
            self._inboxes[agent] = asyncio.Queue(maxsize=self._max_inbox)
        return self._inboxes[agent]

    def subscribe(self, agent: str, *topics: str):
        self.register(agent)
        for t in topics:
            self._topic_subs.setdefault(t, set()).add(agent)

    def agents(self):
        return list(self._inboxes)

    # ── delivery ──
    def _deliver(self, agent: str, msg: Message):
        q = self._inboxes.get(agent)
        if q is None:
            return
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # Drop the oldest message rather than the newest: stale market
            # context is worthless, the latest verdict is not.
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            q.put_nowait(msg)

    async def _record(self, msg: Message, persist: bool):
        if self._persist and persist:
            await asyncio.to_thread(
                save_message, msg.msg_id, msg.topic, msg.sender, msg.payload,
                msg.correlation_id, msg.recipient,
            )

    async def publish(self, sender: str, topic: str, payload: dict,
                      correlation_id=None, persist=True) -> Message:
        """Broadcast to every subscriber of `topic` except the sender."""
        msg = Message(topic=topic, sender=sender, payload=payload,
                      correlation_id=correlation_id)
        for agent in self._topic_subs.get(topic, ()):  # snapshot not needed: no awaits inside
            if agent != sender:
                self._deliver(agent, msg)
        await self._record(msg, persist)
        return msg

    async def send(self, sender: str, to: str, topic: str, payload: dict,
                   correlation_id=None, reply_to=None, persist=True) -> Message:
        """Direct message to one agent's inbox."""
        msg = Message(topic=topic, sender=sender, payload=payload, recipient=to,
                      correlation_id=correlation_id, reply_to=reply_to)
        self._deliver(to, msg)
        await self._record(msg, persist)
        return msg

    # ── request/reply ──
    async def request(self, sender: str, to: str, topic: str, payload: dict,
                      correlation_id=None, timeout=10.0) -> Message | None:
        """Send and await the reply to this specific message.
        Returns None on timeout — a silent agent must never stall the desk."""
        msg = Message(topic=topic, sender=sender, payload=payload, recipient=to,
                      correlation_id=correlation_id)
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg.msg_id] = fut
        self._deliver(to, msg)
        await self._record(msg, True)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(msg.msg_id, None)

    async def respond(self, original: Message, sender: str, payload: dict,
                      topic=None, persist=True) -> Message:
        """Answer a request(); resolves the requester's future."""
        msg = Message(topic=topic or f"{original.topic}.reply", sender=sender,
                      payload=payload, recipient=original.sender,
                      correlation_id=original.correlation_id,
                      reply_to=original.msg_id)
        fut = self._pending.get(original.msg_id)
        if fut is not None and not fut.done():
            fut.set_result(msg)
        else:
            # Late reply after timeout: deliver to inbox so it is not lost.
            self._deliver(original.sender, msg)
        await self._record(msg, persist)
        return msg

    async def gather(self, sender: str, responders: list[str], topic: str,
                     payload: dict, correlation_id=None, timeout=10.0) -> dict[str, Message]:
        """Ask several agents the same question concurrently; collect whoever
        answers within the deadline. Missing answers are simply absent."""
        results = await asyncio.gather(*[
            self.request(sender, r, topic, payload,
                         correlation_id=correlation_id, timeout=timeout)
            for r in responders
        ])
        return {r: m for r, m in zip(responders, results) if m is not None}

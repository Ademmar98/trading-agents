"""AgentBus — async message broker for real multi-agent behavior.

Each agent runs in its own asyncio task, subscribes to topics, and publishes
events. Agents can:
  • emit events (pub/sub)
  • request data from other agents (request/reply)
  • maintain persistent state across cycles
  • run at independent frequencies

This replaces the sequential for-loop over fresh class instances with a
living, concurrent agent fabric.
"""
import asyncio
import json
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set


@dataclass
class AgentMessage:
    topic: str
    payload: Any
    sender: str
    timestamp: float = field(default_factory=time.time)
    msg_id: str = field(default_factory=lambda: f"{time.time():.6f}")


class AgentBus:
    """Async pub/sub + request/reply message bus for trading agents.

    Usage:
        bus = AgentBus()
        await bus.start()

        # Subscribe
        bus.subscribe("market_data", my_callback)

        # Publish
        await bus.publish("market_data", {"price": 65000}, sender="analyst")

        # Request/reply
        reply = await bus.request("risk_assessment", {"symbol": "BTC"}, timeout=5)
    """

    def __init__(self):
        self._subscriptions: Dict[str, List[Callable]] = defaultdict(list)
        self._pending_replies: Dict[str, asyncio.Future] = {}
        self._reply_handlers: Dict[str, Callable] = {}
        self._agent_tasks: Dict[str, asyncio.Task] = {}
        self._agent_states: Dict[str, Dict] = {}
        self._running = False
        self._lock = asyncio.Lock()
        self._message_log: List[AgentMessage] = []
        self._max_log_size = 5000

    # ── Pub/Sub ──

    def subscribe(self, topic: str, handler: Callable[[AgentMessage], Coroutine]):
        """Subscribe a coroutine handler to a topic."""
        self._subscriptions[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Callable):
        """Remove a handler from a topic."""
        if topic in self._subscriptions:
            self._subscriptions[topic] = [h for h in self._subscriptions[topic] if h != handler]

    async def publish(self, topic: str, payload: Any, sender: str = "system"):
        """Fire-and-forget publish to all subscribers."""
        msg = AgentMessage(topic=topic, payload=payload, sender=sender)
        async with self._lock:
            self._message_log.append(msg)
            if len(self._message_log) > self._max_log_size:
                self._message_log = self._message_log[-self._max_log_size // 2:]

        handlers = self._subscriptions.get(topic, [])
        if not handlers:
            return

        # Fire all handlers concurrently; failures in one don't kill others
        async def _safe_call(handler):
            try:
                await handler(msg)
            except Exception as e:
                await self.publish("errors", {
                    "agent": getattr(handler, "__qualname__", str(handler)),
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }, sender="bus")

        await asyncio.gather(*[_safe_call(h) for h in handlers], return_exceptions=True)

    # ── Request/Reply ──

    async def request(self, topic: str, payload: Any, timeout: float = 10.0) -> Optional[Any]:
        """Send a request and await a reply. Returns None on timeout."""
        req_id = f"req-{time.time():.9f}"
        fut = asyncio.get_event_loop().create_future()
        self._pending_replies[req_id] = fut

        envelope = {"_req_id": req_id, "_reply_to": topic, "payload": payload}
        await self.publish(topic, envelope, sender="requestor")

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_replies.pop(req_id, None)

    async def reply(self, req_id: str, payload: Any):
        """Reply to a specific request."""
        fut = self._pending_replies.get(req_id)
        if fut and not fut.done():
            fut.set_result(payload)

    def register_reply_handler(self, topic: str, handler: Callable[[Any], Coroutine]):
        """Register an agent as the reply handler for a request topic."""
        self._reply_handlers[topic] = handler
        # Auto-subscribe the handler wrapper
        async def _wrapper(msg: AgentMessage):
            data = msg.payload
            if isinstance(data, dict) and "_req_id" in data:
                req_id = data["_req_id"]
                result = await handler(data["payload"])
                await self.reply(req_id, result)
            else:
                # Also handle plain publishes on this topic
                await handler(data)
        self.subscribe(topic, _wrapper)

    # ── Agent Lifecycle ──

    async def register_agent(self, name: str, agent_instance: "BaseAgent"):
        """Register an agent and start its background task."""
        self._agent_states[name] = agent_instance.state
        task = asyncio.create_task(agent_instance._run_loop(), name=f"agent-{name}")
        self._agent_tasks[name] = task

    async def unregister_agent(self, name: str):
        """Gracefully stop an agent."""
        task = self._agent_tasks.pop(name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._agent_states.pop(name, None)

    async def get_agent_state(self, name: str) -> Optional[Dict]:
        async with self._lock:
            return self._agent_states.get(name)

    async def set_agent_state(self, name: str, state: Dict):
        async with self._lock:
            self._agent_states[name] = state

    async def shutdown(self):
        """Cancel all agent tasks."""
        self._running = False
        for name, task in list(self._agent_tasks.items()):
            task.cancel()
        for name, task in list(self._agent_tasks.items()):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._agent_tasks.clear()

    async def start(self):
        self._running = True

    def get_recent_messages(self, topic: Optional[str] = None, n: int = 100) -> List[AgentMessage]:
        logs = self._message_log
        if topic:
            logs = [m for m in logs if m.topic == topic]
        return logs[-n:]


# Singleton instance (one bus per process)
_global_bus: Optional[AgentBus] = None


def get_bus() -> AgentBus:
    global _global_bus
    if _global_bus is None:
        _global_bus = AgentBus()
    return _global_bus


def reset_bus():
    global _global_bus
    _global_bus = None

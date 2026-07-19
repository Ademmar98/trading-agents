"""Actor runtime: every agent is a long-lived asyncio task with an inbox,
an optional periodic tick, and persistent state that survives cycles and
process restarts (agent_state table).

This replaces the sequential `for agent in CYCLE_AGENTS: agent().run()`
loop — agents are instantiated once, run concurrently, and coordinate
through core.bus.MessageBus instead of SharedMemory JSON files.
SharedMemory remains as a report sink so the dashboard keeps working.
"""
import asyncio
import threading
import time
import traceback

from core.bus import MessageBus, Message
from core.database import get_agent_state, set_agent_state
from core.memory import SharedMemory
from core.notifier import Notifier
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_STOP_TOPIC = "__stop__"
_SHARED_NOTIFIER = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


class AsyncAgent:
    """Base actor. Subclasses set `name`, `subscriptions`, `tick_interval`
    and implement on_message()/tick(). Blocking domain logic must go
    through self.work() so the event loop never stalls."""

    name: str = "agent"
    subscriptions: tuple = ()
    tick_interval: float | None = None   # seconds between ticks; None = message-driven only
    tick_delay: float = 0.0              # initial stagger so agents don't all fetch at t=0
    notifier = _SHARED_NOTIFIER

    def __init__(self, bus: MessageBus, services: dict | None = None):
        self.bus = bus
        self.services = services or {}
        self.inbox = bus.register(self.name)
        if self.subscriptions:
            bus.subscribe(self.name, *self.subscriptions)
        self.memory = SharedMemory()
        # Persistent memory: loaded once at construction, saved on change.
        self.state = get_agent_state(self.name)
        self._alive = True
        self.last_active = time.time()

    # ── helpers ──
    async def work(self, fn, *args, **kwargs):
        """Run blocking (requests/sqlite/CPU) domain code in a worker thread."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def save_state(self):
        await asyncio.to_thread(set_agent_state, self.name, self.state)

    async def log(self, message: str):
        await asyncio.to_thread(self.memory.log, self.name, message)

    async def publish(self, topic, payload, correlation_id=None, persist=True):
        return await self.bus.publish(self.name, topic, payload,
                                      correlation_id=correlation_id, persist=persist)

    async def respond(self, original: Message, payload, topic=None):
        return await self.bus.respond(original, self.name, payload, topic=topic)

    # ── overridables ──
    async def on_message(self, msg: Message):
        pass

    async def tick(self):
        pass

    async def on_start(self):
        pass

    # ── actor loop ──
    async def run_loop(self):
        loop = asyncio.get_running_loop()
        try:
            await self.on_start()
        except Exception as e:
            await self._log_crash("on_start", e)
        next_tick = (loop.time() + self.tick_delay) if self.tick_interval is not None else None
        while self._alive:
            timeout = None
            if next_tick is not None:
                timeout = max(0.0, next_tick - loop.time())
            msg = None
            try:
                if timeout is None:
                    msg = await self.inbox.get()
                else:
                    msg = await asyncio.wait_for(self.inbox.get(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            if msg is not None:
                if msg.topic == _STOP_TOPIC:
                    break
                self.last_active = time.time()
                try:
                    await self.on_message(msg)
                except Exception as e:
                    await self._log_crash(f"on_message:{msg.topic}", e)
            if next_tick is not None and loop.time() >= next_tick:
                self.last_active = time.time()
                try:
                    await self.tick()
                except Exception as e:
                    await self._log_crash("tick", e)
                next_tick = loop.time() + self.tick_interval
        await self.save_state()

    async def _log_crash(self, where, exc):
        # One agent's crash must never take down the desk.
        await asyncio.to_thread(
            self.memory.log_error, self.name, f"{where}: {exc}", traceback.format_exc()
        )

    def request_stop(self):
        self._alive = False


class AgentRuntime:
    """Owns the bus and the agent tasks. Agents are constructed once and
    live for the process lifetime — state accumulates in-object and in
    the agent_state table, not rebuilt every cycle."""

    def __init__(self, agent_classes, bus: MessageBus | None = None, services: dict | None = None):
        self.bus = bus or MessageBus()
        self.services = services or {}
        self.services.setdefault("runtime", self)
        self.agents = [cls(self.bus, self.services) for cls in agent_classes]
        self._stop = None   # asyncio.Event bound to the runtime loop
        self._loop = None
        self._thread = None
        self._tasks = []

    def agent(self, name):
        for a in self.agents:
            if a.name == name:
                return a
        return None

    def heartbeats(self):
        return {a.name: a.last_active for a in self.agents}

    def dead_agents(self):
        """Agents whose actor task exited while the desk is still running —
        run_loop swallows handler errors, so a finished task means the loop
        itself died and the agent will never answer again."""
        if not self._tasks or (self._stop is not None and self._stop.is_set()):
            return []
        return [t.get_name().split(":", 1)[-1] for t in self._tasks if t.done()]

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        tasks = [asyncio.create_task(a.run_loop(), name=f"agent:{a.name}") for a in self.agents]
        self._tasks = tasks
        await self._stop.wait()
        for a in self.agents:
            a.request_stop()
            await self.bus.send("runtime", a.name, _STOP_TOPIC, {}, persist=False)
        done, pending = await asyncio.wait(tasks, timeout=10)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def shutdown(self):
        """Thread-safe stop signal."""
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def start_in_thread(self):
        """Run the actor loop in a daemon thread (main thread keeps the
        dashboard). Returns the thread; use shutdown() to stop."""
        def _runner():
            try:
                asyncio.run(self.run())
            except Exception as e:
                SharedMemory().log_error("runtime", str(e), traceback.format_exc())
        self._thread = threading.Thread(target=_runner, name="agent-runtime", daemon=True)
        self._thread.start()
        return self._thread

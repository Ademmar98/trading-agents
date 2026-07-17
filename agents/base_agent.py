import asyncio
import time
from abc import abstractmethod
from typing import Any, Dict, Optional

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.agent_bus import AgentBus, AgentMessage, get_bus
from core.memory import SharedMemory
from core.notifier import Notifier


_SHARED_NOTIFIER = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


class BaseAgent:
    """Async-capable base agent with persistent state and bus integration.

    Subclasses must implement:
      - `run_cycle()` — the agent's main work (async)
      - `interval_seconds` — how often to run (default: 60)

    The agent lives in its own asyncio task, maintains state across cycles,
    and communicates via the AgentBus (pub/sub + request/reply).
    """
    name: str = "base"
    interval_seconds: float = 60.0  # override in subclass
    notifier = _SHARED_NOTIFIER

    def __init__(self):
        self.memory = SharedMemory()
        self.bus: AgentBus = get_bus()
        self.state: Dict[str, Any] = {}
        self._last_run: float = 0.0
        self._cycle_count: int = 0
        self._running: bool = False
        self._subscriptions: list = []

    # ── Lifecycle ──

    async def _run_loop(self):
        """Internal loop — do not override. Runs in an asyncio task."""
        self._running = True
        # Subscribe to topics this agent cares about
        await self.on_start()

        while self._running:
            try:
                now = time.time()
                if now - self._last_run >= self.interval_seconds:
                    self._last_run = now
                    self._cycle_count += 1
                    await self._run_cycle_wrapped()
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._on_error(e)
            # Sleep in small chunks so cancellation is responsive
            await asyncio.sleep(min(self.interval_seconds, 5.0))

        await self.on_stop()

    async def _run_cycle_wrapped(self):
        """Wrapper that logs, emits events, and handles errors."""
        await self.bus.publish("agent.started_cycle", {
            "agent": self.name,
            "cycle": self._cycle_count,
        }, sender=self.name)

        try:
            result = await self.run_cycle()
        except Exception as e:
            await self._on_error(e)
            result = None

        await self.bus.publish("agent.finished_cycle", {
            "agent": self.name,
            "cycle": self._cycle_count,
            "result": result,
        }, sender=self.name)

    async def _on_error(self, exc: Exception):
        import traceback
        err = {
            "agent": self.name,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        await self.bus.publish("errors", err, sender=self.name)
        self.log(f"ERROR: {exc}")

    # ── Override hooks ──

    async def on_start(self):
        """Called once when the agent task starts. Subscribe to bus topics here."""
        pass

    async def on_stop(self):
        """Called once when the agent task is cancelled. Unsubscribe here."""
        for handler, topic in self._subscriptions:
            self.bus.unsubscribe(topic, handler)
        self._subscriptions.clear()

    @abstractmethod
    async def run_cycle(self) -> Any:
        """The agent's main work. Must be implemented by subclasses.

        Use self.bus.publish() to emit events.
        Use self.bus.request() to get data from other agents.
        Use self.state to persist data across cycles.
        """
        raise NotImplementedError

    # ── Helpers ──

    def log(self, message: str):
        self.memory.log(self.name, message)

    async def subscribe(self, topic: str, handler):
        """Subscribe to a bus topic. Auto-unsubscribed on stop."""
        self.bus.subscribe(topic, handler)
        self._subscriptions.append((handler, topic))

    def stop(self):
        """Signal the agent loop to exit."""
        self._running = False

    def is_running(self) -> bool:
        return self._running

    # ── Backward compatibility for sequential callers ──

    def run(self):
        """Synchronous fallback for legacy sequential pipeline.
        Creates a temporary event loop if needed."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're already in async context — schedule a task
            asyncio.create_task(self._run_cycle_wrapped())
        else:
            # Legacy: run synchronously
            try:
                self._cycle_count += 1
                # Subclasses that haven't been ported to async yet
                # may still define a sync run_cycle()
                import inspect
                if inspect.iscoroutinefunction(self.run_cycle):
                    asyncio.run(self.run_cycle())
                else:
                    # Legacy sync agent
                    self._run_sync_legacy()
            except NotImplementedError:
                # Base contract: a bare BaseAgent (or a subclass that
                # implements neither run() nor run_cycle()) is not runnable.
                # Never swallow this — it must fail loudly.
                raise
            except Exception as e:
                self.log(f"ERROR: {e}")

    def _run_sync_legacy(self):
        """For agents that haven't been ported to async yet."""
        # Try to call the old sync run() if it exists on subclass
        if hasattr(self, '_sync_run') and callable(self._sync_run):
            self._sync_run()

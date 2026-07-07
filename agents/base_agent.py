from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.memory import SharedMemory
from core.notifier import Notifier


_SHARED_NOTIFIER = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


class BaseAgent:
    name: str = "base"
    notifier = _SHARED_NOTIFIER

    def __init__(self):
        self.memory = SharedMemory()

    def log(self, message: str):
        self.memory.log(self.name, message)

    def run(self):
        raise NotImplementedError

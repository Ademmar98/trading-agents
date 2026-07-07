from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.memory import SharedMemory
from core.notifier import Notifier


class BaseAgent:
    name: str = "base"

    def __init__(self):
        self.memory = SharedMemory()
        self.notifier = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    def log(self, message: str):
        self.memory.log(self.name, message)

    def run(self):
        raise NotImplementedError

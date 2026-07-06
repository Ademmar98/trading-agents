from core.memory import SharedMemory


class BaseAgent:
    name: str = "base"

    def __init__(self):
        self.memory = SharedMemory()

    def log(self, message: str):
        self.memory.log(self.name, message)

    def run(self):
        raise NotImplementedError

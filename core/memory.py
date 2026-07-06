import json
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR


class SharedMemory:
    def __init__(self):
        self.dirs = {
            "analyses": DATA_DIR / "analyses",
            "decisions": DATA_DIR / "decisions",
            "orders": DATA_DIR / "orders",
            "reports": DATA_DIR / "reports",
            "logs": DATA_DIR / "logs",
        }
        for d in self.dirs.values():
            d.mkdir(parents=True, exist_ok=True)

    def write(self, category: str, filename: str, data: Any):
        path = self.dirs[category] / f"{filename}.json"
        data["_timestamp"] = time.time()
        path.write_text(json.dumps(data, indent=2, default=str))

    def read(self, category: str, filename: str) -> dict | None:
        path = self.dirs[category] / f"{filename}.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def list_files(self, category: str, suffix=".json"):
        path = self.dirs[category]
        return sorted(path.glob(f"*{suffix}"), reverse=True)

    def read_latest(self, category: str) -> dict | None:
        files = self.list_files(category)
        if files:
            return json.loads(files[0].read_text())
        return None

    def log(self, agent: str, message: str):
        entry = {"agent": agent, "message": message, "time": time.time()}
        log_file = self.dirs["logs"] / "journal.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_error(self, source: str, message: str, trace: str = ""):
        entry = {"source": source, "message": message, "trace": trace, "time": time.time()}
        error_file = self.dirs["logs"] / "errors.jsonl"
        with open(error_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_recent_errors(self, n=50):
        error_file = self.dirs["logs"] / "errors.jsonl"
        if not error_file.exists():
            return []
        lines = [l for l in error_file.read_text().strip().splitlines() if l.strip()]
        result = []
        for l in lines[-n:]:
            try:
                result.append(json.loads(l))
            except Exception:
                pass
        return result

    def get_recent_logs(self, n=20):
        log_file = self.dirs["logs"] / "journal.jsonl"
        if not log_file.exists():
            return []
        lines = [l for l in log_file.read_text().strip().splitlines() if l.strip()]
        result = []
        for l in lines[-n:]:
            try:
                result.append(json.loads(l))
            except Exception:
                pass
        return result

    def read_portfolio(self):
        return self.read("reports", "portfolio") or {}

    def write_portfolio(self, data):
        self.write("reports", "portfolio", data)

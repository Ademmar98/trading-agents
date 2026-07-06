import pytest

from agents.base_agent import BaseAgent


class TestBaseAgent:
    def test_init(self):
        agent = BaseAgent()
        assert agent.name == "base"

    def test_log(self):
        agent = BaseAgent()
        agent.log("test message")
        logs = agent.memory.get_recent_logs(10)
        assert any("test message" in l["message"] for l in logs)

    def test_run_raises(self):
        agent = BaseAgent()
        with pytest.raises(NotImplementedError):
            agent.run()

    def test_name_override(self):
        class TestAgent(BaseAgent):
            name = "test_agent"
        agent = TestAgent()
        assert agent.name == "test_agent"

    def test_custom_log(self):
        class TestAgent(BaseAgent):
            name = "test_agent"
            def run(self):
                self.log("custom run")
                return "done"
        agent = TestAgent()
        result = agent.run()
        assert result == "done"
        logs = agent.memory.get_recent_logs(10)
        assert any("custom run" in l["message"] for l in logs)

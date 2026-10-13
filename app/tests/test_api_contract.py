"""Contract tests for the stateless chat API.

These tests pin the HTTP contract and the stateless semantics of
``AgentRuntime.chat(history=...)`` without touching Ollama or the network:
the LLM-facing seams (goal extractor, planner, executor) are stubbed.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""

import os
import sys

# Make the app package root importable regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

import server


# ── HTTP contract ────────────────────────────────────────────────────────────


class _RecordingAgent:
    """Stands in for the real agent: records every call it receives."""

    def __init__(self, answer="echo-answer"):
        self.answer = answer
        self.calls = []

    def chat(self, user_input, history=None):
        self.calls.append((user_input, list(history or [])))
        return self.answer


@pytest.fixture()
def recording_agent(monkeypatch):
    agent = _RecordingAgent()
    builds = []

    def factory():
        builds.append(agent)
        return agent

    monkeypatch.setattr(server, "build_agent", factory)
    client = TestClient(server.app)
    return client, agent, builds


def test_health(recording_agent):
    client, _, _ = recording_agent
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_chat_accepts_message_only_and_returns_answer(recording_agent):
    client, agent, _ = recording_agent
    res = client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 200
    assert res.json() == {"answer": "echo-answer"}
    (user_input, history), = agent.calls
    assert user_input == "hello"
    assert history == []


def test_chat_forwards_client_conversation_in_order(recording_agent):
    client, agent, _ = recording_agent
    res = client.post(
        "/api/chat",
        json={
            "message": "third question",
            "conversation": [
                {"role": "user", "content": "first"},
                {"role": "agent", "content": "one"},
                {"role": "user", "content": "second"},
            ],
        },
    )
    assert res.status_code == 200
    (_, history), = agent.calls
    assert [m.type for m in history] == ["human", "ai", "human"]
    assert [m.content for m in history] == ["first", "one", "second"]


def test_chat_rejects_unknown_role(recording_agent):
    client, _, _ = recording_agent
    res = client.post(
        "/api/chat",
        json={
            "message": "hi",
            "conversation": [{"role": "system", "content": "x"}],
        },
    )
    assert res.status_code in (400, 422)


def test_chat_rejects_empty_message(recording_agent):
    client, _, _ = recording_agent
    res = client.post("/api/chat", json={"message": ""})
    assert res.status_code in (400, 422)


def test_chat_returns_500_when_agent_raises(monkeypatch):
    def broken_factory():
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "build_agent", broken_factory)
    client = TestClient(server.app)
    res = client.post("/api/chat", json={"message": "hi"})
    assert res.status_code == 500


# ── Stateless semantics of AgentRuntime.chat ────────────────────────────────


def _make_stub_agent(tmp_path):
    """A real AgentRuntime with every LLM-facing seam stubbed out."""
    from agent import AgentRuntime
    from agent_config import AgentConfig
    from evaluator import TaskEvaluator
    from goals.goal import Goal
    from memory import ConversationMemory
    from state import AgentState
    from tasks.task import Task

    (tmp_path / "AGENTS.md").write_text("You are a test harness.")

    class StubExtractor:
        def __init__(self):
            self.seen_histories = []

        def extract(self, user_input, history):
            self.seen_histories.append(list(history))
            return Goal(intent="stub intent", requirements=[user_input])

    class StubPlanner:
        def create_plan(self, state):
            goal_text = getattr(state.goal, "text", None) or str(state.goal)
            return [Task(id="t1", description=goal_text, success_criteria="ok")]

    class StubExecutor:
        def __init__(self):
            self.ran = []

        def execute(self, task, state):
            self.ran.append(task.description)
            return "ANSWER"

    extractor = StubExtractor()
    executor = StubExecutor()
    state = AgentState(agent_id="test", goal=None, conversation=ConversationMemory())
    agent = AgentRuntime(
        planner=StubPlanner(),
        evaluator=TaskEvaluator(),
        task_executor=executor,
        state=state,
        config=AgentConfig(agent_path=str(tmp_path / "AGENTS.md")),
        goal_extractor=extractor,
    )
    return agent, extractor, executor


def test_chat_with_history_is_stateless_between_turns(tmp_path):
    agent, extractor, executor = _make_stub_agent(tmp_path)

    first = agent.chat("turn one", history=[HumanMessage(content="prior-1")])
    seen_first = extractor.seen_histories[-1]
    assert any(m.content == "prior-1" for m in seen_first)

    second = agent.chat(
        "turn two", history=[HumanMessage(content="fresh-1"), AIMessage(content="fresh-2")]
    )
    seen_second = extractor.seen_histories[-1]
    contents = [m.content for m in seen_second]
    assert contents == ["You are a test harness.", "fresh-1", "fresh-2"]
    # No leak from the previous turn's conversation.
    assert "prior-1" not in contents
    assert "turn one" not in " ".join(c for c in contents if isinstance(c, str))

    assert first == "ANSWER"
    assert second == "ANSWER"
    assert executor.ran  # both turns actually executed their task


def test_chat_without_history_keeps_server_side_memory(tmp_path):
    """The REPL path: omit history and turns accumulate in shared memory."""
    agent, extractor, _ = _make_stub_agent(tmp_path)

    agent.chat("turn one")
    agent.chat("turn two")

    seen_second = extractor.seen_histories[-1]
    contents = [m.content for m in seen_second if isinstance(m.content, str)]
    # Turn one's goal banner entered the shared conversation during turn one
    # and must be visible when turn two is extracted.
    joined = " ".join(contents)
    assert "turn one" in joined


def test_system_prompt_is_prepended_to_client_history(tmp_path):
    agent, extractor, _ = _make_stub_agent(tmp_path)
    agent.chat("hi", history=[HumanMessage(content="client-only")])
    seen = extractor.seen_histories[-1]
    assert seen[0].type == "system"
    assert seen[0].content == "You are a test harness."

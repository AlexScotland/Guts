"""Deterministic no-LLM executor for offline presets and tests.

Answers with an ``ECHO`` banner plus the task description, so swapping it in
mid-conversation is observable without any model behind the harness.
"""
import logging

logger = logging.getLogger(__name__)


class EchoExecutor:
    """Marks the task complete and returns an echo of its description."""

    def execute(self, task, state) -> str:
        logger.info("EchoExecutor running task %s", task.id)
        task.start()
        echo = f"ECHO[{task.id}]: {task.description}"
        task.complete(echo)
        return echo

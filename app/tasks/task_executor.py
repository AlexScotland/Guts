import logging

from langchain_core.messages import HumanMessage, ToolMessage

from tasks.interfaces import TaskExecutor
from tools.tool_call import ToolCall

logger = logging.getLogger(__name__)


class LLMTaskExecutor(TaskExecutor):

    MAX_EXTENSION = 5
    HARD_CAP = 15

    def __init__(self, llm, tool_registry, max_iterations=30):
        self.llm = llm
        self.registry = tool_registry
        if len(self.registry.list()) > 0:
            self.llm.with_tools(self.registry.list())
        self.max_iterations = max_iterations

    def execute_tools(self, tool_calls, state):
        for tool_call in tool_calls:
            try:
                result = self.registry.execute(tool_call)
                logger.debug("Tool %s executed", tool_call.name)
            except Exception as e:
                logger.error("Error executing tool %s: %s", tool_call.name, str(e))
                result = f"Error executing tool {tool_call.name}: {str(e)}"

            state.conversation.add(
                ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call.id
                )
            )

    def _request_commit(self, state):
        """Forced final-answer round when the hard cap is hit."""
        state.conversation.add(
            HumanMessage(
                content=(
                    "You have exhausted your step budget for this task. "
                    "Do NOT call any tools now. Based on the information "
                    "you already have, commit to your best answer and state it directly."
                )
            )
        )
        final = self.llm.invoke(state.conversation.get())
        logger.info("Commit-forced answer: %s", final)
        state.conversation.add(final)
        return (final.content or "").strip() if final else ""

    def execute(self, task, state):
        logger.info(
            "Executing task: %s (budget=%s)",
            task.description,
            getattr(task, "budget", 6)
        )
        task.start()

        budget = min(getattr(task, "budget", 6) or 6, self.HARD_CAP)
        ceiling = min(budget + self.MAX_EXTENSION, self.HARD_CAP)
        checkpoint = max(1, budget * 3 // 5)
        extended = False
        iterations = 0

        state.conversation.add(
            HumanMessage(content=task.description)
        )

        while iterations < ceiling:
            iterations += 1
            logger.info(
                "Task iteration %s/%s (checkpoint=%s)",
                iterations,
                ceiling,
                checkpoint
            )

            response = self.llm.invoke(state.conversation.get())
            logger.info("LLM response: %s", response)
            state.conversation.add(response)

            if not response.tool_calls:
                content = (response.content or "").strip()
                task.complete(content)
                return content

            tool_calls = [
                ToolCall(id=call["id"], name=call["name"], args=call["args"])
                for call in response.tool_calls
            ]
            logger.info("Executing tools: %s", tool_calls)
            self.execute_tools(tool_calls, state)

            if iterations >= checkpoint and not extended:
                extended = True
                remaining = ceiling - iterations
                if remaining > 0:
                    state.conversation.add(
                        HumanMessage(
                            content=(
                                f"Budget check: you have used {iterations} of ~{budget} "
                                f"estimated steps, with at most {remaining} more available "
                                f"(one extension of up to {self.MAX_EXTENSION} is allowed).\n"
                                "Decide now:\n"
                                "1. COMMIT - answer the task directly with what you have "
                                "(no tool calls), or\n"
                                "2. EXTEND - continue and state briefly what you still "
                                "need to check.\n"
                                "If you extend, stay within the remaining budget and "
                                "commit as soon as you have enough."
                            )
                        )
                    )

        logger.warning(
            "Task %s hit hard ceiling after %s iterations",
            task.id,
            iterations
        )
        answer = self._request_commit(state)
        if answer:
            task.complete(answer)
            return answer
        task.fail(
            f"Exceeded ceiling of {ceiling} and produced no final answer"
        )
        return ""

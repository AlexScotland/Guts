import json
import logging

from langchain_core.messages import SystemMessage, HumanMessage
from planners.interfaces import Planner
from tasks.task import Task

logger = logging.getLogger(__name__)


class LLMPlanner(Planner):

    def __init__(self, llm):
        self.llm = llm

    def parse_tasks(self, content) -> list[Task]:
        text = (content or "").strip()
        if text.startswith("```"):
            lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            text = text[start:end + 1]

        raw_tasks = json.loads(text)
        return [
            Task(
                id=t["id"],
                description=t["description"],
                success_criteria=t.get("success_criteria"),
                budget=int(t.get("budget", 6))
            )
            for t in raw_tasks
        ]

    def create_plan(self, state) -> list[Task]:
        system = (
            "You are a planning agent.\n"
            "Your job is to break a user goal into executable tasks.\n"
            "Rules:\n"
            "- Create small, concrete tasks.\n"
            "- Do not execute tools.\n"
            "- Do not solve the problem.\n"
            "- Only describe the steps.\n"
            "- For each task, set 'budget': your estimate of how many tool-call "
            "rounds that task will need (a whole number, 1-20). "
            "Err slightly high for reading-heavy tasks.\n"
            "- Your reply must be a JSON array only. "
            "No Markdown fences, no commentary before or after."
        )

        goal_text = getattr(state.goal, "text", None) or str(state.goal)

        schema_line = json.dumps(
            {
                "id": "short_identifier",
                "description": "what needs to be done",
                "success_criteria": "how to know if it is done",
                "budget": 6
            },
            indent=2
        )
        shape_block = (
            "Return tasks as JSON exactly in this shape (budget is a number):\n"
            "[\n"
            "  " + schema_line.replace("\n", "\n  ") + "\n"
            "]"
        )

        user = (
            "User Goal: " + goal_text + "\n\n"
            + shape_block
        )

        response = self.llm.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=user)
            ]
        )

        try:
            return self.parse_tasks(response.content)
        except json.JSONDecodeError:
            logger.warning("Planner returned non-JSON content: %r", response.content)
            return [
                Task(
                    id="t1",
                    description=goal_text,
                    success_criteria="goal satisfied",
                    budget=6
                )
            ]
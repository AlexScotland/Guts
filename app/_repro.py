"""Repro: one parallel branch fails while the other is in-flight.

Runs in a plain (non-async) thread, exactly like the FastAPI sync ``def chat``
endpoint (starlette dispatches it to a thread-pool worker that has NO running
asyncio loop). That is precisely the branch of ``run_branches`` that takes the
``asyncio.run(self._async_map(...))`` path. Faithful to production.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langchain_core.messages import AIMessage

from components import (
    ComponentContext,
    ComponentRegistry,
    ComponentSlot,
    register_builtin_components,
    register_graph_components,
)
from components.graph_store import GraphStore
from components.loops import LoopComponents
from goals.goal import Goal
from memory import ConversationMemory
from state import AgentState
from tools.tool_registry import ToolRegistry

HERE = os.path.dirname(os.path.abspath(__file__))


class AgentConfigDummy:
    agent_path = os.path.join(HERE, "AGENTS.md")
    agent_name = "repro"


class FakeLLM:
    def with_structured_output(self, schema):
        class _S:
            def invoke(self, messages):
                return type("V", (), {"decision": "pass", "feedback": "ok"})()
        return _S()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="fake-llm-answer")


class BranchExecutor:
    """Simulates the LIVE failure: the 'local' branch raises mid-turn while the
    'web' branch is still in-flight in another thread."""

    def __init__(self, fail_mode="runtime"):
        self.log = []
        self.fail_mode = fail_mode

    def execute(self, task, state):
        desc = task.description or ""
        self.log.append(("start", desc[:24]))
        if "local" in desc:
            self.log.append(("RAISE local", desc[:24]))
            if self.fail_mode == "cancelled":
                raise asyncio.CancelledError()
            raise RuntimeError("local branch Ollama exploded (simulated)")
        if "web" in desc:
            self.log.append(("sleep 1.2s web", desc[:24]))
            time.sleep(1.2)
            self.log.append(("ok web", desc[:24]))
            return "web research done"
        self.log.append(("ok other", desc[:24]))
        return "final-answer-for:" + desc[:20]


def scenario(label: str, fail_mode="runtime"):
    tmp = os.path.join(HERE, "_repro_dir", fail_mode)
    os.makedirs(tmp, exist_ok=True)
    store = GraphStore(os.path.join(tmp, "graphs"))
    context = ComponentContext(llm=FakeLLM(), tools=ToolRegistry(), config=AgentConfigDummy())

    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=store)
    registry.activate(ComponentSlot.LOOP, "graph")

    with open(os.path.join(HERE, "graphs", "killer-research.json")) as fh:
        doc = json.load(fh)
    store.save("killer", doc, name="killer")
    store.set_active("killer")
    loop = registry.active(ComponentSlot.LOOP)

    executor = BranchExecutor(fail_mode=fail_mode)
    state = AgentState(agent_id="repro", conversation=ConversationMemory(), goal=Goal(intent="taxes in Ontario"))
    components = LoopComponents(executor=executor)

    print(f"\n===== {label} =====")
    t0 = time.time()
    try:
        ans = loop.run(components, state)
    except BaseException as exc:  # noqa: BLE001 - a LEAK (containment missing)
        dt = time.time() - t0
        print(f"ESCAPE FAIL {dt:.2f}s  type={type(exc).__name__!r}  str={exc!r}")
        rec = store.last_run("killer")
        print("last-run ->", (rec or {}).get("status"), "|", repr((rec or {}).get("error")))
        return False
    dt = time.time() - t0
    rec = store.last_run("killer")
    events = state.metadata["graph_events"]
    bf = [e for e in events if e["event"] == "branch_failed"]
    checks = {
        "run completed (no escape)": True,
        "answer built from the survivor branch": (
            "final-answer-for:" in str(ans)
        ),
        "sibling branch finished in-flight": ("ok web", "Read-only research: sear") in executor.log,
        "failed branch still ran (raised, not silently skipped)": ("RAISE local", "Read-only research: read") in executor.log,
        "last-run record is ok": (rec or {}).get("status") == "ok",
        "exactly one branch_failed event": len(bf) == 1,
        "event names the branch 'local'": bool(bf) and bf[0]["branch"] == "local",
        "event carries the error type": bool(bf) and (
            "RuntimeError" in bf[0]["detail"] or "CancelledError" in bf[0]["detail"]
        ),
        "event is on the parallel node": bool(bf) and bf[0]["node"] == "fanout",
        "branch slot is [ok-web, None] in declared order": any(
            e.get("event") == "result"
            and e.get("node") == "fanout"
            and e.get("outputs", {}).get("merged") == ["web research done", None]
            for e in events
        ),
        "state is COMPLETED": state.status.value == "completed",
    }
    ok = True
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and bool(passed)
    print(f"  {dt:.2f}s  status={(rec or {}).get('status')}  answer={str(ans)[:60]!r}")
    print("executor log:", executor.log)
    return ok


if __name__ == "__main__":
    ok1 = scenario("killer-research: branch raises RuntimeError while sibling in-flight", fail_mode="runtime")
    ok2 = scenario("killer-research: branch raises asyncio.CancelledError (the LEAK scenario)", fail_mode="cancelled")
    raise SystemExit(0 if (ok1 and ok2) else 1)

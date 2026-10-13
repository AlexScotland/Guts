"""Real-LLM smoke run for the graph loop (EXPLICIT, NON-ASSERTED).

Frozen v0 test rule: real-LLM runs are smoke tests — they prove the wiring
(activation → store → engine → conversation) works end to end, but they are
NEVER test assertions; the fake-driven suite (tests/test_graph_loop.py) is
the oracle. This script prints the answer and the node-event stream.

Run from the ``app/`` directory with Ollama reachable:
    .venv/bin/python graph_smoke.py "Explain this harness's hot-swap story in 3 bullets"

It saves the shipped killer workflow (graphs/killer-research.json) as
``killer-research``, activates the graph loop with it, runs ONE turn, and
prints the answer + every node event (start/done/result/control/...).
"""
import json
import os

GOAL = (
    "Explain this harness's component hot-swap story in exactly 3 bullets, "
    "citing the slot names."
)
GRAPH_ID = "killer-research"
GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "graphs", f"{GRAPH_ID}.json"
)


def main() -> None:
    from components import ComponentSlot
    from components.graph_store import get_graph_store
    from main import build_agent, get_registry

    registry = get_registry()
    store = get_graph_store()

    with open(GRAPH_PATH) as fh:
        doc = json.load(fh)
    store.save(GRAPH_ID, doc, name="Killer workflow (parallel research -> act + critic retries)")
    store.set_active(GRAPH_ID)
    registry.activate(ComponentSlot.LOOP, "graph")
    print(f"activated loop={registry.active_alias(ComponentSlot.LOOP)} "
          f"graph_id={store.active_id}")

    agent = build_agent(registry)
    answer = agent.chat(GOAL)
    print("\n=== ANSWER ===\n" + str(answer))

    last = store.last_run(GRAPH_ID) or {}
    events = last.get("events", [])
    print(f"\n=== {len(events)} NODE EVENTS (status={last.get('status')}) ===")
    for ev in events:
        parts = [ev.get(k) for k in ("event", "node", "branch") if ev.get(k) is not None]
        line = " ".join(str(p) for p in parts)
        detail = ev.get("detail")
        if detail:
            line += f" — {detail[:100]}"
        print("  " + line)


if __name__ == "__main__":
    main()

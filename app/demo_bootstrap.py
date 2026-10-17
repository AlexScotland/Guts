"""Public-demo seed (opt-in via the ``GUTS_DEMO=1`` environment variable).

The shared public instance (see docs/deploy-demo.md) must be usable by a
first-time stranger with zero setup and zero LLM: the ``offline`` preset is
deterministic and makes zero model calls (passthrough goal, trivial plan,
echo executor, unconditional evaluation), and one showcase graph is active
by default so the Loop Designer opens with something interesting already
drawn.

Everything is committed and restart-stable *except* the in-memory last-run
records, which clear on every deploy — exactly the "fresh demo" posture a
public instance wants. A demo visitor who changes the active preset or
graph mutates only their own session view (the registry swap is process-
shared, so treat a shared instance as a shared playground — see the docs).

Usage (Render / any WSGI-less ASGI runtime):
    GUTS_DEMO=1 uvicorn server:app --host 0.0.0.0 --port $PORT

Local dev without the env var: call ``bootstrap_demo()`` by hand, or use
the same two curls the README documents. Nothing here runs unless
``GUTS_DEMO=1`` is set.
"""

import json
import os

from main import get_registry
from components import ComponentSlot
from components.graph_store import get_graph_store

#: The graph the public demo opens on: the manual-route showcase — a
#: route hop with a pinned (manual) decider, so the whole run is
#: deterministic and makes ZERO LLM calls (contract: docs/tutorial.md §9
#: pins exactly this graph for offline capture; `critic`-style graphs
#: deliberately fail-fast under offline — they need a model).
DEFAULT_DEMO_GRAPH = "sample-manual-route"


def _log(msg: str) -> None:
    print(f"[demo-bootstrap] {msg}", flush=True)


def bootstrap_demo() -> dict:
    """Activate the no-LLM demo posture and the showcase graph.

    Idempotent: safe to call more than once (e.g. on every server start).
    Returns a small report dict for logging.
    """
    if os.environ.get("GUTS_DEMO") not in ("1", "true", "yes"):
        return {"active": False, "reason": "GUTS_DEMO is not set"}

    registry = get_registry()
    report: dict = {"active": True}
    try:
        report["preset"] = registry.activate_preset("offline")
        _log("activated 'offline' preset (zero LLM, deterministic)")
    except Exception as exc:  # noqa: BLE001 — a broken demo must log, not die
        report["preset_error"] = str(exc)
        _log(f"WARNING: offline preset activation failed: {exc}")

    try:
        registry.activate(ComponentSlot.LOOP, "graph")
        get_graph_store().set_active(DEFAULT_DEMO_GRAPH)
        report["active_graph"] = DEFAULT_DEMO_GRAPH
        _log(f"activated 'graph' loop on {DEFAULT_DEMO_GRAPH}")
    except Exception as exc:  # noqa: BLE001
        report["graph_error"] = str(exc)
        _log(f"WARNING: demo graph activation failed: {exc}")

    _log(json.dumps(report))
    return report


__all__ = ["bootstrap_demo", "DEFAULT_DEMO_GRAPH"]

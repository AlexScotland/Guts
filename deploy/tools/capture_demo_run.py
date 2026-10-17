#!/usr/bin/env python3
"""Regenerate the GitHub Pages replay fixture from a real GUTS run.

The Pages "replay of a real run" player (deploy/pages/assets/replay/) is fed
a fixture JSON. The fixture is a REAL capture — same posture as
docs/tutorial.md §9: the server is seeded with the zero-LLM ``offline``
preset and the ``sample-manual-route`` graph, so the run is fully
deterministic and makes no model calls. This tool:

  1. seeds the demo posture on a live backend (offline preset + graph loop),
  2. runs the chat turn TWICE and asserts the node-event stream is
     bit-for-bit identical (the property the replay player relies on),
  3. writes the fixture: the graph document, the final answer, the real
     event stream (timestamps stripped), the real wall-clock duration, and
     a DETERMINISTIC per-event presentation pace (the engine finished in
     milliseconds; a human needs pacing to watch it).

Stdlib only. Usage (boots a GUTS_DEMO=1 server locally, or point at any):

    python3 deploy/tools/capture_demo_run.py \
        --base http://127.0.0.1:8909 \
        --out  deploy/pages/assets/replay/run.json
"""

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request

DEFAULT_GRAPH_ID = "sample-manual-route"
DEFAULT_MESSAGE = "Explain how a routing node decides which branch to run."
DEFAULT_OUT = "deploy/pages/assets/replay/run.json"

#: Deterministic per-event presentation pacing (ms). Chosen to read at a
#: human pace without implying engine cost — the REAL duration is reported
#: separately in the fixture (real_duration_ms) and surfaced by the player.
PACE_MS = {
    "start": 280,
    "result": 720,
    "done": 340,
    "skipped": 560,
    "failure": 640,
    "retry": 420,
    "control": 420,
    "aborted": 640,
}
LEAD_IN_MS = 500


def call(base, method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def run_once(base, graph_id, message):
    # Fresh conversation each call (the thread is frontend-owned): run 1 and
    # run 2 must land on the same event stream.
    call(base, "POST", "/api/chat", {"message": message, "conversation": []})
    return call(base, "GET", f"/api/graphs/{graph_id}/last-run")


def canonical(events):
    """Strip wall-clock timestamps — the determinism check compares the
    semantic stream, not its absolute time."""
    return [{k: v for k, v in ev.items() if k != "at"} for ev in events]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8909")
    ap.add_argument("--graph-id", default=DEFAULT_GRAPH_ID)
    ap.add_argument("--message", default=DEFAULT_MESSAGE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    base = args.base.rstrip("/")
    graph_id = args.graph_id

    # ── 1. seed the deterministic posture ────────────────────────────────
    health = call(base, "GET", "/health")
    if not (isinstance(health, dict) and health.get("status") == "ok"):
        print(f"ERROR: backend unhealthy: {health!r}", file=sys.stderr)
        return 1
    call(base, "POST", "/api/components/activate", {"preset": "offline"})
    call(base, "POST", "/api/components/activate",
         {"loop": "graph", "graph_id": graph_id})
    doc = call(base, "GET", f"/api/graphs/{graph_id}")["graph"]

    # ── 2. run twice, assert identical event streams ────────────────────
    last1 = run_once(base, graph_id, args.message)
    last2 = run_once(base, graph_id, args.message)
    if canonical(last1["events"]) != canonical(last2["events"]):
        print("ERROR: event streams differ between runs — the replay fixture "
              "would not be stable. Aborting.", file=sys.stderr)
        print("run1:", json.dumps(last1["events"], indent=2), file=sys.stderr)
        print("run2:", json.dumps(last2["events"], indent=2), file=sys.stderr)
        return 2

    events = last1["events"]
    ats = [ev.get("at") for ev in events]
    real_ms = (max(ats) - min(ats)) * 1000.0 if len(ats) >= 2 else 0.0
    pace = [LEAD_IN_MS] + [PACE_MS.get(ev["event"], 420) for ev in events]

    fixture = {
        "meta": {
            "tool": "capture_demo_run.py",
            "captured_at_utc": datetime.datetime.now(datetime.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "preset": "offline",
            "graph_id": graph_id,
            "graph_name": doc.get("name", ""),
            "message": args.message,
            "llm_calls": 0,
            "note": "REAL capture: the offline preset is deterministic, so "
                    "every run — yours, the live instance's, or this "
                    "replay's — walks the same event stream.",
        },
        "graph": doc,
        "answer": last1.get("answer"),
        "status": last1.get("status"),
        "real_duration_ms": round(real_ms, 1),
        "pace_ms": pace,
        "events": [{k: v for k, v in ev.items() if k != "at"} for ev in events],
    }

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(fixture, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"OK: wrote {args.out}")
    print(f"   {len(events)} events, real_duration={real_ms:.1f}ms, "
          f"determinism=MATCH, answer={fixture['answer'][:60]!r}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())

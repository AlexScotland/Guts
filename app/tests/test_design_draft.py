"""Face 0 — the stateless design core (design/) and its HTTP surface.

Pinned behaviors (docs/graph-loop-designer.md, "AI-assisted design"):
  * draft(goal, notes, current) is STATELESS and LOOP-BLIND: the canonical
    acceptance case is the user's own sentence, and the repeat count / stop
    condition come only from what the model puts in the document — the core
    never counts loops or knows what one is for;
  * the ONLY green door is backend validate_graph: verdict.ok is that
    result verbatim, a red verdict is a normal outcome (doc still returned
    unchanged), and an invalid document NEVER raises;
  * the catalog is pure functions over the SAME PRIMITIVES table the
    backend validates against — no drift between what the model is told and
    what validate_graph enforces;
  * POST /api/designer/draft + GET /api/designer/catalog expose the core.

No network, no real LLM: the completer is an in-process fake, exactly like
the rest of the contract suite.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest
from langchain_core.messages import AIMessage

import server
from design import catalog
from design import draft as draft_mod
from schemas.graph import validate_graph

USER_GOAL = (
    "I want a graph does research, then does a fact check, drops irrelevant "
    "knowledge and loops until a decision of if the AI feels like the "
    "original ask is completed"
)

# The canonical mapping of USER_GOAL into the frozen language — same shape
# family as the saved app/graphs/research-loop.json (research→critic control
# loop), with the drop-irrelevant step as a second shape-compatible research
# pass. Backend-validate_graph is the arbiter (test_canonical_doc_is_valid).
CANONICAL_DOC = {
    "name": "research-until-completed",
    "nodes": {
        "dig": {
            "primitive": "research",
            "config": {"task": "Gather authoritative, current facts that answer the goal; on later passes close the gaps the critic flagged and verify weak claims."},
            "budget": 40,
        },
        "sift": {
            "primitive": "research",
            "config": {"task": "Drop knowledge that is irrelevant to the goal; keep only what the ask actually needs."},
            "budget": 20,
        },
        "judge": {
            "primitive": "critic",
            "config": {"criterion": "pass only when the remaining knowledge answers the original ask"},
        },
    },
    "edges": [
        {"from": "dig", "to": "sift", "type": "data"},
        {"from": "sift", "to": "judge", "type": "data"},
        {"from": "judge", "to": "dig", "type": "control", "max_passes": 8},
    ],
}


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeCompleter:
    """records prompts; replies from a list (str or dict, consumed in order)."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, dict):                 # a document: the model emits JSON text
            reply = json.dumps(reply, indent=2)
        if isinstance(reply, (str, list)):
            reply = AIMessage(content=reply)
        return reply


def last_user_text(fake: FakeCompleter) -> str:
    messages = fake.calls[-1]
    user = [m for m in messages if m["role"] == "user"]
    return user[-1]["content"]


@pytest.fixture
def fake_completer(monkeypatch):
    """Route-level seam: draft()'s default completer becomes a fake."""
    def _install(replies=None):
        fake = FakeCompleter(replies)
        monkeypatch.setattr(draft_mod, "get_completer", lambda: fake)
        return fake
    return _install


@pytest.fixture
def client(fake_completer):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


# ── catalog: the frozen language as pure data ───────────────────────────────


def test_catalog_matches_backend_table():
    prims = catalog.list_primitives()
    assert [p["primitive"] for p in prims] == [
        "prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route",
    ]
    critic = next(p for p in prims if p["primitive"] == "critic")
    assert critic["requires"] == ["result"]
    assert critic["provides"] == ["verdict", "pass"]
    research = next(p for p in prims if p["primitive"] == "research")
    assert research["budgeted"] is True
    assert "context" in research["accepts"] and "context" in research["provides"]


def test_ports_of_roundtrip_and_rejects_unknown():
    p = catalog.ports_of("parallel")
    assert p["primitive"] == "parallel"
    assert p["provides"] == ["merged"]
    with pytest.raises(ValueError):
        catalog.ports_of("frobnicate")


def test_wire_check_mirrors_backend_rule():
    assert catalog.wire_check("research", "critic") is True       # result∩result
    assert catalog.wire_check("research", "research") is True     # context∩context
    assert catalog.wire_check("plan", "critic") is False          # plan∉{result,goal}
    assert catalog.wire_check("research", "prompt") is False      # goal never rides an edge
    assert catalog.wire_check("act", "gate") is False             # result∉{verdict}
    assert catalog.wire_check("critic", "research") is False      # verdict/pass ∉ {goal,context}
    assert catalog.wire_check("nope", "research") is False
    assert catalog.wire_check("research", "nope") is False


def test_canonical_doc_is_valid_backend():
    validate_graph(CANONICAL_DOC)  # the arbiter itself


# ── draft core: stateless + loop-blind ───────────────────────────────────────


def test_user_sentence_roundtrips_to_valid_doc(fake_completer):
    fake = fake_completer([CANONICAL_DOC])
    result = draft_mod.draft(USER_GOAL, notes="loop while the ask is not clearly answered")

    assert result.verdict == {"ok": True, "issues": []}
    assert result.doc == CANONICAL_DOC           # the model's doc, verbatim
    prompt = last_user_text(fake)
    assert USER_GOAL in prompt
    assert "loop while the ask is not clearly answered" in prompt   # notes ride in
    validate_graph(result.doc)                    # green door is backend, not the core


def test_loop_count_comes_from_notes_alone(fake_completer):
    """The invariant: the core has ZERO loop-count intelligence. The same
    goal gets a different max_passes purely because the NOTES differ — the
    (fake) model just does what the business case says; the core merely
    carries the notes into the prompt and reports the backend verdict.
    """
    def doc_with(max_passes: int) -> dict:
        doc = json.loads(json.dumps(CANONICAL_DOC))
        doc["edges"][-1]["max_passes"] = max_passes
        return doc

    fake = FakeCompleter([doc_with(2)])
    r2 = draft_mod.draft(USER_GOAL, notes="Run it exactly twice, then stop.", completer=fake)
    assert "twice" in last_user_text(fake)          # the business case reached the model
    assert r2.doc["edges"][-1]["max_passes"] == 2
    assert r2.verdict["ok"] is True

    fake = FakeCompleter([doc_with(8)])
    r8 = draft_mod.draft(USER_GOAL, completer=fake)
    assert "twice" not in last_user_text(fake)      # no notes → no count instruction
    assert r8.doc["edges"][-1]["max_passes"] == 8
    assert r8.verdict["ok"] is True

    # core behavior identical either way: same goal, same nodes — only the
    # notes-driven max_passes differs. "make it loop twice" needs no core change.
    assert r2.doc["nodes"] == r8.doc["nodes"]


def test_red_verdict_returns_doc_verbatim_and_never_raises(fake_completer):
    bad = {"name": "broken", "nodes": {"x": {"primitive": "frobnicate"}}, "edges": []}
    fake = fake_completer([bad])
    result = draft_mod.draft(USER_GOAL)

    assert result.doc == bad                       # returned verbatim for the caller
    assert result.verdict["ok"] is False
    assert result.verdict["issues"], "a red verdict must carry the validator's reason"
    validate_graph(CANONICAL_DOC)                  # sanity: the arbiter still agrees


def test_current_doc_rides_into_the_prompt(fake_completer):
    fake = fake_completer([CANONICAL_DOC])
    current = dict(CANONICAL_DOC, name="my-tuned-loop")
    draft_mod.draft("tighten this", notes=None, current=current)

    prompt = last_user_text(fake)
    assert "CURRENT DOCUMENT" in prompt
    assert "my-tuned-loop" in prompt                # the revision target is visible


def test_one_repair_retry_then_success(fake_completer):
    fake = fake_completer(["Sure! Here is the graph:", CANONICAL_DOC])
    result = draft_mod.draft(USER_GOAL)

    assert result.verdict["ok"] is True
    assert len(fake.calls) == 2
    assert "not a graph document" in last_user_text(fake)


def test_fence_blocks_are_stripped(fake_completer):
    fake = fake_completer(["```json\n" + json.dumps(CANONICAL_DOC, indent=2) + "\n```"])
    result = draft_mod.draft(USER_GOAL)

    assert result.verdict["ok"] is True
    assert len(fake.calls) == 1                      # no repair needed


def test_two_failures_raise_and_core_never_raises_on_bad_doc(fake_completer):
    fake = fake_completer(["no json here", "still no json here"])
    with pytest.raises(draft_mod.DraftError):
        draft_mod.draft(USER_GOAL)

    # contrast: an invalid DOCUMENT is a result, not an error
    fake2 = fake_completer([{"nodes": {"a": {"primitive": "frobnicate"}}, "edges": []}])
    try:
        r = draft_mod.draft(USER_GOAL, completer=fake2)
        assert r.verdict["ok"] is False
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"invalid doc must not raise, got {exc!r}")


def test_goal_is_required():
    with pytest.raises(draft_mod.DraftError):
        draft_mod.draft("   ")


def test_extract_json_recovers_from_prose_wrapping():
    text = "Here it is: " + json.dumps(CANONICAL_DOC) + " — hope that helps!"
    assert draft_mod._extract_json(text) == CANONICAL_DOC


# ── HTTP face: POST /api/designer/draft, GET /api/designer/catalog ──────────


def test_api_draft_ok(client, fake_completer):
    fake = fake_completer([CANONICAL_DOC])
    resp = client.post("/api/designer/draft", json={"goal": USER_GOAL})

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == {"ok": True, "issues": []}
    assert body["doc"] == CANONICAL_DOC
    assert USER_GOAL in last_user_text(fake)


def test_api_draft_red_verdict_is_still_200(client, fake_completer):
    bad = {"name": "broken", "nodes": {"x": {"primitive": "frobnicate"}}, "edges": []}
    fake_completer([bad])
    resp = client.post("/api/designer/draft", json={"goal": USER_GOAL, "notes": "tighten"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"]["ok"] is False
    assert body["doc"] == bad


def test_api_draft_requires_goal(client, fake_completer):
    fake_completer([CANONICAL_DOC])
    resp = client.post("/api/designer/draft", json={})
    assert resp.status_code == 422


def test_api_draft_model_unreachable_is_502(monkeypatch, client):
    def _down():
        raise OSError("[Errno -2] Name or service not known")
    monkeypatch.setattr(draft_mod, "get_completer", _down)
    resp = client.post("/api/designer/draft", json={"goal": USER_GOAL})
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


def test_api_draft_model_call_fails_is_502(monkeypatch, client):
    class BoomCompleter:
        def __init__(self):
            self.calls = 0
        def invoke(self, messages):
            self.calls += 1
            raise RuntimeError("httpx.ConnectError: [Errno -2] Name or service not known")
    boom = BoomCompleter()
    monkeypatch.setattr(draft_mod, "get_completer", lambda: boom)

    with pytest.raises(draft_mod.DraftError) as exc:     # core raises a typed error, not a transport leak
        draft_mod.draft(USER_GOAL)
    assert "failed" in str(exc.value)

    resp = client.post("/api/designer/draft", json={"goal": USER_GOAL})
    assert resp.status_code == 502
    assert "design model" in resp.json()["detail"]


def test_api_catalog(client):
    resp = client.get("/api/designer/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["primitive"] for p in body["primitives"]] == [
        "prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route",
    ]
    assert all(r in body["rules"] for r in body["rules"])  # rules is a non-empty list
    assert any("max_passes" in r for r in body["rules"])

"""FastAPI server wrapping the AI agent for HTTP access.

Stateless-by-design: the frontend owns the conversation thread. Every
``POST /api/chat`` carries the full prior conversation alongside the new
message, and the agent is built fresh per request — nothing of the thread
lives in this process, so a restart loses no conversation state.

Component hot-swap lives on the process-shared registry (see main.py):
swapping a slot or activating a preset takes effect from the very next
request, with no restart and no per-request state needed.

Exposes:
  POST /api/chat               — send a message + prior conversation, get the answer
  GET  /api/components        — live slot/preset configuration (described)
  POST /api/components/activate — activate a preset, a slot alias, or the graph
  GET  /api/graphs            — saved graph documents (loop.graph vocabulary)
  POST /api/graphs            — save a graph document (validated, id + doc)
  GET  /api/graphs/{id}       — fetch one saved document (canvas render)
  DELETE /api/graphs/{id}     — delete a saved graph document
  GET  /api/graphs/{id}/last-run — last run record (events = canvas seam)
  GET  /api/agents            — saved agent documents (capability-composition)
  POST /api/agents            — save an agent document (validated, id + doc)
  GET  /api/agents/{id}       — fetch one saved document (designer render)
  DELETE /api/agents/{id}     — delete a saved agent document
  POST /api/agents/{id}/activate — make a saved agent the ACTIVE one
  GET  /health                — liveness probe
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from typing import Literal

from components import ComponentSlot
from components.graph_store import GraphError, describe_graphs, get_graph_store
from components.agent_store import AgentError, describe_agents, get_agent_store
from components.primitives.base import describe_error
from main import build_agent, get_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-backend")

app = FastAPI(
    title="learning-ai-harness API",
    description="Stateless HTTP interface for the AI assistant agent",
    version="1.1.0",
)

# CORS — allow the chat-ui origin (and local dev) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ──────────────────────────────────


class ConversationTurn(BaseModel):
    role: Literal["user", "agent"]
    content: str = Field(..., min_length=1, max_length=50_000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000, description="The new user message")
    # Prior turns of the frontend thread, oldest first. Optional: an empty
    # list is a fresh conversation.
    conversation: list[ConversationTurn] = Field(
        default_factory=list,
        description="Prior conversation turns (oldest first), owned by the frontend.",
    )


class ChatResponse(BaseModel):
    answer: str


class ActivateRequest(BaseModel):
    preset: str | None = Field(default=None, description="Activate a whole preset, e.g. 'fast'.")
    slot: str | None = Field(default=None, description="Activate one slot, e.g. 'executor'.")
    alias: str | None = Field(default=None, description="The alias to activate for the slot.")
    loop: str | None = Field(default=None, description="Activate the loop slot, e.g. 'graph'.")
    graph_id: str | None = Field(
        default=None,
        description="With loop='graph': make this saved graph the active one.",
    )


class GraphSaveRequest(BaseModel):
    id: str = Field(..., description="Stable graph id: a [A-Za-z0-9_-] token.")
    name: str | None = Field(default=None, description="Human-readable name (optional).")
    graph: dict = Field(
        ...,
        description=(
            "The graph document (validated against the frozen v0 contract: "
            "closed 8-primitive vocabulary, typed edges, one entry, bounded retries)."
        ),
    )


class AgentSaveRequest(BaseModel):
    id: str = Field(..., description="Stable agent id: a [A-Za-z0-9_-] token.")
    name: str | None = Field(default=None, description="Human-readable name (optional).")
    agent: dict = Field(
        ...,
        description=(
            "The agent document (validated against the agent-graph contract: "
            "closed vocabulary, persona + model + mcp[] + skill[] + settings + "
            "exactly one loop child)."
        ),
    )


class HealthResponse(BaseModel):
    status: str = "ok"


# ── Routes ───────────────────────────────────


@app.get("/api/components")
def components_status():
    """Describe the live component configuration: for every slot the active
    alias and the available alternatives, plus all named presets."""
    return get_registry().describe()


@app.post("/api/components/activate")
def activate_components(req: ActivateRequest):
    """Swap components in at runtime.

    Exactly one of ``preset`` (a named slot set), ``slot`` + ``alias`` (a
    single slot), or ``loop`` (the loop slot — e.g. ``{"loop": "graph",
    "graph_id": "..."}`` makes a saved graph the active workflow) may be
    given. The swap affects the next /api/chat turn; an in-flight turn
    finishes on the component set it started with.
    """
    chosen = [name for name, value in (("preset", req.preset), ("slot", req.slot), ("loop", req.loop)) if value]
    if len(chosen) != 1:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'preset', 'slot' (with 'alias'), or 'loop'.",
        )
    if req.loop and req.loop != "graph" and req.graph_id:
        raise HTTPException(
            status_code=400,
            detail="'graph_id' is only valid with loop='graph'.",
        )

    registry = get_registry()
    try:
        if chosen == ["loop"]:
            registry.activate(ComponentSlot.LOOP, req.loop)
            if req.loop == "graph" and req.graph_id:
                get_graph_store().set_active(req.graph_id)  # GraphError → 400
        elif chosen == ["preset"]:
            registry.activate_preset(req.preset)
        else:
            try:
                slot = ComponentSlot(req.slot)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown slot {req.slot!r} (expected one of: {', '.join(s.value for s in ComponentSlot)})",
                )
            if not req.alias:
                raise HTTPException(status_code=400, detail="'alias' is required with 'slot'.")
            registry.activate(slot, req.alias)
    except HTTPException:
        raise
    except (GraphError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"activation failed: {exc}")

    return registry.describe()


@app.get("/api/graphs")
def list_graphs():
    """Saved graph documents (the `loop.graph` vocabulary): the active id +
    one line per document. Graphs are JSON files; the store validates every
    document against the frozen v0 contract at save time."""
    return describe_graphs(get_graph_store())


@app.post("/api/graphs")
def save_graph(req: GraphSaveRequest):
    """Save (create or overwrite) a graph document by id.

    The document is validated against the frozen v0 contract (closed
    primitive vocabulary, typed edges, one entry, bounded retries) BEFORE
    it is persisted — an invalid graph is a 400, never a saved file.

    Returns the same shape as GET /api/graphs and DELETE (``{active,
    graphs: [...]}``) — one family shape, so the client can parse any of
    the three endpoints identically.
    """
    try:
        get_graph_store().save(req.id, req.graph, name=req.name)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return describe_graphs(get_graph_store())


@app.get("/api/graphs/{graph_id}")
def get_graph(graph_id: str):
    """Fetch one saved graph document by id (id + name + the full document)
    so the canvas can render it. 404 when the id is not a saved graph."""
    store = get_graph_store()
    try:
        doc = store.get(graph_id)
    except (KeyError, GraphError):
        raise HTTPException(status_code=404, detail=f"unknown graph {graph_id!r}")
    return {
        "id": graph_id,
        "name": doc.get("name") or graph_id,
        "graph": doc,
    }


@app.delete("/api/graphs/{graph_id}")
def delete_graph(graph_id: str):
    """Delete a saved graph document (and its last-run record)."""
    store = get_graph_store()
    if not store.delete(graph_id):
        raise HTTPException(status_code=404, detail=f"unknown graph {graph_id!r}")
    return describe_graphs(store)


@app.get("/api/graphs/{graph_id}/last-run")
def last_graph_run(graph_id: str):
    """Run record for a saved graph (status, answer, node events) —
    the API + future-canvas seam (frozen v0 contract: observability).

    While a run is IN FLIGHT this returns the LIVE record
    ({status: "running", at, events: [...so far]}), so the designer can
    highlight the currently executing node; poll it while /api/chat is
    pending and the finished record ({ok|failed, answer, events}) appears
    afterwards. Records are in-memory per process: a server restart clears
    them (the graph files themselves persist); 404 when unknown or not yet
    run here and nothing is in flight."""
    store = get_graph_store()
    if graph_id not in store.ids():
        raise HTTPException(status_code=404, detail=f"unknown graph {graph_id!r}")
    record = store.live_run(graph_id)
    if record is None:
        record = store.last_run(graph_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"graph {graph_id!r} has no run recorded in this process yet",
        )
    return record


# ── Agents (the capability-composition document, docs/agent-graph.md) ──────


@app.get("/api/agents")
def list_agents():
    """Saved agent documents (the agent-graph vocabulary): the active id +
    one line per document (persona, model, mcp, skills, settings, loop).
    Agents are JSON files; the store validates every document against the
    closed-vocabulary contract at save time."""
    return describe_agents(get_agent_store())


@app.post("/api/agents")
def save_agent(req: AgentSaveRequest):
    """Save (create or overwrite) an agent document by id.

    The document is validated against the closed-vocabulary agent contract
    (exactly one loop child, closed mcp/providers enums, skill paths under
    the app root, settings keys ⊆ the six slot names) BEFORE it is
    persisted — an invalid agent is a 400, never a saved file.

    Returns the same shape as GET /api/agents and DELETE (``{active,
    agents: [...]}``) — one family shape, like the graph door.
    """
    try:
        get_agent_store().save(req.id, req.agent, name=req.name)
    except AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return describe_agents(get_agent_store())


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    """Fetch one saved agent document by id (id + name + the full document)
    so the designer can render it. 404 when the id is not a saved agent."""
    store = get_agent_store()
    try:
        doc = store.get(agent_id)
    except (KeyError, AgentError):
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    return {
        "id": agent_id,
        "name": doc.get("name") or agent_id,
        "agent": doc,
    }


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str):
    """Delete a saved agent document (the bind it produced, if any, is
    dropped with it — activation is the only live effect, and the hot path
    re-resolves per turn)."""
    store = get_agent_store()
    if not store.delete(agent_id):
        raise HTTPException(status_code=404, detail=f"unknown agent {agent_id!r}")
    return describe_agents(store)


@app.post("/api/agents/{agent_id}/activate")
def activate_agent(agent_id: str):
    """Make a saved agent the ACTIVE one (flips the store's active id).

    Bind-then-run: activation lands on the NEXT /api/chat turn (the pinned
    per-turn-snapshot semantics) exactly like every slot swap — no restart,
    no in-flight change. The bind itself (model/MCP/skills/persona onto the
    shared context) happens over the existing slot/turn-graph seams; this
    route owns the door + the active pointer.
    """
    store = get_agent_store()
    try:
        store.set_active(agent_id)
    except (KeyError, AgentError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return describe_agents(store)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Send a message (plus the frontend's prior conversation) to the AI
    agent and return its answer.

    The agent is stateless across requests: ``req.conversation`` is loaded
    fresh for this turn, and any new context the agent creates (goal, plan,
    tool results) is scoped to this call only.

    SYNC ``def`` on purpose: ``agent.chat`` blocks for the whole turn (LLM
    + tool I/O). Running it in the thread pool (what a ``def`` endpoint
    gets) keeps the uvicorn event loop free to accept and serve other
    requests while a turn is in flight — including the
    ``GET /api/graphs/{id}/last-run`` live-record polls and any concurrent
    turn. An ``async def`` here would freeze the loop for the entire turn.
    """
    try:
        history = [
            HumanMessage(content=turn.content) if turn.role == "user"
            else AIMessage(content=turn.content)
            for turn in req.conversation
        ]
        agent = build_agent()
        answer = agent.chat(req.message, history=history)
        return ChatResponse(answer=answer)
    except (KeyboardInterrupt, SystemExit):
        # Never turn a server shutdown / Ctrl-C into a 500 — let them escape.
        raise
    except BaseException as exc:
        # Catch BaseException, not just Exception: a leaked cancellation is a
        # realistic failure mode (e.g. asyncio.CancelledError when a parallel
        # branch is cancelled mid-run). If it slips past `except Exception`,
        # the framework's default 500 has NO JSON body — the client sees
        # "Backend error 500" with an empty detail, and the graph run record
        # is never written (see graph_loop.run). Catch it here and surface a
        # diagnostic body.
        logger.exception("Agent turn failed: %s", describe_error(exc))
        raise HTTPException(status_code=500, detail=describe_error(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)

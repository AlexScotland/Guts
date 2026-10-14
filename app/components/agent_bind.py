"""Bind-then-run: resolve the active agent document onto the existing seams.

The doc's crux (docs/agent-graph.md): an agent graph is a configuration
layer that the slot/turn-graph machinery already enforces — no new
execution path, no new transport. Binding lands each piece on an
already-existing seam:

  * ``loop``     → the loop slot (``graph`` alias + the graph store's
                   active id, or a built-in loop alias) — exactly what the
                   activate route does today;
  * ``settings`` → one ``registry.activate(slot, alias)`` per entry (e.g.
                   ``memory: trimmed``);
  * ``model``    → a fresh provider on ``context.llm`` + rebuild of the
                   slots that captured the LLM at construction
                   (registry.rebuild — a plain re-activate is idempotent
                   and would keep the stale instance);
  * ``mcp[]``    → the process-shared McpPool (alias-keyed, pooled — never
                   spawned per turn); the translated tools land in the
                   context's ToolRegistry as ordinary entries (the
                   executor already iterates it, zero special-casing);
  * ``prompt`` /
  * ``skill[]``  → the composed persona (prompt REPLACES the AGENTS.md
                   default; skill fragments ride alongside), materialized
                   to a file and pointed at via ``agent_path`` — the
                   read path (AgentRuntime) is never touched, because
                   ``build_agent()`` runs per turn.

The failure model is the doc's: a bind failure is VISIBLE — an
``AgentBindError`` on the activation route (a 400), never a silent empty
toolset or missing persona. The run phase is untouched: the existing
per-turn snapshot (``_snapshot_components`` → ``loop.run``) proceeds
byte-for-byte as today; a bind made mid-turn lands on the NEXT turn.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading

from .registry import (
    ComponentAliasError,
    ComponentNotRegisteredError,
    ComponentRegistry,
)
from .slots import ComponentSlot

from mcp import McpError, McpPool, McpPoolError, McpToolAdapter

#: The slots that capture ``context.llm`` at CONSTRUCTION time (their
#: factories read ``ctx.llm`` when the registry activates them): a new model
#: therefore needs a forced rebuild of exactly these three — the loop slots
#: read the context live per turn (graph primitives via ``state.ctx``) and
#: need nothing.
_LLM_SLOTS = (ComponentSlot.GOAL_RESOLVER, ComponentSlot.PLANNER, ComponentSlot.EXECUTOR)

_MCP_PREFIX = "mcp_"
_UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")

_EXTRAS_BIND_STATE = "agent_bind"     # {agent, signature, prev_agent_path}
_EXTRAS_MCP = "mcp"                    # {alias: [tool names]} — the bound set
_EXTRAS_PERSONA_PATH = "persona_path"  # the composed persona file, if any


class AgentBindError(ValueError):
    """A VISIBLE bind failure (save already validated the document; bind is
    where a connection / model / persona actually has to be built). The
    activation route maps this to a 400; it never degrades silently."""


# ── the process-shared MCP pool (the one genuinely new runtime piece) ──────

_mcp_pool: McpPool | None = None
_mcp_pool_lock = threading.Lock()


def get_mcp_pool() -> McpPool:
    """The process-shared :class:`McpPool` (lazy; default factory = the real
    ``open_connection``). Bind tests inject their own fake pool explicitly,
    so this singleton only serves the live server / REPL paths."""
    global _mcp_pool
    with _mcp_pool_lock:
        if _mcp_pool is None:
            from mcp import open_connection

            _mcp_pool = McpPool(factory=open_connection)
        return _mcp_pool


def reset_mcp_pool():
    """Test helper: drain and drop the process-shared pool."""
    global _mcp_pool
    with _mcp_pool_lock:
        if _mcp_pool is not None:
            try:
                _mcp_pool.drain()
            except Exception:
                pass
        _mcp_pool = None


# ── seams (injectable per the repo's fakes-only rule) ─────────────────────


def _default_skill_root() -> str:
    # components/agent_bind.py -> components/ -> app/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_agents_dir() -> str:
    return os.path.join(_default_skill_root(), "agents")


def _default_model_factory(model_spec: dict):
    """Build the v1 LLM provider(s) from a validated ``model`` entry.

    ``ollama`` is the closed v1 enum; extending it means adding a factory
    HERE (a deliberate, visible change) — nothing routes silently."""
    provider = model_spec.get("provider")
    model_id = model_spec.get("id")
    if provider == "ollama":
        from inference.ollama import OllamaProvider

        return OllamaProvider(model=model_id)
    raise AgentBindError(f"bind model: no factory for provider {provider!r} (v1: ollama)")


def _graph_store_for(registry: ComponentRegistry):
    extras = registry.context.extras if isinstance(registry.context.extras, dict) else {}
    stored = extras.get("graph_store")
    if stored is not None:
        return stored
    from .graph_store import get_graph_store

    return get_graph_store()


# ── helpers ────────────────────────────────────────────────────────────────


def _adapter_name(alias: str, remote_name: str) -> str:
    """A harness tool name for one remote MCP tool: the alias prefix keeps
    two servers that both expose ``echo`` from colliding."""
    safe_alias = _UNSAFE_NAME.sub("_", alias) or "mcp"
    safe_remote = _UNSAFE_NAME.sub("_", remote_name) or "tool"
    return f"{_MCP_PREFIX}{safe_alias}_{safe_remote}"


def _extras(context) -> dict:
    extras = context.extras
    return extras if isinstance(extras, dict) else {}


def _set_extras(context, mapping: dict) -> None:
    """Update ``context.extras`` in place (it may legitimately be None)."""
    extras = dict(context.extras) if isinstance(context.extras, dict) else {}
    extras.update(mapping)
    context.extras = extras


def _default_base_persona(context) -> str | None:
    """The AGENTS.md-default persona (the current ``agent_path``), unless it
    IS a previously composed agent persona (never nest one agent's persona
    inside the next)."""
    config = getattr(context, "config", None)
    path = getattr(config, "agent_path", None)
    if not path:
        return None
    if _extras(context).get(_EXTRAS_PERSONA_PATH) == path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _materialize_persona(agent_id: str | None, text: str, directory: str | None) -> str:
    directory = directory or _app_agents_dir()
    os.makedirs(directory, exist_ok=True)
    safe = _UNSAFE_NAME.sub("_", agent_id or "agent") or "agent"
    path = os.path.join(directory, f"{safe}.persona.md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def _doc_signature(agent_id: str | None, doc: dict) -> str:
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{agent_id}:{canon}".encode("utf-8")).hexdigest()[:16]


# ── the bind itself ────────────────────────────────────────────────────────


def bind_agent(
    registry: ComponentRegistry,
    doc: dict,
    *,
    agent_id: str | None = None,
    pool: McpPool | None = None,
    model_factory=None,
    skill_root: str | None = None,
    persona_dir: str | None = None,
    graph_store=None,
) -> dict:
    """Resolve one validated agent document onto the live registry/context.

    Returns a summary of what landed (bound loop/model/mcp/skills/settings/
    persona path). Raises :class:`AgentBindError` on the first failure —
    the visible-failure model, ordered so the cheapest checks run first:
    settings → loop → model → mcp → skills → persona.
    """
    if not isinstance(doc, dict):
        raise AgentBindError(f"bind agent: an agent document must be an object; got {type(doc).__name__}")
    context = registry.context
    factory = model_factory or _default_model_factory
    bound: dict = {"loop": None, "model": None, "mcp": {}, "skills": [], "settings": {}, "persona": None}

    # ── settings: slot-alias overrides (the slot path of the activate route)
    for slot_name, alias in (doc.get("settings") or {}).items():
        try:
            registry.activate(ComponentSlot(slot_name), alias)
        except (ComponentAliasError, ValueError) as exc:
            raise AgentBindError(f"bind settings.{slot_name}: {exc}") from exc
        bound["settings"][slot_name] = alias

    # ── loop: the one required child (ref = a saved turn-graph, alias = built-in)
    loop = doc.get("loop")
    if loop is None:
        raise AgentBindError("bind loop: 'loop' is required (an agent with no loop can't run a turn)")
    if loop.get("ref") is not None:
        ref = loop["ref"]
        try:
            registry.activate(ComponentSlot.LOOP, "graph")
            store = graph_store or _graph_store_for(registry)
            store.set_active(ref)
        except (ComponentAliasError, KeyError, ValueError) as exc:
            raise AgentBindError(f"bind loop.ref {ref!r}: {exc}") from exc
        bound["loop"] = {"ref": ref}
    else:
        alias = loop.get("alias")
        try:
            registry.activate(ComponentSlot.LOOP, alias)
        except (ComponentAliasError, ValueError) as exc:
            raise AgentBindError(f"bind loop.alias {alias!r}: {exc}") from exc
        bound["loop"] = {"alias": alias}

    # ── model: a fresh provider on context.llm + rebuild the LLM-capturing slots
    model = doc.get("model")
    if model is not None:
        try:
            provider = factory(model)
        except AgentBindError:
            raise
        except Exception as exc:
            raise AgentBindError(f"bind model: provider construction failed: {exc}") from exc
        context.llm = provider
        for slot in _LLM_SLOTS:
            try:
                registry.rebuild(slot)
            except ComponentNotRegisteredError:
                continue  # slot not registered — nothing built against the old LLM
            except ComponentAliasError as exc:
                raise AgentBindError(f"bind model: rebuild {slot.value!r}: {exc}") from exc
        bound["model"] = model

    # ── mcp: pooled connections + tools merged into the context's ToolRegistry
    declared = doc.get("mcp") or []
    declared_aliases = [entry.get("alias") for entry in declared]
    pool = pool or get_mcp_pool()
    tools_registry = context.tools
    prev_bound = dict(_extras(context).get(_EXTRAS_MCP) or {})
    # an alias the new document no longer declares must not keep serving:
    # drop its stale adapters (the re-activate path).
    if tools_registry is not None:
        for alias, names in prev_bound.items():
            if alias not in declared_aliases:
                for name in names:
                    tools_registry.unregister(name)
    fresh_bound: dict[str, list[str]] = dict(prev_bound)
    new_names: dict[str, list[str]] = {}
    if declared:
        if tools_registry is None:
            raise AgentBindError("bind mcp: the context has no ToolRegistry — tools cannot be merged")
        for entry in declared:
            alias = entry.get("alias")
            if alias in prev_bound:
                pool.reset(alias)  # re-activate: drop + re-spawn with the current spec
            pool.set(alias, entry)
            try:
                remote = pool.tools(alias)  # connect + tools/list — a McpError is a VISIBLE bind failure
            except (McpError, McpPoolError) as exc:
                raise AgentBindError(f"bind mcp[{alias}]: {exc}") from exc
            names = []
            for tool in remote:
                name = _adapter_name(alias, tool.name)
                description = tool.description or f"MCP tool {tool.name!r} served by {alias!r}"
                tools_registry.register(
                    McpToolAdapter(name=name, description=description, alias=alias, tool_name=tool.name, pool=pool)
                )
                names.append(name)
            fresh_bound[alias] = names
            new_names[alias] = names
    _set_extras(context, {_EXTRAS_MCP: fresh_bound})
    bound["mcp"] = fresh_bound

    # ── skills: persona fragments + the declared bundled tools must resolve
    skill_root = skill_root or _default_skill_root()
    fragments: list[str] = []
    for index, entry in enumerate(doc.get("skill") or []):
        path = os.path.join(skill_root, entry.get("path", ""))
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise AgentBindError(f"bind skill[{index}]: {entry.get('path')!r} is not readable text: {exc}") from exc
        fragments.append(text)
        for tool_name in entry.get("tools") or []:
            if tools_registry is None:
                raise AgentBindError(f"bind skill[{index}]: tool {tool_name!r} declared, but the context has no ToolRegistry")
            if tools_registry.get(tool_name) is None:
                raise AgentBindError(
                    f"bind skill[{index}]: tool {tool_name!r} is not registered — a skill's bundled "
                    "tools must exist before bind (visible failure, not a silent empty toolset)"
                )
        bound["skills"].append(entry.get("path"))

    # ── prompt + skills: the composed persona (read path untouched) ────────
    prompt = doc.get("prompt")
    if prompt is not None or fragments:
        parts: list[str] = []
        if prompt:
            parts.append(str(prompt).strip())
        else:
            base = _default_base_persona(context)
            if base:
                parts.append(base)
        parts.extend(f.strip() for f in fragments if f and f.strip())
        persona_text = "\n\n".join(parts)
        if not persona_text:
            raise AgentBindError("bind persona: nothing to compose (no prompt, skills empty, no default) — "
                                 "a visible failure, not a missing persona")
        if context.config is None:
            raise AgentBindError("bind persona: the context has no AgentConfig to point agent_path at")
        persona_path = _materialize_persona(agent_id, persona_text, persona_dir)
        context.config.agent_path = persona_path
        _set_extras(context, {_EXTRAS_PERSONA_PATH: persona_path})
        bound["persona"] = persona_path

    return bound


# ── the lazy per-turn hook (bind at the first turn, idempotent) ────────────


def ensure_agent_bound(registry: ComponentRegistry, *, pool: McpPool | None = None) -> dict | None:
    """Bind the store's ACTIVE agent (if any) onto ``registry`` — idempotent.

    This is the "or lazily at the first turn" half of the bind model:
    ``build_agent()`` runs it per turn, and the signature comparison makes a
    no-op re-bind a dict lookup — no spawn, no file write, no re-activate.
    The bind lands on THIS turn's snapshot (built after it), so activation
    always lands on the next turn exactly like a slot swap.

    When the active agent disappears (delete), the pre-bind ``agent_path``
    is restored — the default persona comes back, no stale persona lingers.
    """
    from .agent_store import get_agent_store

    context = registry.context
    state = _extras(context).get(_EXTRAS_BIND_STATE)
    store = get_agent_store()
    active = store.active_id

    if active is None:
        if state:
            prev_path = state.get("prev_agent_path")
            if prev_path and context.config is not None:
                context.config.agent_path = prev_path
        _set_extras(context, {_EXTRAS_BIND_STATE: None})
        return None

    doc = store.get(active)
    signature = _doc_signature(active, doc)
    if state and state.get("agent") == active and state.get("signature") == signature:
        return None  # same document, same agent — already bound

    prev_path = (state or {}).get("prev_agent_path")
    if prev_path is None and state is None and context.config is not None:
        prev_path = context.config.agent_path  # first bind: remember the default
    try:
        bound = bind_agent(
            registry,
            doc,
            agent_id=active,
            pool=pool,
            persona_dir=store.directory,
            skill_root=getattr(store, "skill_root", None),
            graph_store=_graph_store_for(registry),
        )
    except AgentBindError:
        raise
    except Exception as exc:  # a seam failure is still a VISIBLE bind failure
        raise AgentBindError(f"bind agent {active!r}: {exc}") from exc
    _set_extras(
        context,
        {_EXTRAS_BIND_STATE: {"agent": active, "signature": signature, "prev_agent_path": prev_path}},
    )
    return bound

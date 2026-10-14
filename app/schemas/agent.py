"""The agent-graph contract (docs/agent-graph.md) — closed-vocabulary,
stdlib-only, a sibling of schemas/graph.py.

An **agent** is a *capability-composition document*: persona + model + MCP
servers + skills + slot settings + exactly one ``loop`` child. The loop is
exactly one of:

  * ``ref``   — a saved turn-graph id (transitional; needs the known-ids
                seam at the save site);
  * ``alias`` — a built-in loop alias;
  * ``graph`` — an INLINE turn-graph document, validated with the same
                ``validate_graph`` contract (the stateless v2 path: the
                document travels in the request, nothing is stored).

The agent graph is a **document, not a node** of the turn vocabulary — the
9-primitive dataflow contract stays closed. At bind time the document is
resolved onto the existing slot/turn-graph seams (``bind``-then-``run``),
and the turn runs unchanged; this module only validates the document,
exactly like ``validate_graph``.

``skill[]`` entries carry exactly one of ``text`` (an inline persona
fragment — the stateless v2 path) or ``path`` (a readable file under the
skill root), plus optional declared ``tools``.

Like ``GraphError``, failures are ``AgentError(ValueError)`` with a
human-readable path, raised at the FIRST violation. Purity is a frozen
rule: no app imports; the seams that need the outside world (``loop.ref``
must name a saved graph; ``skill.path`` must exist under a root) are
injected callables/roots at the save site (``agent_store``) instead of
imported here. Importing the sibling ``schemas.graph`` validator is the
one inter-module dependency — both modules are pure/stdlib.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .graph import GraphError, validate_graph

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The six slot names (components/slots.py). ``settings`` keys must be a
#: subset of these. Frozen v1 so this pure module stays import-free.
SLOT_NAMES = ("goal_resolver", "planner", "executor", "evaluator", "memory", "loop")

#: Built-in loop-slot aliases (components/builtin.py). ``loop.alias`` must
#: name one of these; a saved turn-graph is reached via ``loop.ref`` and is
#: NOT an alias value.
KNOWN_LOOP_ALIASES = ("plan_execute", "direct")

#: MCP transports accepted in v1. ``stdio`` spawns ``command``;
#: ``sse``/``http`` connect a URL.
MCP_TRANSPORTS = ("stdio", "sse", "http")

#: Model providers accepted in v1 (closed enum — extend deliberately).
MODEL_PROVIDERS = ("ollama",)

#: The closed top-level vocabulary of an agent document.
ALLOWED_KEYS = ("id", "name", "prompt", "model", "mcp", "skill", "settings", "loop")


class AgentError(ValueError):
    """A violated agent-graph contract rule (save, bind, or describe time)."""


# ── normalized shapes ────────────────────────────────────────────────────────


@dataclass
class LoopSpec:
    """The one required child: exactly one of ``ref`` (a saved graph id —
    transitional), ``alias`` (a built-in loop alias), or ``graph`` (an
    inline, already-validated turn-graph document — the stateless v2
    path). Never two, never neither."""

    ref: str | None
    alias: str | None
    graph: dict | None = None


@dataclass
class McpSpec:
    alias: str
    transport: str
    command: tuple | None      # stdio: argv, declared order
    url: str | None            # sse | http
    headers: dict | None       # optional str->str (trusted-dev posture)


@dataclass
class SkillSpec:
    path: str | None           # XOR with text (exactly one of the two)
    text: str | None = None    # inline persona fragment (the stateless v2 path)
    tools: tuple | None = None  # declared bundled tool names (checked at bind)


@dataclass
class ModelSpec:
    provider: str
    id: str
    params: dict | None


@dataclass
class AgentDoc:
    """A validated, normalized agent document (declared order preserved)."""

    id: str | None
    name: str | None
    prompt: str | None
    model: ModelSpec | None
    mcp: tuple                 # tuple[McpSpec], declared order (a set)
    skill: tuple               # tuple[SkillSpec], declared order (a set)
    settings: dict             # slot name -> alias (normalized, declared order)
    loop: LoopSpec


# ── scalar parsers ───────────────────────────────────────────────────────────


def _validate_id(value: Any, label: str):
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise AgentError(
            f"{label}: id must be a non-empty [A-Za-z0-9_-] token of at most 64 chars; got {value!r}"
        )
    return value


def _validate_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentError(f"{label}: must be a non-empty string; got {value!r}")
    return value


def _reject_unknown_keys(obj: dict, allowed: tuple[str, ...], label: str):
    for key in obj:
        if key not in allowed:
            raise AgentError(
                f"{label}: unknown key {key!r} (closed vocabulary: {', '.join(allowed)}); "
                "nothing routes silently"
            )


# ── node validators ─────────────────────────────────────────────────────────


def _validate_loop(raw: Any) -> LoopSpec:
    if not isinstance(raw, dict):
        raise AgentError(f"loop: must be a JSON object; got {type(raw).__name__}")
    _reject_unknown_keys(raw, ("ref", "alias", "graph"), "loop")
    ref, alias, graph = raw.get("ref"), raw.get("alias"), raw.get("graph")
    present = [
        name
        for name, value in (("ref", ref), ("alias", alias), ("graph", graph))
        if value is not None
    ]
    if len(present) > 1:
        raise AgentError(
            "loop: provide exactly one of 'ref' (a saved graph id), "
            "'alias' (a built-in loop), or 'graph' (an inline graph document) — "
            f"not {', '.join(repr(name) for name in present)}"
        )
    if len(present) == 0:
        raise AgentError(
            "loop: an agent with no loop can't run a turn — provide "
            "'ref' (a saved graph id), 'alias' (one of: "
            f"{', '.join(KNOWN_LOOP_ALIASES)}), or 'graph' (an inline graph document)"
        )
    if ref is not None:
        return LoopSpec(ref=_validate_id(ref, "loop.ref"), alias=None, graph=None)
    if alias is not None:
        if alias not in KNOWN_LOOP_ALIASES:
            raise AgentError(
                f"loop.alias: must be one of {', '.join(KNOWN_LOOP_ALIASES)}; got {alias!r} "
                "(a saved graph is reached via loop.ref, an inline one via loop.graph)"
            )
        return LoopSpec(ref=None, alias=alias, graph=None)
    # ``graph`` — the stateless v2 path: the inline document is validated with
    # the same closed-vocabulary graph contract as a saved one. GraphError is
    # re-raised as AgentError so this module keeps its single error type
    # (both are ValueErrors, but AgentError keeps the message path-qualified).
    try:
        validate_graph(graph)
    except GraphError as exc:
        raise AgentError(f"loop.graph: {exc}") from exc
    return LoopSpec(ref=None, alias=None, graph=graph)


def _validate_mcp(raw: Any, index: int) -> McpSpec:
    label = f"mcp[{index}]"
    if not isinstance(raw, dict):
        raise AgentError(f"{label}: must be a JSON object; got {type(raw).__name__}")
    _reject_unknown_keys(raw, ("alias", "transport", "command", "url", "headers"), label)
    alias = _validate_id(raw.get("alias"), f"{label}.alias")
    transport = raw.get("transport")
    if transport not in MCP_TRANSPORTS:
        raise AgentError(
            f"{label}.transport: must be one of {', '.join(MCP_TRANSPORTS)}; got {transport!r}"
        )
    command, url, headers = None, None, None
    if transport == "stdio":
        if raw.get("url") is not None:
            raise AgentError(f"{label}: transport 'stdio' takes 'command', not 'url'")
        argv = raw.get("command")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(part, str) and part.strip() for part in argv
        ):
            raise AgentError(
                f"{label}.command: 'stdio' requires a non-empty list of strings; got {argv!r}"
            )
        command = tuple(argv)
    else:
        if raw.get("command") is not None:
            raise AgentError(f"{label}: transport {transport!r} takes 'url', not 'command'")
        url = _validate_nonempty_str(raw.get("url"), f"{label}.url")
    if raw.get("headers") is not None:
        headers_raw = raw["headers"]
        if not isinstance(headers_raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers_raw.items()
        ):
            raise AgentError(f"{label}.headers: must be an object of string -> string; got {headers_raw!r}")
        headers = dict(headers_raw)
    return McpSpec(alias=alias, transport=transport, command=command, url=url, headers=headers)


def _validate_skill(raw: Any, index: int, skill_root: str | None) -> SkillSpec:
    label = f"skill[{index}]"
    if not isinstance(raw, dict):
        raise AgentError(f"{label}: must be a JSON object; got {type(raw).__name__}")
    _reject_unknown_keys(raw, ("path", "text", "tools"), label)
    path_raw, text_raw = raw.get("path"), raw.get("text")
    if (path_raw is None) == (text_raw is None):
        if path_raw is not None and text_raw is not None:
            raise AgentError(
                f"{label}: provide exactly one of 'text' (an inline persona fragment) "
                "or 'path' (a skill file) — not both"
            )
        raise AgentError(
            f"{label}: provide exactly one of 'text' (an inline persona fragment) "
            "or 'path' (a skill file)"
        )
    path = None
    text = None
    if path_raw is not None:
        path = _validate_nonempty_str(path_raw, f"{label}.path")
        if skill_root is not None:
            root = os.path.realpath(skill_root)
            target = os.path.realpath(os.path.join(root, path))
            if not target.startswith(root + os.sep):
                raise AgentError(f"{label}.path: must stay under the skill root; got {path!r}")
            if not os.path.isfile(target):
                raise AgentError(f"{label}.path: no such file {path!r} under the skill root")
            try:
                with open(target, encoding="utf-8") as fh:
                    fh.read()
            except (OSError, UnicodeDecodeError) as exc:
                raise AgentError(f"{label}.path: {path!r} must be a readable text file: {exc}") from exc
    else:
        # Inline fragment (the stateless v2 path): a non-empty persona
        # snippet composed into the turn's leading system message at bind.
        text = _validate_nonempty_str(text_raw, f"{label}.text")
    tools = None
    if raw.get("tools") is not None:
        declared = raw["tools"]
        if not isinstance(declared, list) or not declared or not all(
            isinstance(name, str) and name.strip() for name in declared
        ):
            raise AgentError(
                f"{label}.tools: must be a non-empty list of tool names; got {declared!r}"
            )
        tools = tuple(declared)
    return SkillSpec(path=path, text=text, tools=tools)


def _validate_model(raw: Any) -> ModelSpec:
    if not isinstance(raw, dict):
        raise AgentError(f"model: must be a JSON object; got {type(raw).__name__}")
    _reject_unknown_keys(raw, ("provider", "id", "params"), "model")
    provider = raw.get("provider")
    if provider not in MODEL_PROVIDERS:
        raise AgentError(
            f"model.provider: must be one of {', '.join(MODEL_PROVIDERS)}; got {provider!r}"
        )
    model_id = _validate_nonempty_str(raw.get("id"), "model.id")
    params = None
    if raw.get("params") is not None:
        params_raw = raw["params"]
        if not isinstance(params_raw, dict):
            raise AgentError(f"model.params: must be an object; got {params_raw!r}")
        params = dict(params_raw)
    return ModelSpec(provider=provider, id=model_id, params=params)


def _validate_settings(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise AgentError(f"settings: must be an object of slot -> alias; got {type(raw).__name__}")
    out: dict = {}
    for slot, alias in raw.items():
        if slot not in SLOT_NAMES:
            raise AgentError(
                f"settings: unknown slot {slot!r} (must be one of: {', '.join(SLOT_NAMES)})"
            )
        out[slot] = _validate_nonempty_str(alias, f"settings.{slot}")
    return out


# ── document validator ──────────────────────────────────────────────────────


def validate_agent(
    data,
    *,
    known_graph_ids: set | frozenset | None = None,
    skill_root: str | None = None,
) -> AgentDoc:
    """Validate an agent document (must already be parsed to a dict).

    Raises :class:`AgentError` with a human-readable path on the first
    violation.

    Two seams are injected at the save site instead of imported (this module
    stays pure/stdlib, the frozen rule):

    * ``known_graph_ids`` — when given, ``loop.ref`` must name one of these
      saved turn-graphs (``agent_store`` passes the graph store's ids);
      when omitted (pure mode) only the id *shape* is checked.
    * ``skill_root`` — when given, every ``skill.path`` must exist under it
      as a readable text file; when omitted only the shape is checked.

    ``loop.graph`` (the stateless v2 path) is fully validated here, with the
    sibling ``schemas.graph.validate_graph`` contract — no seam needed, the
    document is self-contained in the request.
    """
    if not isinstance(data, dict):
        raise AgentError("an agent must be a JSON object at the top level")
    _reject_unknown_keys(data, ALLOWED_KEYS, "agent")

    agent_id = None if data.get("id") is None else _validate_id(data["id"], "id")
    name = None if data.get("name") is None else _validate_nonempty_str(data["name"], "name")
    prompt = None if data.get("prompt") is None else _validate_nonempty_str(data["prompt"], "prompt")

    model = None if data.get("model") is None else _validate_model(data["model"])

    mcp_raw = data.get("mcp") or []
    if not isinstance(mcp_raw, list):
        raise AgentError(f"mcp: must be an array of entries; got {type(mcp_raw).__name__}")
    mcp = tuple(_validate_mcp(entry, i) for i, entry in enumerate(mcp_raw))
    aliases = [spec.alias for spec in mcp]
    if len(set(aliases)) != len(aliases):
        dupes = sorted({a for a in aliases if aliases.count(a) > 1})
        raise AgentError(f"mcp: alias must be unique within the document; duplicates: {', '.join(dupes)}")

    skill_raw = data.get("skill") or []
    if not isinstance(skill_raw, list):
        raise AgentError(f"skill: must be an array of entries; got {type(skill_raw).__name__}")
    skill = tuple(_validate_skill(entry, i, skill_root) for i, entry in enumerate(skill_raw))

    settings = {} if data.get("settings") is None else _validate_settings(data["settings"])

    if "loop" not in data or data["loop"] is None:
        raise AgentError(
            "loop: an agent with no loop can't run a turn — 'loop' is required "
            "(one of {ref: <saved graph id>}, {alias: <loop alias>}, "
            "{graph: <inline graph document>})"
        )
    loop = _validate_loop(data["loop"])
    if loop.ref is not None and known_graph_ids is not None and loop.ref not in known_graph_ids:
        known = ", ".join(sorted(known_graph_ids)) or "(none saved)"
        raise AgentError(f"loop.ref: {loop.ref!r} is not a saved graph (known: {known})")

    return AgentDoc(
        id=agent_id,
        name=name,
        prompt=prompt,
        model=model,
        mcp=mcp,
        skill=skill,
        settings=settings,
        loop=loop,
    )


def parse_agent(source, **kwargs):
    """Parse an agent (JSON text, file path, or dict) and validate it.

    Parsed exactly like graphs today: a dict is used as-is, a string that
    looks like JSON is decoded, anything else is treated as a file path.
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, str):
        text = source.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise AgentError(f"agent JSON is invalid: {exc}") from exc
        else:
            try:
                with open(source) as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                raise AgentError(f"Cannot load agent document {source!r}: {exc}") from exc
    else:
        raise AgentError(
            f"an agent must be a dict, a JSON string, or a file path; got {type(source).__name__}"
        )
    return validate_agent(data, **kwargs)

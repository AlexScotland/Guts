"""``draft(goal, notes, current) -> {doc, verdict}`` — the stateless design core.

The whole contract in one sentence: natural language in, a proposed graph
document out, and the verdict IS the backend ``validate_graph`` — nothing
here is a second opinion.

Loop-blind by construction (the invariant tests pin): ``draft`` contains no
loop-count logic, no stop-condition logic, no knowledge of what a loop is
for. A repeat count ("loop twice") or a stop condition ("until the ask
feels complete") is BUSINESS-CASE material: it rides in the model's choice
of ``max_passes`` / node config driven by the notes, and only the notes
carry it. So "make it loop twice" works with zero code change in this
module — the prompt merely tells the model that notes are a business case.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from schemas.graph import GraphError, validate_graph

from . import catalog

#: The design model (inference side). Injected for tests; the module-level
#: ``get_completer`` is the seam both the HTTP face and the MCP face patch.
Completer = object   # anything with .invoke(messages) -> message/content
_default_completer: Completer | None = None


class DraftError(ValueError):
    """The design model could not be reached or did not answer with a document."""


@dataclass
class DraftResult:
    doc: dict
    verdict: dict          # {"ok": bool, "issues": [str, ...]}


# ── prompt ────────────────────────────────────────────────────────────────────

def _primitive_lines() -> list[str]:
    lines = []
    for p in catalog.list_primitives():
        ports = []
        if p["requires"]:
            ports.append("requires " + "+".join(p["requires"]))
        if p["requires_any"]:
            ports.append("requires one of [" + " / ".join(" or ".join(g) for g in p["requires_any"]) + "]")
        if p["accepts"]:
            ports.append("accepts " + ", ".join(p["accepts"]))
        ports.append("provides " + ", ".join(p["provides"]))
        flags = []
        if p["budgeted"]:
            flags.append("budgeted")
        if p["side_effects"]:
            flags.append("side-effects")
        if flags:
            ports.append("(" + ", ".join(flags) + ")")
        lines.append(f"- {p['primitive']}: {p['description']} [in/out: " + "; ".join(ports) + "]")
    return lines


def render_prompt(goal: str, notes: str | None = None, current: dict | None = None) -> str:
    """Deterministic prompt: frozen language + rules + the user's business case.

    Pure function of its inputs — same goal/notes/current renders the same
    text, so the core has no clocks, no session state, no second pass.
    """
    parts = [
        "You are the design engine for GUTS graph loops. Return ONLY one JSON object — a valid graph document in the loop.graph vocabulary below. No prose, no code fences.",
        "",
        "PRIMITIVES (closed set — use only these):",
        *_primitive_lines(),
        "",
        "RULES (the document you emit must satisfy every one):",
    ]
    parts += [f"{i}. {r}" for i, r in enumerate(catalog.RULES, 1)]
    parts += [
        "",
        "THE GOAL (what the loop is for, in the user's words):",
        goal.strip(),
    ]
    if notes and notes.strip():
        parts += [
            "",
            "NOTES (the user's business case — express it in the document: repeat counts become max_passes, stop conditions become the critic's criterion, scope becomes node config):",
            notes.strip(),
        ]
    if current:
        parts += [
            "",
            "CURRENT DOCUMENT (revise it — keep what works, change what the goal/notes ask for):",
            json.dumps(current, indent=2, sort_keys=True),
        ]
    parts += ["", "Return only the JSON graph document."]
    return "\n".join(parts)


# ── model I/O (injected seam) ─────────────────────────────────────────────────

def get_completer() -> Completer:
    """Production completer: the repo's Ollama provider (inference/ollama.py).

    Lazily imported so tests and offline callers never touch the runtime.
    """
    global _default_completer
    if _default_completer is None:
        from inference.config import DEFAULT_MODEL
        from inference.ollama import OllamaProvider

        _default_completer = OllamaProvider(model=DEFAULT_MODEL).llm
    return _default_completer


def set_completer(completer: Completer | None) -> None:
    """Explicit seam for the HTTP/MCP faces and tests (None = back to default)."""
    global _default_completer
    _default_completer = completer


def _content_of(reply) -> str:
    """Message -> text (ChatOllama returns AIMessage; fakes may return str)."""
    content = getattr(reply, "content", reply)
    if isinstance(content, list):          # multimodal parts
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


_FENCE_RE = re.compile(r"```(?:json|javascript)?\s*(\{.*?\})\s*```", re.S)


def _extract_json(text: str) -> dict | None:
    """Best-effort document extraction: fence block, whole text, first
    balanced brace block. Returns a dict, else None (caller decides retry)."""
    s = text.strip()
    candidates: list[str] = []
    m = _FENCE_RE.search(s)
    if m:
        candidates.append(m.group(1))
    candidates.append(s)
    start = s.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(s[start:i + 1])
                    break
    for candidate in candidates:
        try:
            doc = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return None


def _reply_text(completer: Completer, messages: list[dict]) -> str:
    try:
        reply = completer.invoke(messages)
    except Exception as exc:                          # provider transport (httpx/ollama) failures
        raise DraftError(f"the model call failed: {exc}") from exc
    return _content_of(reply)


def _invoke_and_parse(completer: Completer, goal: str, notes: str | None, current: dict | None) -> dict:
    prompt = render_prompt(goal, notes=notes, current=current)
    messages = [{"role": "user", "content": prompt}]
    text = _reply_text(completer, messages)
    doc = _extract_json(text)
    if doc is not None:
        return doc
    # One repair pass — the model gets its own failure, not ours.
    messages += [
        {"role": "assistant", "content": text},
        {"role": "user", "content": (
            "That is not a graph document. Return ONLY the one JSON object, "
            "no prose and no code fences."
        )},
    ]
    doc = _extract_json(_reply_text(completer, messages))
    if doc is None:
        raise DraftError("the design model did not return a graph document")
    return doc


def _verdict(doc: dict) -> dict:
    """The ONLY green door: backend validate_graph. A red verdict is a
    normal outcome — the doc is still returned, verbatim, for the caller."""
    try:
        validate_graph(doc)
    except GraphError as exc:
        return {"ok": False, "issues": [str(exc)]}
    return {"ok": True, "issues": []}


# ── the core ──────────────────────────────────────────────────────────────────

def draft(goal: str, notes: str | None = None, current: dict | None = None,
          *, completer: Completer | None = None) -> DraftResult:
    """Propose a graph document for ``goal``.

    Stateless: no memory of prior drafts, no loop counting, no refinement —
    the caller (HTTP face, MCP face, chat face) owns refinement. ``verdict``
    is exactly ``validate_graph(doc)``; a red verdict is a result, not an
    error. Raises only ``DraftError`` (model unreachable/unparseable) — an
    invalid document NEVER raises.
    """
    if not goal or not str(goal).strip():
        raise DraftError("goal is required")
    if notes is not None and not isinstance(notes, str):
        raise DraftError("notes must be a string when given")
    if current is not None and not isinstance(current, dict):
        raise DraftError("current must be a document (object) when given")

    engine = completer if completer is not None else get_completer()
    doc = _invoke_and_parse(engine, str(goal), notes, current)
    return DraftResult(doc=doc, verdict=_verdict(doc))

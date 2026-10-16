"""The agent store: JSON files are the store (docs/agent-graph.md).

Mirrors ``components/graph_store.py`` — layout (one directory, default
``app/agents/``):

  <id>.json    — an agent document (validated before it may be saved)
  _meta.json   — the active agent id (restart-safe)

Two seams are injected at construction instead of imported (the schema
stays pure/stdlib, the frozen rule):

  * ``graph_ids`` — a callable returning the saved turn-graph ids, so
    ``loop.ref`` is resolution-checked at save time (unknown ref = 400,
    like an invalid graph never becomes a saved file);
  * ``skill_root`` — the directory every ``skill.path`` must live under
    as a readable text file (default: the ``app/`` root).

Unlike the graph store there is NO run tracking: an agent binds-then-runs
the EXISTING loop, so last-run evidence stays on the graph side (the loop
is where runs happen). The store's job is the document family: save,
fetch, list, activate, deactivate (clear_active) — exactly the graph
store's shape.
"""
import json
import os
import re
import threading

from schemas.agent import AgentError, validate_agent

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_META_FILE = "_meta.json"


def _app_root() -> str:
    # components/agent_store.py -> app/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_directory() -> str:
    return os.path.join(_app_root(), "agents")


def _default_skill_root() -> str:
    # skill paths are stored relative to the app/ root (the doc's example:
    # "skills/citation.md" ⇒ app/skills/citation.md).
    return _app_root()


class AgentStore:
    """In-memory index over a directory of validated agent documents."""

    def __init__(
        self,
        directory: str | None = None,
        graph_ids=None,
        skill_root: str | None = None,
    ):
        self.directory = os.path.abspath(directory or _default_directory())
        self._lock = threading.RLock()
        self._graph_ids = graph_ids if graph_ids is not None else (lambda: ())
        self._skill_root = os.path.abspath(skill_root or _default_skill_root())
        os.makedirs(self.directory, exist_ok=True)
        self._documents: dict[str, dict] = {}
        self._active_id: str | None = None
        self._load()

    # ── internals ──────────────────────────────────────────────

    def _path(self, agent_id: str) -> str:
        return os.path.join(self.directory, f"{agent_id}.json")

    def _known_graph_ids(self):
        try:
            return set(self._graph_ids())
        except Exception:
            return None  # seam unavailable → shape-only validation (pure mode)

    def _validate(self, doc: dict):
        return validate_agent(
            doc,
            known_graph_ids=self._known_graph_ids(),
            skill_root=self._skill_root,
        )

    def _load(self):
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            names = []
        for fname in names:
            if not fname.endswith(".json") or fname == _META_FILE:
                continue
            agent_id = fname[: -len(".json")]
            path = os.path.join(self.directory, fname)
            try:
                with open(path) as fh:
                    doc = json.load(fh)
                self._validate(doc)  # refuse to index a document that cannot bind
            except Exception:
                continue  # corrupt/unknown files stay inert on disk
            self._documents[agent_id] = doc
        meta_path = os.path.join(self.directory, _META_FILE)
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as fh:
                    meta = json.load(fh)
                active = meta.get("active")
                if isinstance(active, str) and active in self._documents:
                    self._active_id = active
            except (OSError, json.JSONDecodeError):
                pass

    def _persist_active(self):
        meta_path = os.path.join(self.directory, _META_FILE)
        try:
            with open(meta_path, "w") as fh:
                json.dump({"active": self._active_id}, fh)
        except OSError:
            pass  # active id is in-memory first; persistence best-effort

    # ── CRUD (all validate at save time) ───────────────────────

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._documents)

    def has(self, agent_id: str) -> bool:
        with self._lock:
            return agent_id in self._documents

    def get(self, agent_id: str) -> dict:
        with self._lock:
            if agent_id not in self._documents:
                raise AgentError(
                    f"unknown agent {agent_id!r} (have: {', '.join(self._documents) or 'none'})"
                )
            return self._documents[agent_id]

    def save(self, agent_id: str, doc: dict, name: str | None = None) -> dict:
        if not isinstance(agent_id, str) or not _ID_RE.match(agent_id):
            raise AgentError(f"agent id must be a [A-Za-z0-9_-] token; got {agent_id!r}")
        if not isinstance(doc, dict):
            raise AgentError("an agent document must be a JSON object")
        if doc.get("id") is not None and doc["id"] != agent_id:
            raise AgentError(
                f"document id {doc['id']!r} does not match the saved id {agent_id!r} — "
                "one id per agent"
            )
        self._validate(doc)  # raises AgentError → 400 at the API edge
        if name is not None:
            doc = {**doc, "name": str(name)}
        with self._lock:
            path = self._path(agent_id)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(doc, fh, indent=2, sort_keys=False)
            os.replace(tmp, path)
            self._documents[agent_id] = doc
        return self.list_meta()[self.ids().index(agent_id)]

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._documents:
                return False
            try:
                os.remove(self._path(agent_id))
            except OSError:
                pass
            self._documents.pop(agent_id, None)
            if self._active_id == agent_id:
                self._active_id = None
                self._persist_active()
            return True

    # ── activation ───────────────────────────────────────────────

    @property
    def skill_root(self) -> str:
        """The directory every ``skill.path`` resolves under (the bind path
        reads skill text relative to it — same root the save-time validator
        used, so the two doors agree)."""
        return self._skill_root

    @property
    def active_id(self) -> str | None:
        with self._lock:
            return self._active_id

    def set_active(self, agent_id: str):
        with self._lock:
            if agent_id not in self._documents:
                raise AgentError(f"cannot activate unknown agent {agent_id!r}")
            self._active_id = agent_id
            self._persist_active()

    def clear_active(self) -> bool:
        """Deactivate WITHOUT deleting (the deactivate door): the active
        pointer goes away, the document stays saved. Returns True when an id
        WAS active (the caller then rolls the bound state back); False is a
        no-op (nothing was active)."""
        with self._lock:
            if self._active_id is None:
                return False
            self._active_id = None
            self._persist_active()
            return True

    # ── introspection ────────────────────────────────────────────

    def list_meta(self) -> list[dict]:
        """One line per agent: identity + the capability bundle's shape
        (what the discovery door shows: model? mcp aliases? skills? which
        loop it binds to) + active flag."""
        with self._lock:
            out = []
            for aid in sorted(self._documents):
                doc = self._documents[aid]
                try:
                    mtime = os.path.getmtime(self._path(aid))
                except OSError:
                    mtime = None
                loop = doc.get("loop") or {}
                out.append(
                    {
                        "id": aid,
                        "name": doc.get("name") or aid,
                        "loop": loop.get("ref") or loop.get("alias"),
                        "model": (doc.get("model") or {}).get("id"),
                        "mcp": [e.get("alias") for e in (doc.get("mcp") or [])],
                        "skills": [e.get("path") for e in (doc.get("skill") or [])],
                        "has_prompt": bool(doc.get("prompt")),
                        "settings": dict(doc.get("settings") or {}),
                        "active": aid == self._active_id,
                        "updated_at": mtime,
                    }
                )
            return out


# ── process-shared default (server + bind path share ONE store per process) ──

_default_store: AgentStore | None = None
_default_lock = threading.Lock()


def get_agent_store(directory: str | None = None, graph_ids=None) -> AgentStore:
    """The process-shared AgentStore (or a fresh one bound to ``directory``).

    By default ``loop.ref`` resolution checks run against the real graph
    store's ids, so the two stores' save-time doors line up."""
    if directory is not None:
        return AgentStore(directory=directory, graph_ids=graph_ids)
    global _default_store
    with _default_lock:
        if _default_store is None:
            if graph_ids is None:
                from components.graph_store import get_graph_store

                graph_ids = lambda: set(get_graph_store().ids())
            _default_store = AgentStore(graph_ids=graph_ids)
        return _default_store


def reset_agent_store():
    """Test helper: drop the process-shared default store (mirrors
    graph_store.reset_graph_store)."""
    global _default_store
    with _default_lock:
        _default_store = None


def describe_agents(store: AgentStore) -> dict:
    """The ``agents`` block of registry.describe() / the /api/agents family:
    one shape for GET/POST/DELETE/activate, like describe_graphs."""
    return {"active": store.active_id, "agents": store.list_meta()}

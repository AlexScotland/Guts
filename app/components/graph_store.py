"""The graph store: JSON files are the store (v0).

Layout (one directory, default ``app/graphs/``):
  <id>.json    — a graph document (validated before it may be saved)
  _meta.json   — the active graph id (restart-safe)

Last-run records (answer + node events — the API + future-canvas seam) are
kept in memory per store instance; documents and the active id persist as
files, which is what "graphs as JSON files" (the frozen v0 leaning) means.

Live-run records: an in-progress run is mirrored per event (status "running"
+ events so far) so the canvas seam (GET /api/graphs/{id}/last-run) shows
the CURRENT node in flight, not just the finished run. Same in-memory
posture as last-run; cleared when the run ends (either way).
"""
import json
import os
import re
import threading
import time

from schemas.graph import GraphError, validate_graph

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_META_FILE = "_meta.json"

# Defensive bound on a single live run's mirrored event log (each event is
# small; a pathological graph should not grow an unbounded per-graph blob).
LIVE_EVENT_CAP = 500


def _default_directory() -> str:
    # components/graph_store.py -> app/ -> app/graphs/
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graphs")


class GraphStore:
    """In-memory index over a directory of validated graph documents."""

    def __init__(self, directory: str | None = None):
        self.directory = os.path.abspath(directory or _default_directory())
        self._lock = threading.RLock()
        os.makedirs(self.directory, exist_ok=True)
        self._documents: dict[str, dict] = {}
        self._runs: dict[str, dict] = {}
        self._live: dict[str, dict] = {}
        self._active_id: str | None = None
        self._load()

    # ── internals ──────────────────────────────────────────────

    def _path(self, graph_id: str) -> str:
        return os.path.join(self.directory, f"{graph_id}.json")

    def _load(self):
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            names = []
        for fname in names:
            if not fname.endswith(".json") or fname == _META_FILE:
                continue
            graph_id = fname[: -len(".json")]
            path = os.path.join(self.directory, fname)
            try:
                with open(path) as fh:
                    doc = json.load(fh)
                validate_graph(doc)  # refuse to index a document that cannot run
            except Exception:
                continue  # corrupt/unknown files stay inert on disk
            self._documents[graph_id] = doc
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

    # ── CRUD (all validate at save time) ─────────────────────────

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._documents)

    def has(self, graph_id: str) -> bool:
        with self._lock:
            return graph_id in self._documents

    def get(self, graph_id: str) -> dict:
        with self._lock:
            if graph_id not in self._documents:
                raise GraphError(f"unknown graph {graph_id!r} (have: {', '.join(self._documents) or 'none'})")
            return self._documents[graph_id]

    def save(self, graph_id: str, doc: dict, name: str | None = None) -> dict:
        if not isinstance(graph_id, str) or not _ID_RE.match(graph_id):
            raise GraphError(f"graph id must be a [A-Za-z0-9_-] token; got {graph_id!r}")
        if not isinstance(doc, dict):
            raise GraphError("a graph document must be a JSON object")
        validate_graph(doc)  # raises GraphError → 400 at the API edge
        if name is not None:
            doc = {**doc, "name": str(name)}
        with self._lock:
            path = self._path(graph_id)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(doc, fh, indent=2, sort_keys=False)
            os.replace(tmp, path)
            self._documents[graph_id] = doc
        return self.list_meta()[self.ids().index(graph_id)]

    def delete(self, graph_id: str) -> bool:
        with self._lock:
            if graph_id not in self._documents:
                return False
            path = self._path(graph_id)
            try:
                os.remove(path)
            except OSError:
                pass
            self._documents.pop(graph_id, None)
            self._runs.pop(graph_id, None)
            self._live.pop(graph_id, None)
            if self._active_id == graph_id:
                self._active_id = None
                self._persist_active()
            return True

    # ── activation ───────────────────────────────────────────────

    @property
    def active_id(self) -> str | None:
        with self._lock:
            return self._active_id

    def set_active(self, graph_id: str):
        with self._lock:
            if graph_id not in self._documents:
                raise GraphError(f"cannot activate unknown graph {graph_id!r}")
            self._active_id = graph_id
            self._persist_active()

    # ── introspection ────────────────────────────────────────────

    def list_meta(self) -> list[dict]:
        with self._lock:
            out = []
            for gid in sorted(self._documents):
                doc = self._documents[gid]
                try:
                    mtime = os.path.getmtime(self._path(gid))
                except OSError:
                    mtime = None
                out.append(
                    {
                        "id": gid,
                        "name": doc.get("name") or gid,
                        "version": str(doc.get("version", "1.0")),
                        "node_count": len(doc.get("nodes") or {}),
                        "active": gid == self._active_id,
                        "updated_at": mtime,
                    }
                )
            return out

    def record_run(self, graph_id: str, record: dict):
        with self._lock:
            stamped = {"at": round(time.time(), 3), **record}
            self._runs[graph_id] = {
                "status": stamped.get("status", "ok"),
                "at": stamped["at"],
                "answer": stamped.get("answer"),
                "error": stamped.get("error"),
                "events": stamped.get("events", []),
            }

    def last_run(self, graph_id: str) -> dict | None:
        with self._lock:
            return self._runs.get(graph_id)

    # ── live run (in flight: status "running" + events so far) ─────────

    def begin_run(self, graph_id: str) -> dict:
        """Open the live record for ``graph_id`` (a new run supersedes any
        prior live record for the same graph). Returns the live record.

        Shape mirrors the finished record minus the not-yet-there parts
        (answer/error are explicit nulls): one client type both sides."""
        with self._lock:
            live = {
                "status": "running",
                "at": round(time.time(), 3),
                "answer": None,
                "error": None,
                "events": [],
            }
            self._live[graph_id] = live
            return live

    def append_event(self, graph_id: str, record: dict):
        """Mirror one engine event into the live record (no-op when the run
        has ended or was never begun — e.g. a run started via the REPL)."""
        with self._lock:
            live = self._live.get(graph_id)
            if live is None:
                return
            events = live["events"]
            if len(events) >= LIVE_EVENT_CAP:
                return
            events.append(dict(record))

    def clear_live(self, graph_id: str):
        with self._lock:
            self._live.pop(graph_id, None)

    def live_run(self, graph_id: str) -> dict | None:
        """The in-flight record (``status:"running"`` + events so far) or
        ``None`` when no run is in flight. A copy: callers may mutate."""
        with self._lock:
            live = self._live.get(graph_id)
            if live is None:
                return None
            return {
                "status": live["status"],
                "at": live["at"],
                "answer": None,
                "error": None,
                "events": list(live["events"]),
            }


# ── process-shared default (server + loop share ONE store per process) ──────

_default_store: GraphStore | None = None
_default_lock = threading.Lock()


def get_graph_store(directory: str | None = None) -> GraphStore:
    """The process-shared GraphStore (or a fresh one bound to ``directory``)."""
    if directory is not None:
        return GraphStore(directory)
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = GraphStore()
        return _default_store


def reset_graph_store():
    """Test helper: drop the process-shared default store."""
    global _default_store
    with _default_lock:
        _default_store = None


def describe_graphs(store: GraphStore) -> dict:
    """The ``graphs`` block of registry.describe() / GET /api/components."""
    return {"active": store.active_id, "graphs": store.list_meta()}

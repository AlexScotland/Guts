"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Header from "./components/Header";
import Toolbar from "./components/Toolbar";
import Canvas, { type Selection } from "./components/Canvas";
import AgentLibrary from "./components/AgentLibrary";
import GraphLibrary from "./components/GraphLibrary";
import Palette from "./components/Palette";
import Inspector from "./components/Inspector";
import TestPanel from "./components/TestPanel";
import { useTheme } from "./hooks/useTheme";
import {
  activate,
  activateAgent,
  ApiError,
  deleteAgent,
  deleteGraph,
  getComponents,
  getGraph,
  getAgent,
  getAgents,
  getGraphs,
  getLastRun,
  isBackendOnline,
  postChat,
  saveAgent,
  saveGraph,
  testAgentMcp,
} from "./lib/api";
import {
  deriveNodeOutputs,
  deriveNodeSkipDetails,
  deriveNodeStates,
  type NodeRunState,
} from "./lib/run";
import {
  FALLBACK_PRIMITIVES,
  fromDoc,
  toDoc,
  checkCanvas,
  type CanvasEdge,
  type CanvasGraph,
  type CanvasNode,
  type PrimitiveMeta,
} from "./lib/graph";
import {
  docToDraft,
  draftToDoc,
  isValidAgentId,
  newAgentDraft,
  newAgentId,
  type AgentDraft,
  type CanvasAgent,
} from "./lib/agentNode";
import type {
  AgentMeta,
  BranchSpec,
  GraphMeta,
  LastRun,
  PrimitiveInfo,
} from "./lib/types";
import styles from "./page.module.css";

function primitivesFromApi(api: PrimitiveInfo[]): PrimitiveMeta[] {
  return api.map((p) => ({
    primitive: p.name,
    description: p.description ?? "",
    requires: (p.requires ?? []) as PrimitiveMeta["requires"],
    requires_any: (p.requires_any ?? []) as PrimitiveMeta["requires_any"],
    accepts: (p.accepts ?? []) as PrimitiveMeta["accepts"],
    provides: (p.provides ?? []) as PrimitiveMeta["provides"],
    side_effects: !!p.side_effects,
  }));
}

/** A sensible first node so the canvas is never a blank void. */
function seedGraph(id: string): CanvasGraph {
  return {
    id,
    name: id,
    budget: null,
    max_parallel: null,
    nodes: [
      {
        id: "research",
        primitive: "research",
        x: 60,
        y: 80,
        config: {},
        budget: 40,
        branches: [],
      },
      {
        id: "answer",
        primitive: "act",
        x: 400,
        y: 120,
        config: { task: "Answer the goal using the gathered context. Cite the strongest sources inline." },
        budget: 40,
        branches: [],
      },
    ],
    edges: [
      { from: "research", to: "answer", type: "data" },
    ],
  };
}

export default function Page() {
  const { theme, toggle } = useTheme();
  const [online, setOnline] = useState<boolean | null>(null);
  const [primitives, setPrimitives] = useState<PrimitiveMeta[]>(FALLBACK_PRIMITIVES);
  const [apiLive, setApiLive] = useState(false);

  const [graphs, setGraphs] = useState<GraphMeta[] | null>(null);
  const [activeGraph, setActiveGraph] = useState<string | null>(null);
  const [activeLoop, setActiveLoop] = useState<string | null>(null);

  // The layer above (docs/agent-graph.md): saved capability-composition docs.
  const [agents, setAgents] = useState<AgentMeta[] | null>(null);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);

  // The canvas AGENT NODES (docs/agent-graph.md — "The agent node on the
  // canvas"): each is a DRAFT of one agent document (a second document
  // family — never serialized into the turn-graph doc). `agentBaselines`
  // holds the last SAVED serialization per id (the dirty diff); a fresh
  // draft's baseline is its own serialization, so it starts clean.
  const [canvasAgents, setCanvasAgents] = useState<CanvasAgent[]>([]);
  const [agentBaselines, setAgentBaselines] = useState<Record<string, string>>({});
  /** The in-flight agent action per node ("saving…" / "testing web…" / …). */
  const [agentBusy, setAgentBusy] = useState<{ id: string; label: string } | null>(null);

  const clearAgentBusy = useCallback(
    (id: string) => setAgentBusy((cur) => (cur && cur.id === id ? null : cur)),
    []
  );

  const [graph, setGraph] = useState<CanvasGraph>(() => seedGraph("my-loop"));
  const [baseline, setBaseline] = useState<string>(() =>
    JSON.stringify(toDoc(seedGraph("my-loop")))
  );

  const [selection, setSelection] = useState<Selection | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);

  // Live run view (the canvas seam): armed while a run is in flight; the
  // poller keeps runRecord fresh (status "running" + events so far) so the
  // current node can be highlighted on the canvas. After the run ends, the
  // finished record (ok/failed) stays here — the finished states remain
  // visible until the next run or a different graph is loaded.
  const [runActive, setRunActive] = useState(false);
  const [runRecord, setRunRecord] = useState<LastRun | null>(null);
  /** When the current run armed (client ms) — the poller must not apply a
   *  finished record that predates it (the PREVIOUS run, seen in the
   *  save→activate window before this run's live record exists). */
  const [armTs, setArmTs] = useState(0);

  const resetRunView = useCallback(() => {
    setRunActive(false);
    setRunRecord(null);
  }, []);

  const meta = useMemo(
    () => new Map(primitives.map((p) => [p.primitive, p])),
    [primitives]
  );

  const dirty = useMemo(
    () => JSON.stringify(toDoc(graph)) !== baseline,
    [graph, baseline]
  );

  const issues = useMemo(() => checkCanvas(graph, meta), [graph, meta]);

  // Per-agent-node unsaved flag: the draft's CURRENT serialization vs the
  // last saved one (the round-trip test pins that a saved doc loads back to
  // the identical serialization, so the diff is honest).
  const agentDirty = useMemo(() => {
    const d: Record<string, boolean> = {};
    for (const a of canvasAgents) {
      d[a.id] = JSON.stringify(draftToDoc(a.draft)) !== (agentBaselines[a.id] ?? "");
    }
    return d;
  }, [canvasAgents, agentBaselines]);

  /** Saved turn-graph ids — the agent loop slot's `ref` options. */
  const savedGraphIds = useMemo(() => (graphs ?? []).map((g) => g.id), [graphs]);

  const docJson = useMemo(() => JSON.stringify(toDoc(graph), null, 2), [graph]);

  // Per-node run states for the canvas (the live record while a run is in
  // flight; the finished record afterwards — the same shapes, one seam).
  const nodeStates = useMemo(
    () => (runRecord && runRecord.events.length > 0 ? deriveNodeStates(runRecord.events) : null),
    [runRecord]
  );

  // Per-node last output for click-to-read (docs/ai-per-node-outputs.md),
  // folded from the SAME record the canvas states come from.
  const nodeOutputs = useMemo(
    () => (runRecord && runRecord.events.length > 0 ? deriveNodeOutputs(runRecord.events) : null),
    [runRecord]
  );

  // Skip REASONS per node (v1 routing: "not routed" / "upstream pruned") —
  // the pruned card styling carries its WHY (docs/ai-routing-nodes.md).
  const skipDetails = useMemo(
    () => (runRecord && runRecord.events.length > 0 ? deriveNodeSkipDetails(runRecord.events) : null),
    [runRecord]
  );

  const notify = useCallback((ok: boolean, msg: string) => {
    setToast({ ok, msg });
    const t = setTimeout(() => setToast(null), 5200);
    return () => clearTimeout(t);
  }, []);

  // ── discovery doors (primitives / graphs / status) ────────────────────────

  const refreshDoors = useCallback(async () => {
    const live = await isBackendOnline();
    setOnline(live);
    if (!live) {
      setApiLive(false);
      setPrimitives(FALLBACK_PRIMITIVES);
      setGraphs(null);
      setActiveGraph(null);
      setActiveLoop(null);
      return;
    }
    try {
      const comps = await getComponents();
      if (comps.primitives?.length) {
        setPrimitives(primitivesFromApi(comps.primitives));
        setApiLive(true);
      }
      setActiveLoop(comps.slots?.loop?.active ?? null);
      if (comps.graphs) {
        setGraphs(comps.graphs.graphs);
        setActiveGraph(comps.graphs.active);
      }
    } catch {
      /* offline path already handled */
    }
    try {
      const g = await getGraphs();
      setGraphs(g.graphs);
      setActiveGraph(g.active);
    } catch {
      /* keep whatever we have */
    }
    try {
      const a = await getAgents();
      setAgents(a.agents);
      setActiveAgent(a.active);
    } catch {
      /* pre-agent backend — the panel shows its offline/empty state */
    }
  }, []);

  useEffect(() => {
    void refreshDoors();
    const iv = setInterval(() => void refreshDoors(), 5000);
    return () => clearInterval(iv);
  }, [refreshDoors]);

  // While a run is in flight, poll the canvas seam (300ms — fast enough to
  // catch short nodes, cheap on a localhost backend) for the live record —
  // the currently executing node(s). 404s are silent: a pre-live backend
  // (a server started before the live seam landed — a restart loads it)
  // has no in-flight record, and the final read (after the run resolves)
  // is what onRun always falls back to.
  useEffect(() => {
    if (!runActive) return;
    let cancelled = false;
    // Apply polled records MONOTONICALLY — a slower (older) response must
    // never replace a newer one, and the previous run's records must not
    // flash on the canvas in the save→activate window:
    //  - live → live: always (events grow);
    //  - live → finished, or finished → (newer) finished: only if strictly
    //    newer (a finished run's `at` is always later than its live `at`);
    //  - a settled record predating the run's arm is the PREVIOUS run —
    //    wait for this run's live record (1s tolerance for clock skew);
    //  - a live record never supersedes a newer finished one (a late
    //    in-flight poll arriving after the run settled).
    const apply = (rec: LastRun) => {
      const now = rec.at * 1000;
      setRunRecord((prev) => {
        if (prev === null) {
          if (rec.status === "running") return rec;
          return now >= armTs - 1000 ? rec : prev;
        }
        if (prev.status === "running" && rec.status === "running") return rec;
        if (prev.status !== "running" && rec.status === "running")
          return rec.at > prev.at ? rec : prev;
        return rec.at > prev.at ? rec : prev;
      });
    };
    const poll = async () => {
      try {
        const rec = await getLastRun(graph.id);
        if (!cancelled) apply(rec);
      } catch (e) {
        // Only 404 (nothing in flight on an old backend) is expected;
        // everything else is a transient network blip — keep the last
        // good value rather than flashing the canvas.
        if (e instanceof ApiError && e.kind === "http" && e.status === 404) {
          /* wait quietly */
        }
      }
    };
    void poll();
    const iv = setInterval(() => void poll(), 300);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [runActive, graph.id, armTs]);

  // ── graph mutations (the working copy) ────────────────────────────────────

  const patchGraph = useCallback(
    (patch: Partial<Pick<CanvasGraph, "name" | "budget" | "max_parallel">>) =>
      setGraph((g) => ({ ...g, ...patch })),
    []
  );

  const patchNode = useCallback(
    (id: string, patch: Partial<CanvasNode>) =>
      setGraph((g) => {
        let edges = g.edges;
        if (patch.id && patch.id !== id) {
          edges = g.edges.map((e) => ({
            ...e,
            from: e.from === id ? patch.id! : e.from,
            to: e.to === id ? patch.id! : e.to,
          }));
          // A rename is a patch, not a close: keep the selection (and the
          // on-node inspector popover) tracking the node under its NEW id.
          // Without this, `selected` (old id) no longer matches and the
          // popover dismisses mid-edit while the user is typing in the ID
          // field.
          setSelection((sel) =>
            sel?.kind === "node" && sel.id === id
              ? { kind: "node", id: patch.id! }
              : sel
          );
        }
        return {
          ...g,
          edges,
          nodes: g.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)),
        };
      }),
    []
  );

  const patchBranches = useCallback(
    (id: string, branches: BranchSpec[]) =>
      setGraph((g) => ({
        ...g,
        nodes: g.nodes.map((n) => (n.id === id ? { ...n, branches } : n)),
      })),
    []
  );

  const patchEdge = useCallback(
    (edge: CanvasEdge, patch: Partial<CanvasEdge>) =>
      setGraph((g) => ({
        ...g,
        edges: g.edges.map((e) =>
          e.from === edge.from &&
          e.to === edge.to &&
          e.type === edge.type
            ? { ...e, ...patch }
            : e
        ),
      })),
    []
  );

  const addNode = useCallback(
    (primitive: string) => {
      let created: string | null = null;
      setGraph((g) => {
        const count = g.nodes.length;
        let id = `${primitive}_${count + 1}`;
        const taken = new Set(g.nodes.map((n) => n.id));
        while (taken.has(id)) id = `${id}x`;
        created = id;
        const node: CanvasNode = {
          id,
          primitive,
          x: 80 + (count % 5) * 40,
          y: 80 + (count % 5) * 44,
          config:
            primitive === "prompt"
              ? { text: "Injected focus: be concrete and cite what you rely on." }
              : {},
          budget: null,
          branches: [],
        };
        return { ...g, nodes: [...g.nodes, node] };
      });
      if (created) setSelection({ kind: "node", id: created });
    },
    []
  );

  const removeNode = useCallback((id: string) => {
    setGraph((g) => ({
      ...g,
      nodes: g.nodes.filter((n) => n.id !== id),
      edges: g.edges.filter((e) => e.from !== id && e.to !== id),
    }));
    setSelection((sel) => (sel?.kind === "node" && sel.id === id ? null : sel));
  }, []);

  const moveNode = useCallback(
    (id: string, x: number, y: number) =>
      setGraph((g) => ({
        ...g,
        nodes: g.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)),
      })),
    []
  );

  const addEdge = useCallback((from: string, to: string, type: "data" | "control") => {
    setGraph((g) => {
      if (from === to) return g;
      if (g.edges.some((e) => e.from === from && e.to === to && e.type === type)) return g;
      return {
        ...g,
        edges: [
          ...g.edges,
          type === "control" ? { from, to, type, max_passes: 2 } : { from, to, type },
        ],
      };
    });
  }, []);

  const removeEdge = useCallback(
    (key: string) => {
      setGraph((g) => ({
        ...g,
        edges: g.edges.filter((e) => `${e.from}->${e.to}:${e.type}` !== key),
      }));
      setSelection((sel) => (sel?.kind === "edge" && sel.key === key ? null : sel));
    },
    []
  );

  // ── the agent layer (docs/agent-graph.md — a SECOND document family) ─────
  // The canvas agent nodes are DRAFTS of agent documents. Nothing here
  // touches the turn-graph doc; Save/Activate/Delete/Test-MCP are the REST
  // door, where app/schemas/agent.py stays the authority (400 = honest
  // failure), and the fakes-only oracle on the backend stays untouched.

  /** A fresh agent node on the canvas: a minimal VALID draft (the alias
   *  loop, nothing invented), positioned right of the turn-graph column. */
  const addAgentNode = useCallback(() => {
    const existing = canvasAgents.map((a) => a.id);
    const id = newAgentId(existing);
    const draft = newAgentDraft();
    const n = existing.length;
    setCanvasAgents((a) => [
      ...a,
      { id, x: 640 + (n % 4) * 40, y: 80 + (n % 4) * 56, draft },
    ]);
    setAgentBaselines((b) => ({ ...b, [id]: JSON.stringify(draftToDoc(draft)) }));
    setSelection({ kind: "agent", id });
    notify(true, `Added agent node ${id} — edit, then Save hits the REST door.`);
  }, [canvasAgents, notify]);

  /** Open a SAVED agent as a canvas node (the library's "edit on canvas"):
   *  the document loads through the door into a draft. A node with that id
   *  already on the canvas is just (re)selected. */
  const openAgentOnCanvas = useCallback(
    async (id: string) => {
      if (canvasAgents.some((a) => a.id === id)) {
        setSelection({ kind: "agent", id });
        return;
      }
      try {
        const detail = await getAgent(id);
        const draft = docToDraft(detail.agent);
        const n = canvasAgents.length;
        setCanvasAgents((a) => [
          ...a,
          { id, x: 640 + (n % 4) * 40, y: 80 + (n % 4) * 56, draft },
        ]);
        setAgentBaselines((b) => ({
          ...b,
          [id]: JSON.stringify(draftToDoc(draft)),
        }));
        setSelection({ kind: "agent", id });
        notify(true, `Opened ${id} on the canvas (in sync with the saved document).`);
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      }
    },
    [canvasAgents, notify]
  );

  const moveAgentNode = useCallback(
    (id: string, x: number, y: number) =>
      setCanvasAgents((a) => a.map((n) => (n.id === id ? { ...n, x, y } : n))),
    []
  );

  const patchAgentDraft = useCallback(
    (id: string, patch: Partial<AgentDraft>) =>
      setCanvasAgents((a) =>
        a.map((n) => (n.id === id ? { ...n, draft: { ...n.draft, ...patch } } : n))
      ),
    []
  );

  const removeAgentNode = useCallback((id: string) => {
    setCanvasAgents((a) => a.filter((n) => n.id !== id));
    setAgentBaselines((b) => {
      const next = { ...b };
      delete next[id];
      return next;
    });
    setSelection((sel) => (sel?.kind === "agent" && sel.id === id ? null : sel));
  }, []);

  /** Rename the node's identity (the REST id its Save writes to). Returns an
   *  error message, or null on commit (then the node, selection, and baseline
   *  all move to the new id — a new saved agent is born, the old one untouched). */
  const renameAgentNode = useCallback(
    (oldId: string, nextId: string): string | null => {
      if (!nextId) return "id can't be empty.";
      if (nextId === oldId) return null;
      if (!isValidAgentId(nextId)) {
        return "id must be a [A-Za-z0-9_-] token (first char a letter or digit) — the store's rule.";
      }
      if (canvasAgents.some((a) => a.id === nextId)) {
        return `a canvas agent "${nextId}" already exists — ids must be unique between nodes.`;
      }
      setCanvasAgents((a) => a.map((n) => (n.id === oldId ? { ...n, id: nextId } : n)));
      setAgentBaselines((b) => {
        if (!(oldId in b)) return b;
        const next = { ...b };
        delete next[oldId];
        next[nextId] = b[oldId];
        return next;
      });
      setSelection((sel) =>
        sel?.kind === "agent" && sel.id === oldId ? { kind: "agent", id: nextId } : sel
      );
      return null;
    },
    [canvasAgents]
  );

  /** POST /api/agents — the authority (app/schemas/agent.py) validates BEFORE
   *  writing. Throws on failure; callers own the busy/toast bookkeeping. */
  const saveAgentNode = useCallback(
    async (id: string): Promise<void> => {
      const agent = canvasAgents.find((a) => a.id === id);
      if (!agent) throw new Error(`no canvas agent node "${id}"`);
      const doc = draftToDoc(agent.draft);
      await saveAgent(id, agent.draft.name.trim() || null, doc);
      setAgentBaselines((b) => ({ ...b, [id]: JSON.stringify(doc) }));
    },
    [canvasAgents]
  );

  const onAgentSave = useCallback(
    async (id: string) => {
      setAgentBusy({ id, label: "saving…" });
      try {
        await saveAgentNode(id);
        await refreshDoors();
        notify(true, `Saved agent ${id} (validated on the backend).`);
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      } finally {
        clearAgentBusy(id);
      }
    },
    [saveAgentNode, refreshDoors, notify, clearAgentBusy]
  );

  const onAgentActivate = useCallback(
    async (id: string) => {
      setAgentBusy({ id, label: "activating…" });
      try {
        // Bind-then-run: an unsaved draft must be the saved one that binds.
        if (agentDirty[id]) await saveAgentNode(id);
        await activateAgent(id);
        await refreshDoors();
        notify(true, `Activated agent ${id} (the bind is in effect for the next turn).`);
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      } finally {
        clearAgentBusy(id);
      }
    },
    [agentDirty, saveAgentNode, refreshDoors, notify, clearAgentBusy]
  );

  const onDeleteSavedAgent = useCallback(
    async (id: string) => {
      const ok = window.confirm(
        `Delete saved agent "${id}"? This is permanent — the canvas node stays as an unsaved draft.`
      );
      if (!ok) return;
      setAgentBusy({ id, label: "deleting…" });
      try {
        await deleteAgent(id);
        // The draft loses its saved twin: it is unsaved again (dirty = true).
        setAgentBaselines((b) => {
          const next = { ...b };
          delete next[id];
          return next;
        });
        await refreshDoors();
        notify(true, `Deleted saved agent ${id}.`);
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      } finally {
        clearAgentBusy(id);
      }
    },
    [refreshDoors, notify, clearAgentBusy]
  );

  const onAgentTestMcp = useCallback(
    async (id: string, alias: string) => {
      setAgentBusy({ id, label: `testing ${alias}…` });
      try {
        // "test MCP" checks the SAVED document's alias — save first so the
        // connection check is the author's current intent, not a stale one.
        await saveAgentNode(id);
        const res = await testAgentMcp(id, alias);
        notify(
          true,
          `mcp "${alias}": connected — ${
            res.tools.length > 0 ? res.tools.join(", ") : "no tools advertised"
          }`
        );
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      } finally {
        clearAgentBusy(id);
      }
    },
    [saveAgentNode, notify, clearAgentBusy]
  );

  // ── library operations ────────────────────────────────────────────────────

  const loadGraph = useCallback(
    async (id: string) => {
      try {
        const { graph: doc } = await getGraph(id);
        const g = fromDoc(id, doc);
        setGraph(g);
        setBaseline(JSON.stringify(toDoc(g)));
        setSelection(null);
        resetRunView(); // don't leak another graph's run states onto this canvas
        notify(true, `Loaded ${id}.`);
      } catch (e) {
        notify(false, e instanceof Error ? e.message : String(e));
      }
    },
    [notify, resetRunView]
  );

  const newGraph = useCallback(() => {
    const slug = `loop-${new Date().toISOString().slice(11, 19).replace(/:/g, "")}`;
    const g = seedGraph(slug);
    setGraph(g);
    setBaseline(JSON.stringify(toDoc(g)));
    setSelection(null);
    resetRunView();
    notify(true, `Fresh graph ${slug} — add nodes and save when ready.`);
  }, [notify, resetRunView]);

  // ── persistence + activation + run ────────────────────────────────────────

  const doSave = useCallback(async (): Promise<string> => {
    setSaving(true);
    try {
      const doc = toDoc(graph);
      await saveGraph(graph.id, graph.name, doc);
      setBaseline(JSON.stringify(doc));
      await refreshDoors();
      return graph.id;
    } finally {
      setSaving(false);
    }
  }, [graph, refreshDoors]);

  const onSave = useCallback(async () => {
    try {
      const id = await doSave();
      notify(true, `Saved ${id} (validated on the backend).`);
    } catch (e) {
      notify(false, e instanceof Error ? e.message : String(e));
    }
  }, [doSave, notify]);

  const onActivate = useCallback(async () => {
    try {
      if (dirty) {
        // Saving first keeps the activated document in sync with the canvas.
        await doSave();
      }
      await activate({ loop: "graph", graph_id: graph.id });
      await refreshDoors();
      notify(true, `Activated loop=graph → ${graph.id} (next chat turn uses it).`);
    } catch (e) {
      notify(false, e instanceof Error ? e.message : String(e));
    }
  }, [dirty, doSave, graph.id, notify, refreshDoors]);

  const onDuplicate = useCallback(async () => {
    try {
      const stamp = new Date().toISOString().slice(5, 10).replace("-", "") +
        new Date().toISOString().slice(11, 13);
      const id = `${graph.id}-copy${stamp}`.slice(0, 64);
      const doc = toDoc(graph);
      await saveGraph(id, `${graph.name}-copy`, doc);
      await refreshDoors();
      const { graph: doc2 } = await getGraph(id);
      const g = fromDoc(id, doc2);
      setGraph(g);
      setBaseline(JSON.stringify(toDoc(g)));
      setSelection(null);
      resetRunView();
      notify(true, `Duplicated as ${id}.`);
    } catch (e) {
      notify(false, e instanceof Error ? e.message : String(e));
    }
  }, [graph, notify, refreshDoors, resetRunView]);

  const onDelete = useCallback(async () => {
    const confirm = window.confirm(`Delete saved graph "${graph.id}"? This is permanent.`);
    if (!confirm) return;
    try {
      await deleteGraph(graph.id);
      const g = seedGraph(`loop-${Date.now().toString().slice(-6)}`);
      setGraph(g);
      setBaseline(JSON.stringify(toDoc(g)));
      setSelection(null);
      resetRunView();
      await refreshDoors();
      notify(true, `Deleted ${graph.id}.`);
    } catch (e) {
      notify(false, e instanceof Error ? e.message : String(e));
    }
  }, [graph.id, notify, refreshDoors, resetRunView]);

  const onRun = useCallback(
    async (goal: string) => {
      // Arm the live view: the poller starts tracking the run as soon as it
      // begins (POST /api/chat blocks until the WHOLE run is done, so the
      // canvas highlight happens DURING this await — the canvas seam).
      // armTs also bounds the poller: finished records older than this
      // belong to the PREVIOUS run (the save→activate window).
      setArmTs(Date.now());
      setRunActive(true);
      setRunRecord(null);
      let note: string | undefined;
      try {
        if (dirty) {
          await doSave();
          note = `Saved, then activated + ran ${goal.slice(0, 40)}…`;
        } else {
          await activate({ loop: "graph", graph_id: graph.id });
          note = `Activated + ran ${goal.slice(0, 40)}…`;
        }
        const answer = await postChat(goal, []);
        // The run is recorded server-side; read it back through the canvas seam.
        let run: LastRun = { status: "ok", at: Date.now() / 1000, answer, events: [] };
        try {
          const rec = await getLastRun(graph.id);
          run = { ...run, ...rec };
        } catch {
          /* backend has no last-run yet — fall back to the chat answer */
          note = (note ?? "") + " (no node-event record; showing answer)";
        }
        // Settle on the FINISHED record (ok/failed + full event list): its
        // per-node states stay visible on the canvas until the next run.
        setRunRecord(run);
        await refreshDoors();
        return { run, note };
      } finally {
        setRunActive(false);
      }
    },
    [dirty, doSave, graph.id, refreshDoors]
  );

  return (
    <div className={styles.page}>
      <Header
        online={online}
        activeLoop={activeLoop}
        activeGraph={activeGraph}
        theme={theme}
        onToggleTheme={toggle}
      />

      <div className={styles.body}>
        <aside className={styles.left}>
          <AgentLibrary
            agents={agents}
            activeId={activeAgent}
            online={online === true}
            onOpenLoop={(id) => loadGraph(id)}
            onNewAgent={() => addAgentNode()}
            onEditOnCanvas={(id) => void openAgentOnCanvas(id)}
          />
          <GraphLibrary
            graphs={graphs}
            active={activeGraph}
            currentId={graph.id}
            online={online === true}
            onLoad={loadGraph}
            onNew={newGraph}
          />
          <Palette
            primitives={primitives}
            apiOnline={online === true && apiLive}
            onAdd={addNode}
            onAddAgent={() => addAgentNode()}
          />
        </aside>

        <section className={styles.center}>
          <Toolbar
            id={graph.id}
            dirty={dirty}
            saving={saving}
            active={activeGraph === graph.id}
            online={online === true}
            onSave={onSave}
            onActivate={onActivate}
            onDuplicate={onDuplicate}
            onDelete={onDelete}
          />
          <Canvas
            graph={graph}
            meta={meta}
            online={online === true}
            selection={selection}
            onSelect={setSelection}
            onMoveNode={moveNode}
            onRemoveNode={removeNode}
            onAddEdge={addEdge}
            onRemoveEdge={removeEdge}
            onNode={patchNode}
            onBranches={patchBranches}
            runAnswer={runRecord?.answer ?? null}
            nodeStates={nodeStates}
            nodeOutputs={nodeOutputs}
            skipDetails={skipDetails}
            /* the agent layer (the second document family, docs/agent-graph.md) */
            canvasAgents={canvasAgents}
            activeAgentId={activeAgent}
            agentDirty={agentDirty}
            agentBusy={agentBusy}
            savedGraphIds={savedGraphIds}
            currentGraphId={graph.id}
            onMoveAgent={moveAgentNode}
            onRemoveAgent={removeAgentNode}
            onAgentDraft={patchAgentDraft}
            onAgentRename={renameAgentNode}
            onAgentSave={(id) => void onAgentSave(id)}
            onAgentActivate={(id) => void onAgentActivate(id)}
            onAgentDeleteSaved={(id) => void onDeleteSavedAgent(id)}
            onAgentTestMcp={(id, alias) => void onAgentTestMcp(id, alias)}
          />
        </section>

        <div className={styles.right}>
          <Inspector
            graph={graph}
            selection={selection}
            issues={issues}
            onGraph={patchGraph}
            onEdge={patchEdge}
          />
          <TestPanel
            online={online === true}
            graphId={graph.id}
            dirty={dirty}
            onRun={onRun}
            nodeStates={nodeStates}
          />
        </div>
      </div>

      {toast && (
        <div className={`${styles.toast} ${toast.ok ? styles.toastOk : styles.toastErr}`} role="status">
          {toast.msg}
        </div>
      )}
    </div>
  );
}

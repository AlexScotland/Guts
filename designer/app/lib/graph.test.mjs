/**
 * checkCanvas — route-node rules (v1, docs/ai-routing-nodes.md).
 *
 * Run with:    npm test          (or: node --no-warnings --test app/lib/graph.test.mjs)
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 * Note:        graph.ts imports ./types type-only (erased at strip time), so
 *              the .mjs test imports the .ts module directly — same trick as
 *              run.test.mjs. The backend validator (app/schemas/graph.py) is
 *              the AUTHORITY; these checks mirror it cheaply before save.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  checkCanvas,
  FALLBACK_PRIMITIVES,
  fromDoc,
  BUDGETED,
  TEMPLATES,
  entryEligible,
  fedBy,
  feedsInto,
  insertTemplate,
  whyNotTypable,
} from "./graph.ts";

const META = new Map(FALLBACK_PRIMITIVES.map((p) => [p.primitive, p]));

/** A minimal valid route canvas: dispatch → web (research), dispatch → code (act). */
function routeCanvas(configOverrides = {}, edgeOverrides = null) {
  const config = {
    decider: "manual",
    pinned: "web",
    options: { web: "Deep web research", code: "Writes and tests code" },
    fallback: "web",
    ...configOverrides,
  };
  if (config.fallback === null) delete config.fallback;
  if (config.pinned === null) delete config.pinned;
  const edges = edgeOverrides ?? [
    { from: "dispatch", to: "web", type: "data" },
    { from: "dispatch", to: "code", type: "data" },
  ];
  return {
    id: "g",
    name: "g",
    budget: null,
    max_parallel: null,
    nodes: [
      { id: "dispatch", primitive: "route", x: 0, y: 0, config, budget: null, branches: [] },
      { id: "web", primitive: "research", x: 300, y: 0, config: {}, budget: null, branches: [] },
      { id: "code", primitive: "act", x: 300, y: 200, config: {}, budget: null, branches: [] },
    ],
    edges,
  };
}

/** The route-node rule errors (all of them are labeled on the dispatch node). */
const routeErrors = (g) =>
  checkCanvas(g, META).filter(
    (i) => i.severity === "error" && i.message.includes('Node "dispatch"')
  );

test("route + matching choice set validates cleanly", () => {
  const issues = checkCanvas(routeCanvas(), META).filter((i) => i.severity === "error");
  assert.deepEqual(issues, []);
});

test("the offline fallback table carries the route entry (same ports as the backend)", () => {
  const p = FALLBACK_PRIMITIVES.find((x) => x.primitive === "route");
  assert.ok(p, "route is in the offline vocabulary");
  assert.deepEqual(p.accepts, ["goal", "context", "result"]);
  assert.deepEqual(p.provides, ["context", "result"]);
  assert.equal(p.side_effects, false);
});

test("fromDoc keeps route config intact (options/decider survive a round trip)", () => {
  const doc = {
    nodes: {
      dispatch: {
        primitive: "route",
        config: { decider: "manual", pinned: "web", options: { web: "w", code: "c" } },
      },
      web: { primitive: "research" },
      code: { primitive: "act" },
    },
    edges: [
      { from: "dispatch", to: "web", type: "data" },
      { from: "dispatch", to: "code", type: "data" },
    ],
  };
  const g = fromDoc("rt", doc);
  const d = g.nodes.find((n) => n.id === "dispatch");
  assert.equal(d.primitive, "route");
  assert.deepEqual(d.config.options, { web: "w", code: "c" });
});

test("fewer than two outgoing data edges is an error", () => {
  const g = routeCanvas({}, [{ from: "dispatch", to: "web", type: "data" }]);
  assert.ok(routeErrors(g).some((i) => /at least two outgoing/.test(i.message)));
});

test("control edges do NOT count toward the choice set", () => {
  const g = routeCanvas(
    {},
    [
      { from: "dispatch", to: "web", type: "data" },
      { from: "dispatch", to: "code", type: "control", max_passes: 1 },
    ]
  );
  assert.ok(routeErrors(g).some((i) => /at least two outgoing/.test(i.message)));
});

test("an option key without a matching out-edge is an error", () => {
  const g = routeCanvas({ options: { web: "w", code: "c", ghost: "g" } });
  assert.ok(routeErrors(g).some((i) => /must equal the choice set/.test(i.message)));
});

test("a wired out-edge missing from options is an error", () => {
  const g = routeCanvas({ options: { web: "w" } });
  assert.ok(routeErrors(g).some((i) => /must equal the choice set/.test(i.message)));
});

test("missing / non-object options is an error", () => {
  for (const cfg of [{ decider: "manual", options: undefined }, { decider: "manual", options: "nope" }]) {
    const g = routeCanvas(cfg);
    assert.ok(
      routeErrors(g).some((i) => /config\.options mapping/.test(i.message)),
      `expected an options error for ${JSON.stringify(cfg)}`
    );
  }
});

test("empty option descriptions are an error", () => {
  const g = routeCanvas({ options: { web: "  ", code: "c" } });
  assert.ok(routeErrors(g).some((i) => /non-empty description/.test(i.message)));
});

test("decider is a closed enum: rule reserved, unknown rejected, required", () => {
  const ruleG = routeCanvas({ decider: "rule" });
  assert.ok(routeErrors(ruleG).some((i) => /reserved/.test(i.message)));
  const badG = routeCanvas({ decider: "vibes" });
  assert.ok(routeErrors(badG).some((i) => /"manual" or "llm"/.test(i.message)));
  const noneG = routeCanvas({ decider: null });
  assert.ok(routeErrors(noneG).some((i) => /"manual" or "llm"/.test(i.message)));
});

test("manual decider: pinned is required and must be in the choice set", () => {
  const noPinned = routeCanvas({ pinned: null });
  assert.ok(routeErrors(noPinned).some((i) => /needs config\.pinned/.test(i.message)));
  const ghostPinned = routeCanvas({ pinned: "ghost" });
  assert.ok(routeErrors(ghostPinned).some((i) => /pinned .* not in the choice set/.test(i.message)));
});

test("llm decider: pinned is rejected", () => {
  const g = routeCanvas({ decider: "llm", pinned: "web" });
  assert.ok(
    routeErrors(g).some((i) => /pinned is only valid with the manual decider/.test(i.message))
  );
});

test("fallback must be a member of the choice set", () => {
  const g = routeCanvas({ fallback: "ghost" });
  assert.ok(routeErrors(g).some((i) => /fallback .* not in the choice set/.test(i.message)));
});

test("the llm config shape (no pinned, no fallback) is clean", () => {
  const g = routeCanvas({ decider: "llm", pinned: null, fallback: null });
  assert.deepEqual(routeErrors(g), []);
});

test("non-route graphs are untouched by the route rules", () => {
  const g = {
    id: "g",
    name: "g",
    budget: null,
    max_parallel: null,
    nodes: [
      { id: "r", primitive: "research", x: 0, y: 0, config: {}, budget: null, branches: [] },
    ],
    edges: [],
  };
  assert.deepEqual(checkCanvas(g, META), []);
});

// ── compatibility lights (palette dim/raise + canvas ghostwires) ────────

test("feedsInto / fedBy are the two directions of edgeTypable (self excluded)", () => {
  // research provides context/result:
  const into = feedsInto("research", META);
  assert.ok(into.includes("critic"));      // critic accepts result
  assert.ok(into.includes("plan"));        // plan accepts context
  assert.ok(into.includes("act"));         // act accepts context
  assert.ok(!into.includes("gate"));       // gate accepts only verdict
  assert.ok(!into.includes("prompt"));     // prompt accepts only goal
  assert.ok(!into.includes("research"));   // self is never listed
  // who is permitted to wire IN TO act (plan/context/goal):
  const intoAct = fedBy("act", META);
  assert.ok(intoAct.includes("research"));
  assert.ok(intoAct.includes("plan"));
  assert.ok(!intoAct.includes("critic"));  // verdict/pass are not act inputs
});

test("entryEligible = the backend's single-entry rule (requires⊆goal, all requires_any include goal)", () => {
  const eligible = new Set([...META.keys()].filter((p) => entryEligible(p, META)));
  for (const p of ["prompt", "research", "plan", "parallel", "route", "merge"])
    assert.ok(eligible.has(p), `${p} should be entry-eligible`);
  for (const p of ["act", "critic", "gate"])
    assert.ok(!eligible.has(p), `${p} should not be entry-eligible`);
});

test("whyNotTypable states reasons from the port table; valid pairs return empty", () => {
  assert.equal(whyNotTypable("research", "critic", META), "");
  const msg = whyNotTypable("research", "gate", META);
  assert.match(msg, /research provides context · result/);
  assert.match(msg, /gate accepts verdict/);
  assert.match(whyNotTypable("critic", "research", META), /critic/);
  assert.match(whyNotTypable("prompt", "prompt", META), /itself/);
});

// ── templates: structurally valid, insertion safe ──────────────────────────

test("TEMPLATES: single goal-feeding entry, typed data edges, control-only loops, no self-edges", () => {
  assert.equal(TEMPLATES.length, 2);
  for (const tpl of TEMPLATES) {
    const names = Object.keys(tpl.doc.nodes);
    assert.ok(names.length >= 2, tpl.id);
    const data = tpl.doc.edges.filter((e) => e.type === "data");
    const ctl = tpl.doc.edges.filter((e) => e.type === "control");
    const inDeg = new Map();
    for (const e of data) inDeg.set(e.to, (inDeg.get(e.to) ?? 0) + 1);
    const entries = names.filter((n) => !inDeg.has(n));
    assert.equal(entries.length, 1, `${tpl.id}: exactly one data-entry node`);
    assert.ok(entryEligible(tpl.doc.nodes[entries[0]].primitive, META), tpl.id);
    for (const e of data) {
      assert.notEqual(e.from, e.to, `${tpl.id}: self data edge`);
      const ok = [...(META.get(tpl.doc.nodes[e.from].primitive)?.provides ?? [])].some(
        (t) =>
          t !== "goal" &&
          (META.get(tpl.doc.nodes[e.to].primitive)?.accepts ?? []).includes(t)
      );
      assert.ok(ok, `${tpl.id}: data edge ${e.from}→${e.to} must be typed`);
    }
    for (const e of ctl) {
      const src = tpl.doc.nodes[e.from]?.primitive;
      assert.ok(src === "critic" || src === "gate", `${tpl.id}: ${e.from} owns a control edge`);
      assert.ok((e.max_passes ?? 1) >= 1 && Number.isInteger(e.max_passes ?? 1));
    }
  }
});

test("insertTemplate: collision-safe ids, positions to the right of existing content", () => {
  const empty = { nodes: [], edges: [] };
  const r1 = insertTemplate(empty, TEMPLATES[0]);
  assert.equal(r1.canvas.nodes.length, 2);
  assert.equal(r1.inserted.length, 2);

  // Collision: the canvas already has "dig" and "judge" (the user's own
  // research loop) — the template must suffix, NEVER overwrite.
  const taken = fromDoc("t", {
    nodes: { dig: { primitive: "research" }, judge: { primitive: "critic" } },
    edges: [],
  });
  const r2 = insertTemplate(taken, TEMPLATES[0]);
  const ids = r2.inserted;
  assert.ok(ids.includes("dig2"), `renamed: ${ids}`);
  assert.ok(
    r2.canvas.nodes.some((n) => n.id === "dig2" && n.primitive === "research"),
    "suffixed node kept its primitive"
  );
  assert.ok(
    r2.canvas.edges.some((e) => e.from === "dig2" || e.to === "dig2"),
    "edges are remapped onto the suffixed id"
  );
  // Offset: new content lies to the right of the rightmost existing node.
  const maxX = Math.max(...taken.nodes.map((n) => n.x));
  for (const n of r2.canvas.nodes)
    if (r2.inserted.includes(n.id)) assert.ok(n.x > maxX, "inserted nodes offset right");

  // A second insertion into an already-suffixed canvas keeps incrementing.
  const r3 = insertTemplate(r2.canvas, TEMPLATES[0]);
  assert.ok(r3.inserted.includes("dig3"), `stepped: ${r3.inserted}`);
});

test("BUDGETED reflects the backend's PortShape budgeted=True set", () => {
  assert.deepEqual([...BUDGETED].sort(), ["act", "research", "route"]);
});

// ── click vs drag (the on-node inspector popover) ────────────────────────
//
// Regression: the inspector closed on EVERY plain click because the first
// pointermove (hand jitter, which always precedes pointerup) was treated as
// a drag → immediate deselect + node nudge. Now: movement within
// CLICK_DRAG_THRESHOLD stays a clean click.

import { CLICK_DRAG_THRESHOLD, grabIsCleanClick } from "./graph.ts";

test("no movement is a clean click", () => {
  assert.ok(grabIsCleanClick({ x: 100, y: 100 }, { x: 100, y: 100 }));
});

test("human click jitter (1-4 px) stays a clean click", () => {
  for (const [dx, dy] of [[1, 0], [2, 1], [3, 2], [CLICK_DRAG_THRESHOLD, 0], [0, -2]]) {
    assert.ok(
      grabIsCleanClick({ x: 100, y: 100 }, { x: 100 + dx, y: 100 + dy }),
      `(${dx},${dy}) should be a clean click`
    );
  }
});

test("beyond the threshold it is a drag", () => {
  assert.ok(!grabIsCleanClick({ x: 100, y: 100 }, { x: 106, y: 100 }));
  assert.ok(!grabIsCleanClick({ x: 100, y: 100 }, { x: 103, y: 103 })); // 3,3 > 4 (radius)
  // A real drag comfortably exceeds the threshold within a few px.
  assert.ok(!grabIsCleanClick({ x: 100, y: 100 }, { x: 110, y: 120 }));
});

// ── parallel branches (drawer edition: checkBranch + id stepping) ─────
//
// checkBranch mirrors the runtime contract in app/components/primitives/
// parallel.py: fresh blackboard (exactly one entry), no act inside,
// shape-compatible inner edges, control only from critic/gate.

import { checkBranch, nextBranchNodeId, branchChain } from "./graph.ts";

const br = (name, nodes, edges = []) => ({ name, graph: { nodes, edges } });

test("checkBranch accepts a valid two-node branch (research → critic)", () => {
  assert.deepEqual(
    checkBranch(
      br("web", { dig: { primitive: "research" }, judge: { primitive: "critic" } }, [
        { from: "dig", to: "judge", type: "data" },
      ]),
      META
    ),
    []
  );
});

test("checkBranch accepts a critic loop inside a branch (research ⇄ critic)", () => {
  assert.deepEqual(
    checkBranch(
      br("loop", { dig: { primitive: "research" }, judge: { primitive: "critic" } }, [
        { from: "dig", to: "judge", type: "data" },
        { from: "judge", to: "dig", type: "control", max_passes: 2 },
      ]),
      META
    ),
    []
  );
});

test("checkBranch rejects a side-effect act inside a branch", () => {
  const issues = checkBranch(
    br("commit", { dig: { primitive: "research" }, dox: { primitive: "act" } }, [
      { from: "dig", to: "dox", type: "data" },
    ]),
    META
  );
  assert.ok(
    issues.some((i) => /"act".*rejected inside branches/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: empty branch has no entry", () => {
  const issues = checkBranch(br("empty", {}), META);
  assert.ok(
    issues.some((i) => /no entry|at least one/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: two zero-input nodes = two candidates, flagged with both ids", () => {
  const issues = checkBranch(
    br("x2", { r1: { primitive: "research" }, r2: { primitive: "research" } }),
    META
  );
  assert.ok(
    issues.some((i) => /exactly one (entry|candidate)/.test(i.message) && i.message.includes("r1") && i.message.includes("r2")),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: the single entry must be feedable from the goal alone", () => {
  // critic requires `result` — a branch containing ONLY a critic has one
  // zero-input node, but it cannot be the goal-fed entry.
  const issues = checkBranch(br("solo", { gate: { primitive: "gate" } }), META);
  assert.ok(
    issues.some((i) => /feedable.*goal|entry/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: shape-incompatible data edge is flagged (plan → critic)", () => {
  const issues = checkBranch(
    br("x", { p: { primitive: "plan" }, c: { primitive: "critic" } }, [
      { from: "p", to: "c", type: "data" },
    ]),
    META
  );
  assert.ok(
    issues.some((i) => /shape-compatible data wire/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: control edge from a non-verdict node is rejected", () => {
  const issues = checkBranch(
    br("x", { p: { primitive: "plan" }, c: { primitive: "critic" } }, [
      { from: "p", to: "c", type: "control", max_passes: 2 },
    ]),
    META
  );
  assert.ok(
    issues.some((i) => /only (critic|gate)|control/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch: control edge without max_passes is flagged", () => {
  const issues = checkBranch(
    br("x", { p: { primitive: "plan" }, c: { primitive: "critic" } }, [
      { from: "c", to: "p", type: "control" },
    ]),
    META
  );
  assert.ok(
    issues.some((i) => /max_passes/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("checkBranch messages are labeled with the branch name", () => {
  const issues = checkBranch(br("mybranch", { dox: { primitive: "act" } }), META);
  assert.ok(issues.every((i) => i.message.startsWith('branch "mybranch"')));
});

test("checkCanvas surfaces branch issues through the canvas check", () => {
  const g = {
    id: "g",
    name: "g",
    budget: null,
    max_parallel: null,
    nodes: [
      {
        id: "fan",
        primitive: "parallel",
        x: 0,
        y: 0,
        config: {},
        budget: null,
        branches: [
          br("web", { dig: { primitive: "research" }, dox: { primitive: "act" } }, [
            { from: "dig", to: "dox", type: "data" },
          ]),
          br("kb", {}),
        ],
      },
    ],
    edges: [],
  };
  const issues = checkCanvas(g, META);
  assert.ok(issues.some((i) => /"act".*rejected inside branches/.test(i.message)));
  assert.ok(
    issues.some((i) => i.message.includes('branch "kb"') && /no entry|at least one/.test(i.message)),
    `got: ${JSON.stringify(issues)}`
  );
});

test("nextBranchNodeId steps base → base2 → base3 like the template scheme", () => {
  assert.equal(nextBranchNodeId([], "research"), "research");
  assert.equal(nextBranchNodeId(["research"], "research"), "research2");
  assert.equal(nextBranchNodeId(["research", "research2"], "research"), "research3");
  assert.equal(nextBranchNodeId(["research", "research2", "research3"], "research"), "research4");
});

test("branchChain returns nodes in declared order", () => {
  assert.deepEqual(
    branchChain(br("x", { a: { primitive: "research" }, b: { primitive: "critic" } }, [
      { from: "a", to: "b", type: "data" },
    ])),
    ["a", "b"]
  );
  assert.deepEqual(branchChain(br("empty", {})), []);
});

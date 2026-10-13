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
import { checkCanvas, FALLBACK_PRIMITIVES, fromDoc } from "./graph.ts";

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

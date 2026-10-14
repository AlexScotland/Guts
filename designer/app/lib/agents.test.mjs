/**
 * agents.ts — the layer-above display helpers (docs/agent-graph.md).
 *
 * Pin the SHAPING rules the Agents library panel depends on:
 *  - the one-line summary reads the bundle's shape (loop first — it is the
 *    distinguished child — then model/mcp/skills/persona/settings);
 *  - the bundle view keeps the closed vocab intact: capability rows are read
 *    verbatim, and the loop child is always EITHER a ref (a saved
 *    turn-graph, the expandable one) OR an alias (a built-in loop);
 *  - the loop-graph mini view is exactly "nodes + primitives, edge count" —
 *    what the canvas seam renders for a turn-graph.
 *
 * Run with:    npm test
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  summarizeAgent,
  describeBundle,
  describeLoopCanvas,
  describeLoopGraph,
} from "./agents.ts";

const FULL_AGENT = {
  id: "researcher",
  name: "Deep Researcher",
  active: false,
  loop: "killer-research",
  model: "Duggles/qwen-3-8-larger-context:latest",
  mcp: ["web"],
  skills: ["skills/citation.md"],
  has_prompt: true,
  settings: { memory: "trimmed" },
};

test("summarize: full bundle — loop first, then the shape", () => {
  const line = summarizeAgent(FULL_AGENT);
  assert.equal(
    line,
    "loop killer-research · model Duggles/qwen-3-8-larger-context:latest · 1 mcp · 1 skill · persona · memory=trimmed"
  );
});

test("summarize: minimal agent — the absences are named, not hidden", () => {
  const line = summarizeAgent({
    id: "bare",
    name: "Bare",
    active: true,
    loop: "direct",
    model: null,
    mcp: [],
    skills: [],
    has_prompt: false,
    settings: {},
  });
  assert.equal(line, "loop direct · no mcp · no skills");
});

test("summarize: plural skills + multiple settings keep their order", () => {
  const line = summarizeAgent({
    ...FULL_AGENT,
    skills: ["skills/a.md", "skills/b.md", "skills/c.md"],
    settings: { memory: "trimmed", planner: "fast" },
  });
  assert.match(line, /3 skills/);
  assert.match(line, /memory=trimmed, planner=fast/);
});

const FULL_DOC = {
  name: "Deep Researcher",
  prompt: "You are a research specialist. Cite sources.",
  model: { provider: "ollama", id: "qwen:latest" },
  mcp: [
    { alias: "web", transport: "sse", url: "http://searxng:8080/mcp" },
    { alias: "files", transport: "stdio", command: ["python", "-m", "files_mcp"] },
  ],
  skill: [
    { path: "skills/citation.md" },
    { path: "skills/pdf.md", tools: ["get_pdf_info"] },
  ],
  settings: { memory: "trimmed" },
  loop: { ref: "killer-research" },
};

test("bundle: capability rows read the closed vocab verbatim", () => {
  const b = describeBundle(FULL_DOC, "researcher");
  assert.equal(b.id, "researcher");
  assert.equal(b.name, "Deep Researcher");
  assert.equal(b.persona, true);
  assert.deepEqual(b.model, { provider: "ollama", id: "qwen:latest" });
  assert.deepEqual(b.mcp, [
    { alias: "web", transport: "sse", endpoint: "http://searxng:8080/mcp" },
    { alias: "files", transport: "stdio", endpoint: "python -m files_mcp" },
  ]);
  assert.deepEqual(b.skills[1].tools, ["get_pdf_info"]);
  assert.deepEqual(b.settings, ["memory=trimmed"]);
  assert.deepEqual(b.loop, { kind: "ref", ref: "killer-research" });
});

test("bundle: the alias loop is the alias (the built-in reading, not a graph)", () => {
  const b = describeBundle(
    { loop: { alias: "plan_execute" } },
    "builtin-loop-agent"
  );
  assert.deepEqual(b.loop, { kind: "alias", alias: "plan_execute" });
  assert.equal(b.persona, false);
  assert.equal(b.model, null);
  assert.deepEqual(b.mcp, []);
});

test("bundle: a degenerate loop is named, never invented", () => {
  const b = describeBundle({}, "ghost");
  assert.deepEqual(b.loop, { kind: "alias", alias: "(unspecified)" });
});

const HELLO_GRAPH = {
  name: "hello",
  budget: 15,
  nodes: {
    frame: { primitive: "prompt", config: { text: "Be concise." } },
    answer: { primitive: "act", config: { task: "Answer the goal." } },
  },
  edges: [{ from: "frame", to: "answer", type: "data" }],
};

test("loop-graph mini view: nodes with primitives + edge count, nothing invented", () => {
  const view = describeLoopGraph(HELLO_GRAPH);
  assert.deepEqual(view.nodes, [
    { id: "frame", primitive: "prompt" },
    { id: "answer", primitive: "act" },
  ]);
  assert.equal(view.edges, 1);
});

test("loop-graph mini view: empty graph is empty (no phantom nodes/edges)", () => {
  const view = describeLoopGraph({ nodes: {}, edges: [] });
  assert.deepEqual(view, { nodes: [], edges: 0 });
});

// The canvas's OWN working graph is the LIVE source of the same preview
// shape (no fetch, re-renders as the loop is drawn) — the preview cannot
// tell the two sources apart, so they must agree.
const HELLO_CANVAS = {
  name: "hello",
  nodes: [
    { id: "frame", primitive: "prompt", x: 10, y: 10, config: { text: "Be concise." }, budget: null, branches: [] },
    { id: "answer", primitive: "act", x: 270, y: 10, config: { task: "Answer the goal." }, budget: null, branches: [] },
  ],
  edges: [{ from: "frame", to: "answer", type: "data" }],
};

test("loop-canvas mini view: the SAME shape as the fetched-document view", () => {
  const view = describeLoopCanvas(HELLO_CANVAS);
  assert.deepEqual(view, describeLoopGraph(HELLO_GRAPH));
});

test("loop-canvas mini view: an empty canvas is an empty view", () => {
  assert.deepEqual(
    describeLoopCanvas({ name: "empty", nodes: [], edges: [] }),
    { nodes: [], edges: 0 }
  );
});

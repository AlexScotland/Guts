/**
 * agentNode.ts — the canvas AGENT NODE contract (docs/agent-graph.md,
 * "The agent node on the canvas").
 *
 * The agent node is a design-layer draft of ONE agent document: the
 * capability bundle (persona / model / mcp / skills / settings) around
 * exactly one `loop` slot — a saved turn-graph `ref` XOR a built-in
 * `alias`. Pin the SHAPING RULES the authoring surface depends on:
 *
 *  - a fresh draft is a VALID minimal agent (alias loop) that serializes to
 *    exactly `{ loop: { alias } }` — everything else omitted, nothing
 *    invented;
 *  - `draftToDoc` maps the draft onto the EXACT REST door shape
 *    (POST /api/agents body), the loop slot XOR kept: a ref doc has no
 *    `alias` key, an alias doc has no `ref` key;
 *  - `docToDraft` loads a saved document into a clean draft and the pair
 *    round-trips losslessly (a saved document stays a valid document);
 *  - `checkAgentDraft` is the preflight mirror of the closed validator
 *    (loop XOR + known aliases, transport pairing, alias uniqueness,
 *    settings slot keys ⊆ the six, provider enum, the id token rule) —
 *    an honest preview of the backend's 400, never a new authority;
 *  - `graphToSaveBeforeAgent` is the SAVE-ORDER seam (docs/agent-graph.md):
 *    the backend requires `loop.ref` to name a persisted graph, and the
 *    only graph the canvas can change is the one ON the canvas — so a ref
 *    naming the canvas's own graph means "persist that graph first", while
 *    a ref to another (necessarily already-saved) graph or an alias loop
 *    means "nothing precedes the agent save".
 *
 * Run with:    npm test
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  checkAgentDraft,
  docToDraft,
  draftToDoc,
  graphToSaveBeforeAgent,
  isValidAgentId,
  loopSlotLine,
  newAgentDraft,
  newAgentId,
  newMcpRow,
} from "./agentNode.ts";

const messages = (issues) => issues.map((i) => i.message);
const hasIssue = (draft, id, re) =>
  messages(checkAgentDraft(draft, id)).some((m) => re.test(m));
const clean = (draft, id) => assert.deepEqual(checkAgentDraft(draft, id), []);

test("fresh draft WITHOUT a canvas id: a VALID minimal agent — the alias loop, nothing invented", () => {
  const d = newAgentDraft();
  clean(d, "agent1");
  assert.deepEqual(draftToDoc(d), { loop: { alias: "plan_execute" } });
  // A blank canvas id means the same thing (no working graph to own):
  assert.deepEqual(newAgentDraft("   ").loop, { mode: "alias", alias: "plan_execute" });
});

test("fresh draft WITH a canvas id: the loop slot OWNS it — a ref to THIS canvas's graph", () => {
  const d = newAgentDraft("my-loop");
  assert.deepEqual(d.loop, { mode: "ref", ref: "my-loop" });
  clean(d, "agent1");
  assert.deepEqual(draftToDoc(d), { loop: { ref: "my-loop" } });
  // Trimmed identically to the save-order's trim (a padded id is the same graph):
  assert.deepEqual(newAgentDraft("  my-loop  ").loop, { mode: "ref", ref: "my-loop" });
});

test("newAgentId: fresh, collision-free ids", () => {
  assert.equal(newAgentId([]), "agent1");
  assert.equal(newAgentId(["agent1"]), "agent2");
  assert.equal(newAgentId(["agent1", "agent3", "loop-7"]), "agent2");
});

const FULL_DRAFT = {
  name: "Deep Researcher",
  prompt: "Cite sources.",
  model: { provider: "ollama", id: "qwen:latest" },
  mcp: [
    { alias: "web", transport: "sse", url: "http://searxng:8080/mcp", command: "" },
    { alias: "files", transport: "stdio", url: "", command: "python -m files_mcp" },
  ],
  skill: [
    { path: "skills/citation.md", tools: "" },
    { path: "skills/pdf.md", tools: "get_pdf_info, read_pdf_page" },
  ],
  settings: [{ slot: "memory", alias: "trimmed" }],
  loop: { mode: "ref", ref: "killer-research" },
};

test("draftToDoc: the exact REST door shape (POST /api/agents body)", () => {
  const doc = draftToDoc(FULL_DRAFT);
  assert.equal(doc.name, "Deep Researcher");
  assert.equal(doc.prompt, "Cite sources.");
  assert.deepEqual(doc.model, { provider: "ollama", id: "qwen:latest" });
  assert.deepEqual(doc.mcp, [
    { alias: "web", transport: "sse", url: "http://searxng:8080/mcp" },
    { alias: "files", transport: "stdio", command: ["python", "-m", "files_mcp"] },
  ]);
  // The transport pairing: a stdio entry carries NO url key, an sse entry
  // carries NO command key (the validator rejects the wrong pairing).
  assert.ok(!("command" in doc.mcp[0]));
  assert.ok(!("url" in doc.mcp[1]));
  assert.deepEqual(doc.skill, [
    { path: "skills/citation.md" },
    { path: "skills/pdf.md", tools: ["get_pdf_info", "read_pdf_page"] },
  ]);
  assert.deepEqual(doc.settings, { memory: "trimmed" });
  assert.deepEqual(doc.loop, { ref: "killer-research" });
});

test("draftToDoc: the loop slot is ref XOR alias — never both", () => {
  const refDoc = draftToDoc(FULL_DRAFT);
  assert.ok(!("alias" in refDoc.loop));

  const aliasDraft = { ...FULL_DRAFT, loop: { mode: "alias", alias: "direct" } };
  const aliasDoc = draftToDoc(aliasDraft);
  assert.deepEqual(aliasDoc.loop, { alias: "direct" });
  assert.ok(!("ref" in aliasDoc.loop));
});

test("draftToDoc: empties are omitted, not persisted", () => {
  const d = newAgentDraft();
  d.name = "";
  d.prompt = "   ";
  assert.deepEqual(draftToDoc(d), { loop: { alias: "plan_execute" } });
});

test("checkAgentDraft: the loop is the one child — missing/unknown is named", () => {
  const noRef = { ...newAgentDraft(), loop: { mode: "ref", ref: "   " } };
  assert.ok(hasIssue(noRef, "agent1", /loop/), "a blank ref is a preflight error");

  const badAlias = { ...newAgentDraft(), loop: { mode: "alias", alias: "quantum" } };
  const aliasMsg = messages(checkAgentDraft(badAlias, "agent1")).find((m) => /alias/i.test(m));
  assert.match(aliasMsg ?? "", /plan_execute/);
  assert.match(aliasMsg ?? "", /direct/);

  const goodRef = { ...newAgentDraft(), loop: { mode: "ref", ref: "killer-research" } };
  clean(goodRef, "agent1");
});

test("checkAgentDraft: the agent identity is a token (the store's rule)", () => {
  const d = newAgentDraft();
  assert.ok(hasIssue(d, "bad id!", /id/));
  clean(d, "9researcher");
});

test("isValidAgentId: the store's token rule, directly (the rename seam)", () => {
  for (const ok of ["agent1", "9researcher", "a-b_c", "A2"]) {
    assert.ok(isValidAgentId(ok), `${ok} should be a valid id`);
  }
  for (const bad of ["", " bad", "-lead", ".", "a b", "a/b", "日本語"]) {
    assert.ok(!isValidAgentId(bad), `${JSON.stringify(bad)} should be an invalid id`);
  }
  assert.ok(!isValidAgentId("a".repeat(65)), "the id is bounded to 64 chars");
  assert.ok(isValidAgentId("a".repeat(64)), "64 chars is the last legal length");
});

test("checkAgentDraft: mcp transport pairing + alias uniqueness", () => {
  const stdioNoCommand = {
    ...newAgentDraft(),
    mcp: [{ alias: "files", transport: "stdio", url: "", command: "   " }],
  };
  assert.ok(hasIssue(stdioNoCommand, "agent1", /command/i));

  const sseNoUrl = {
    ...newAgentDraft(),
    mcp: [{ alias: "web", transport: "sse", url: "", command: "" }],
  };
  assert.ok(hasIssue(sseNoUrl, "agent1", /url/i));

  const dupes = {
    ...newAgentDraft(),
    mcp: [
      { alias: "web", transport: "sse", url: "http://a", command: "" },
      { alias: "web", transport: "sse", url: "http://b", command: "" },
    ],
  };
  assert.ok(hasIssue(dupes, "agent1", /web/), "duplicate mcp aliases are named");

  const ok = {
    ...newAgentDraft(),
    mcp: [{ alias: "web", transport: "sse", url: "http://a", command: "" }],
  };
  clean(ok, "agent1");
});

test("checkAgentDraft: a half-typed row is named, not waved through", () => {
  const d = { ...newAgentDraft(), mcp: [newMcpRow()] };
  assert.ok(hasIssue(d, "agent1", /mcp/), "a row with no alias is a preflight error");
});

test("checkAgentDraft: settings keys are the six slots (closed)", () => {
  const bad = {
    ...newAgentDraft(),
    settings: [{ slot: "quantum", alias: "fast" }],
  };
  assert.ok(hasIssue(bad, "agent1", /quantum|slot/i));
  const ok = {
    ...newAgentDraft(),
    settings: [{ slot: "memory", alias: "trimmed" }],
  };
  clean(ok, "agent1");
});

test("checkAgentDraft: model provider is the closed enum (v1 = ollama)", () => {
  const badProvider = { ...newAgentDraft(), model: { provider: "openai", id: "gpt" } };
  assert.ok(hasIssue(badProvider, "agent1", /provider|ollama/));
  const emptyId = { ...newAgentDraft(), model: { provider: "ollama", id: "  " } };
  assert.ok(hasIssue(emptyId, "agent1", /model/i));
});

test("docToDraft: a saved document loads into a clean draft (round-trip)", () => {
  const doc = {
    name: "Deep Researcher",
    prompt: "Cite sources.",
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
  const draft = docToDraft(doc);
  assert.deepEqual(draftToDoc(draft), doc);

  const aliasDoc = { loop: { alias: "plan_execute" } };
  const aliasDraft = docToDraft(aliasDoc);
  assert.deepEqual(aliasDraft.loop, { mode: "alias", alias: "plan_execute" });
  assert.deepEqual(draftToDoc(aliasDraft), aliasDoc);
});

test("graphToSaveBeforeAgent: the save-order (the backend needs a persisted ref)", () => {
  const canvasId = "my-loop";

  // A ref naming the CANVAS's OWN graph → that graph must be saved first
  // (an idempotent overwrite — the ref stays honest, no new id invented).
  const ownRef = { ...newAgentDraft(), loop: { mode: "ref", ref: canvasId } };
  assert.equal(graphToSaveBeforeAgent(ownRef, canvasId), canvasId);

  // Trimmed whitespace around the ref is the same graph (the slot trims too).
  const padded = { ...ownRef, loop: { mode: "ref", ref: `  ${canvasId} ` } };
  assert.equal(graphToSaveBeforeAgent(padded, canvasId), canvasId);

  // A ref to ANOTHER graph → nothing precedes: by definition it is already
  // saved (it was loaded from the store — the datalist's option list).
  const otherRef = { ...newAgentDraft(), loop: { mode: "ref", ref: "killer-research" } };
  assert.equal(graphToSaveBeforeAgent(otherRef, canvasId), null);

  // An alias loop → a built-in, no document behind it, nothing precedes.
  assert.equal(graphToSaveBeforeAgent(newAgentDraft(), canvasId), null);

  // A blank ref names nothing — it would 400 preflight anyway; never
  // claim a predecessor save for it.
  const blank = { ...newAgentDraft(), loop: { mode: "ref", ref: "   " } };
  assert.equal(graphToSaveBeforeAgent(blank, canvasId), null);

  // No canvas graph id → nothing to compare against, nothing precedes.
  assert.equal(graphToSaveBeforeAgent(ownRef, ""), null);
});

test("loopSlotLine: the ref names the saved graph, the alias the built-in", () => {
  const ref = loopSlotLine({ mode: "ref", ref: "killer-research" });
  assert.match(ref, /killer-research/);
  assert.match(ref, /saved/i);
  const alias = loopSlotLine({ mode: "alias", alias: "plan_execute" });
  assert.match(alias, /plan_execute/);
  assert.match(alias, /built-in/i);
});

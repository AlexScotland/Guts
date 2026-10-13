/**
 * deriveNodeOutputs — the click-to-read fold (docs/ai-per-node-outputs.md).
 *
 * Run with:    npm test          (or: node --test app/lib/run.test.mjs)
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 * Note:        `import type` in run.ts is erased at strip time, so no runtime
 *              dependency on ./types. The .mjs extension keeps this file out
 *              of Next's bundler resolution (bundler tsconfig) while letting
 *              Node import the .ts module directly.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { deriveNodeOutputs, deriveNodeSkipDetails, truncateLabel } from "./run.ts";

test("last result per node wins (control-edge re-run overwrites)", () => {
  const events = [
    { event: "start", node: "do" },
    { event: "result", node: "do", outputs: { result: "first pass" } },
    { event: "retry", node: "do" },
    { event: "result", node: "do", outputs: { result: "second pass" } },
  ];
  const m = deriveNodeOutputs(events);
  assert.equal(m.size, 1);
  assert.deepEqual(m.get("do").outputs, { result: "second pass" });
});

test("branch events fold under the BARE node id (same id space as deriveNodeStates)", () => {
  const events = [
    { event: "result", node: "judge", outputs: { verdict: { pass: false, why: "no" } } },
    { event: "result", node: "judge", branch: "b1", outputs: { verdict: { pass: true, why: "yes" } } },
  ];
  const m = deriveNodeOutputs(events);
  // one entry, keyed on the bare id; the branch tag is reported, never a key
  assert.equal(m.size, 1);
  const entry = m.get("judge");
  assert.deepEqual(entry.outputs, { verdict: { pass: true, why: "yes" } });
  assert.equal(entry.branch, "b1");
});

test("branch:node is NOT a key — ev.node is the key, verbatim", () => {
  const events = [
    { event: "result", node: "do", branch: "b1", outputs: { result: "r" } },
  ];
  const m = deriveNodeOutputs(events);
  assert.equal(m.has("b1:do"), false);
  assert.equal(m.get("do").outputs.result, "r");
});

test("non-result events, and result events without outputs, are skipped", () => {
  const events = [
    { event: "start", node: "a" },
    { event: "result", node: "a" },
    { event: "result", node: "a", detail: "no payload" },
    { event: "done", node: "a" },
  ];
  const m = deriveNodeOutputs(events);
  assert.equal(m.size, 0);
});

test("truncated map rides along when the engine cut", () => {
  const events = [
    {
      event: "result",
      node: "research",
      outputs: { result: "head…tail" },
      truncated: { result: 40_000 },
    },
  ];
  const m = deriveNodeOutputs(events);
  assert.deepEqual(m.get("research").truncated, { result: 40_000 });
});

test("null / missing input yields an empty map", () => {
  assert.equal(deriveNodeOutputs(null).size, 0);
  assert.equal(deriveNodeOutputs(undefined).size, 0);
  assert.equal(deriveNodeOutputs([]).size, 0);
});

test("truncateLabel is human-sized", () => {
  assert.equal(truncateLabel(500), "500 chars total");
  assert.equal(truncateLabel(40_000), "39 KB total");
});

// ── deriveNodeSkipDetails (v1 routing — docs/ai-routing-nodes.md) ────────

test("skip details fold: the pruned vocabulary rides the skipped events", () => {
  const events = [
    { event: "start", node: "dispatch" },
    { event: "result", node: "dispatch", outputs: { choice: "web" } },
    { event: "skipped", node: "code", detail: "not routed" },
    { event: "skipped", node: "needsplan", detail: "upstream pruned" },
  ];
  const m = deriveNodeSkipDetails(events);
  assert.equal(m.get("code"), "not routed");
  assert.equal(m.get("needsplan"), "upstream pruned");
  assert.equal(m.has("dispatch"), false); // a running node has no skip detail
});

test("skipped without a detail falls back to 'skipped'; last per node wins", () => {
  const events = [
    { event: "skipped", node: "a" },
    { event: "skipped", node: "b", detail: "first" },
    { event: "skipped", node: "b", detail: "second" },
  ];
  const m = deriveNodeSkipDetails(events);
  assert.equal(m.get("a"), "skipped");
  assert.equal(m.get("b"), "second");
});

test("null / missing input yields an empty map", () => {
  assert.equal(deriveNodeSkipDetails(null).size, 0);
  assert.equal(deriveNodeSkipDetails(undefined).size, 0);
  assert.equal(deriveNodeSkipDetails([]).size, 0);
});

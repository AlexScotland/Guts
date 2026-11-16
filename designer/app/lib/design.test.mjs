/**
 * design.test.mjs — face 2 client contract: what the DESIGN panel sends
 * over the MCP wire and how it parses the design face's answers.
 *
 * Pinned against app/design/mcp_server.py (the SERVER) and app/mcp/client.py
 * (the repo's own MCP client, the reference implementation of the wire):
 *
 *   - request: JSON-RPC 2.0 tools/call, name "draft",
 *     arguments {goal, notes?, current?};
 *   - good result: result.content[0].text = JSON {doc, verdict{ok,issues}};
 *   - visible tool failure: result.isError + content text (ApiError, kind
 *     "http" — the model unreachable is a failure the author must see);
 *   - JSON-RPC error: {error: {code, message}} (ApiError, kind "http");
 *   - RED verdict (validate_graph ✗) is a NORMAL result — parsed, not
 *     thrown (the face never raises on a bad doc);
 *   - shape violations / non-JSON text: ApiError kind "parse".
 *
 * node --test with node --experimental-strip-types (see run.test.mjs for
 * the Node-version rationale): Node imports ./api.ts directly.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, designDraft, parseDesignResult } from "./api.ts";

const DOC = {
  nodes: { dig: { primitive: "research" }, sift: { primitive: "critic" } },
  edges: [
    { from: "dig", to: "sift", type: "data" },
    { from: "dig", to: "sift", type: "control", max_passes: 4 },
  ],
};
const VERDICT_OK = { ok: true, issues: [] };
const VERDICT_RED = { ok: false, issues: ["sift: `goal` is not in scope"] };

/** A well-formed MCP response for the draft tool. */
function rpcResult(doc = DOC, verdict = VERDICT_OK, extra = {}) {
  return {
    jsonrpc: "2.0",
    id: 7,
    result: {
      content: [{ type: "text", text: JSON.stringify({ doc, verdict }) }],
      isError: false,
      ...extra,
    },
  };
}

function captureFetch(responseBody, status = 200) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return calls;
}

const realFetch = globalThis.fetch;
test.after(() => {
  globalThis.fetch = realFetch;
});

// ── parseDesignResult (pure) ────────────────────────────────────────────────

test("good draft result parses into {doc, verdict}", () => {
  const out = parseDesignResult(rpcResult(), 200);
  assert.deepEqual(out.doc, DOC);
  assert.deepEqual(out.verdict, { ok: true, issues: [] });
});

test("RED verdict is a NORMAL result (the green door failing the doc)", () => {
  const out = parseDesignResult(rpcResult(DOC, VERDICT_RED), 200);
  assert.equal(out.verdict.ok, false);
  assert.deepEqual(out.verdict.issues, ["sift: `goal` is not in scope"]);
  assert.deepEqual(out.doc, DOC); // doc rides back verbatim, never mutated
});

test("visible tool failure (isError) throws ApiError with the content text", () => {
  const body = rpcResult();
  body.result.isError = true;
  body.result.content = [
    { type: "text", text: "completer failed: model unreachable (http://host.docker.internal:11434)" },
  ];
  assert.throws(
    () => parseDesignResult(body, 200),
    (e) =>
      e instanceof ApiError &&
      e.kind === "http" &&
      /model unreachable/.test(e.message)
  );
});

test("isError with a broken content array still throws a readable ApiError", () => {
  const body = rpcResult();
  body.result.isError = true;
  body.result.content = [];
  assert.throws(
    () => parseDesignResult(body, 200),
    (e) => e instanceof ApiError && /Design tool failed/.test(e.message)
  );
});

test("JSON-RPC error (bad params / unknown tool / parse error) throws", () => {
  assert.throws(
    () =>
      parseDesignResult(
        { error: { code: -32602, message: "goal is required" }, id: 8, jsonrpc: "2.0" },
        200
      ),
    (e) => e instanceof ApiError && /-32602/.test(e.message) && /goal is required/.test(e.message)
  );
});

test("no result and no error is a parse error", () => {
  assert.throws(
    () => parseDesignResult({ jsonrpc: "2.0", id: 7 }, 200),
    (e) => e instanceof ApiError && e.kind === "parse"
  );
});

test("non-JSON tool text is a parse error (with the offending text in view)", () => {
  const body = rpcResult();
  body.result.content = [{ type: "text", text: "Sorry, I cannot…" }];
  assert.throws(
    () => parseDesignResult(body, 200),
    (e) => e instanceof ApiError && e.kind === "parse" && /non-JSON/.test(e.message)
  );
});

test("missing doc / verdict are parse errors", () => {
  const noDoc = rpcResult();
  noDoc.result.content = [{ type: "text", text: JSON.stringify({ verdict: VERDICT_OK }) }];
  assert.throws(() => parseDesignResult(noDoc, 200), (e) => /`doc`/.test(e.message));

  const noVerdict = rpcResult();
  noVerdict.result.content = [{ type: "text", text: JSON.stringify({ doc: DOC }) }];
  assert.throws(() => parseDesignResult(noVerdict, 200), (e) => /`verdict`/.test(e.message));
});

// ── designDraft (fetch-bound) ──────────────────────────────────────────────

test("designDraft posts JSON-RPC tools/call draft with goal only by default", async () => {
  const calls = captureFetch(rpcResult());
  const out = await designDraft("I want a graph that researches, fact-checks and loops");
  assert.equal(calls.length, 1);
  const sent = JSON.parse(calls[0].init.body);
  assert.equal(sent.jsonrpc, "2.0");
  assert.equal(sent.method, "tools/call");
  assert.equal(sent.params.name, "draft");
  assert.deepEqual(sent.params.arguments, {
    goal: "I want a graph that researches, fact-checks and loops",
  });
  assert.ok(String(calls[0].url).endsWith("/mcp"), `url=${calls[0].url}`);
  assert.deepEqual(out.doc, DOC);
});

test("designDraft threads notes (trimmed) and current (the canvas doc) through", async () => {
  const calls = captureFetch(rpcResult());
  await designDraft("build it", "  stop after 5 passes  ", DOC);
  const sent = JSON.parse(calls[0].init.body);
  assert.equal(sent.params.arguments.notes, "stop after 5 passes");
  assert.deepEqual(sent.params.arguments.current, DOC);
});

test("designDraft drops empty notes (the face 0 contract: notes optional)", async () => {
  const calls = captureFetch(rpcResult());
  await designDraft("build it", "   ");
  const sent = JSON.parse(calls[0].init.body);
  assert.ok(!("notes" in sent.params.arguments));
  assert.ok(!("current" in sent.params.arguments));
});

test("designDraft surfaces a red verdict as a normal result", async () => {
  captureFetch(rpcResult(DOC, VERDICT_RED));
  const out = await designDraft("build it");
  assert.equal(out.verdict.ok, false);
  assert.deepEqual(out.verdict.issues, ["sift: `goal` is not in scope"]);
});

test("designDraft: transport failure with no JSON-RPC envelope is an http ApiError", async () => {
  captureFetch({ detail: "upstream dead" }, 502);
  await assert.rejects(designDraft("build it"), (e) =>
    e instanceof ApiError && e.kind === "http" && /502/.test(e.message)
  );
});

test("designDraft: network failure maps to ApiError kind network", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };
  await assert.rejects(designDraft("build it"), (e) =>
    e instanceof ApiError && e.kind === "network" && /design face/i.test(e.message)
  );
});

test("designDraft: non-JSON body (e.g. proxy HTML error page) → parse ApiError", async () => {
  captureFetch("<html>Internal Server Error</html>", 500);
  await assert.rejects(designDraft("build it"), (e) =>
    e instanceof ApiError && e.kind === "parse" && /non-JSON/.test(e.message)
  );
});

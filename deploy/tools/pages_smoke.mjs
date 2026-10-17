/* pages_smoke.mjs — headless smoke test for the Pages landing + replay
 * player (deploy/pages/assets/js/player.js).
 *
 * Runs in plain Node with a minimal DOM shim (no dependencies): imports the
 * real player against the REAL fixture, lets it auto-play to completion, and
 * asserts the player did what a browser would show:
 *
 *   - meta line painted (graph name, preset, 7 events, 0 LLM calls)
 *   - all 7 events landed in the feed (+ the run-complete row)
 *   - node states match the product contract:
 *       dispatch ok · notes ok · blueprint skipped (badged "not routed")
 *   - the decided edge (dispatch → notes) is lit
 *   - the answer box is revealed with the echoed answer
 *   - the stage note reports the real (<1ms) engine duration
 *
 * Usage (from repo root):  node deploy/tools/pages_smoke.mjs
 * Exit code 0 = pass, 1 = fail. CI-safe (needs only Node >= 18).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const pagesDir = path.resolve(here, "..", "pages");

/* ── minimal DOM shim ────────────────────────────────────────────────── */

class ClassList {
  constructor() { this._set = new Set(); }
  add(...cs) { cs.forEach((c) => c && this._set.add(c)); }
  remove(...cs) { cs.forEach((c) => this._set.delete(c)); }
  contains(c) { return this._set.has(c); }
  has(c) { return this._set.has(c); } // alias — assertions use Set-style .has
  toggle(c) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); }
  [Symbol.iterator]() { return this._set[Symbol.iterator](); }
}

class El {
  constructor(tag, id) {
    this.tag = tag;
    this.id = id || null;
    this.children = [];
    this.attrs = new Map();
    this.classList = new ClassList();
    this.textContent = "";
    this.style = {};
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.hidden = false;
    this._innerHTML = "";
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
    if (name === "class") {
      const cl = new ClassList();
      String(value).split(/\s+/).filter(Boolean).forEach((c) => cl.add(c));
      this.classList = cl;
    }
    if (name.startsWith("data-")) {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    }
  }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  appendChild(child) { this.children.push(child); return child; }
  prepend(child) { this.children.unshift(child); }
  _remove(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
  }
  remove() { /* parent unknown; no-op — the player only removes badges */ }
  addEventListener() {}
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = v; if (v === "") this.children = []; }
  get className() { return [...this.classList].join(" "); }
  set className(v) {
    const cl = new ClassList();
    v.split(/\s+/).filter(Boolean).forEach((c) => cl.add(c));
    this.classList = cl;
  }
  querySelector(tag) { return this.children.find((c) => c.tag === tag) || null; }
  querySelectorAll(sel) {
    if (sel === ".rp-edge") return this.children.filter((c) => c.classList.has("rp-edge"));
    return [];
  }
}

const REGISTRY = new Map();
for (const id of [
  "replay-svg", "replay-btn", "event-feed", "answer-box", "answer-text",
  "stage-note", "meta-graph", "meta-preset", "meta-events", "meta-llm",
  "live-endpoint", "feed-title",
]) REGISTRY.set(id, new El("div", id));
// the real HTML ships <div id="answer-box" hidden> — mirror that initial
// state so the "revealed" assertion has teeth.
REGISTRY.get("answer-box").hidden = true;

globalThis.document = {
  querySelector(sel) {
    if (sel.startsWith("#")) return REGISTRY.get(sel.slice(1)) || null;
    throw new Error(`smoke: unshimmed selector ${sel}`);
  },
  createElement(tag) { return new El(tag); },
  createElementNS(_ns, tag) { return new El(tag); },
};
globalThis.window = { addEventListener() {}, __GUTS_CONFIG__: null };

globalThis.fetch = async (p) => {
  const file = path.join(pagesDir, String(p).replace(/^(\.\/)+/, ""));
  if (!fs.existsSync(file)) {
    return { ok: false, status: 404 };
  }
  const body = fs.readFileSync(file, "utf8");
  return { ok: true, status: 200, json: async () => JSON.parse(body) };
};

/* ── run the real player ─────────────────────────────────────────────── */

const playerUrl = path.join(pagesDir, "assets", "js", "player.js");
await import(pathToFileURL(playerUrl).href);

// auto-play starts 700ms after boot; pacing is ~3.7s → allow a generous 8s.
const deadline = Date.now() + 8000;
let note = REGISTRY.get("stage-note").textContent;
while (Date.now() < deadline && !note.includes("walked")) {
  await new Promise((r) => setTimeout(r, 200));
  note = REGISTRY.get("stage-note").textContent;
}

/* ── assertions ──────────────────────────────────────────────────────── */

let failures = 0;
const check = (label, cond) => {
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${label}`);
  if (!cond) failures += 1;
};

console.log("pages smoke — landing + replay player");

check("meta: graph name painted",
  REGISTRY.get("meta-graph").textContent.includes("manual-route"));
check("meta: offline preset",
  REGISTRY.get("meta-preset").textContent === "offline");
check("meta: 7 events", REGISTRY.get("meta-events").textContent === "7");
check("meta: 0 LLM calls", REGISTRY.get("meta-llm").textContent === "0 LLM calls");
check("playback completed (stage note)", note.includes("walked 7 events"));

const feed = REGISTRY.get("event-feed");
check("feed: 7 events + run-complete row", feed.children.length === 8);

// node state: find the node groups inside the svg scene
const svg = REGISTRY.get("replay-svg");
const nodes = {};
const collect = (el) => {
  el.children.forEach((c) => {
    if (c.classList.has("rp-node")) {
      nodes[c.dataset.node] = [...c.classList];
    }
    collect(c);
  });
};
collect(svg);
check("node dispatch → ok", (nodes.dispatch || []).includes("ok"));
check("node notes → ok", (nodes.notes || []).includes("ok"));
check("node blueprint → skipped", (nodes.blueprint || []).includes("skipped"));

const edges = svg.querySelectorAll(".rp-edge");
const lit = edges.filter((e) => e.classList.has("lit"));
check("edges: decided edge lit (dispatch→notes)",
  lit.some((e) => e.dataset.from === "dispatch" && e.dataset.to === "notes"));
check("edges: untaken edge not lit (→blueprint)",
  !edges.some((e) => e.dataset.from === "dispatch" && e.dataset.to === "blueprint" && e.classList.has("lit")));

check("answer box revealed", REGISTRY.get("answer-box").hidden === false);
check("answer text is the echo",
  REGISTRY.get("answer-text").textContent.startsWith("ECHO[notes]"));
check("stage note: honest real duration (<1ms real time)",
  REGISTRY.get("stage-note").textContent.includes("<1 ms real time"));

console.log(failures === 0
  ? "\nSMOKE PASS — all assertions held."
  : `\nSMOKE FAIL — ${failures} assertion(s) failed.`);
process.exit(failures === 0 ? 0 : 1);

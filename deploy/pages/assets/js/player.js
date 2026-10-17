/* GUTS landing — "replay of a real run" player.
 *
 * The fixture (assets/replay/run.json) is a REAL capture of one offline-
 * preset turn (deploy/tools/capture_demo_run.py). This player walks the
 * event stream with human pacing and mirrors, node-for-node and state-for-
 * state, the canvas logic in designer/app/lib/run.ts (the product's own
 * event→state map):
 *
 *   start → running    done → ok
 *   failure → failed   retry → running
 *   skipped → skipped  aborted → failed
 *   result / control are informational (highlight the decided edge)
 *
 * No framework, no build step, no dependencies.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (sel) => document.querySelector(sel);

const STATE_BY_EVENT = {
  start: "running",
  done: "ok",
  failure: "failed",
  retry: "running",
  skipped: "skipped",
  aborted: "failed",
};

let RUN = null;
let scene = null;
let playing = false;
let playGeneration = 0;
let CONFIG = null;

/* ── fixture + config ────────────────────────────────────────────────── */

async function loadJson(path) {
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

/* ── scene: nodes + edges from the graph document ────────────────────── */

function svgEl(name, attrs = {}) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function trunc(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function layerGraph(graph) {
  const nodes = Object.keys(graph.nodes);
  const preds = new Map(nodes.map((n) => [n, []]));
  for (const edge of graph.edges || []) {
    if (edge.type === "control") continue; // control edges may cycle
    if (preds.has(edge.to) && preds.has(edge.from)) {
      preds.get(edge.to).push(edge.from);
    }
  }
  const depth = new Map();
  const depthOf = (n) => {
    if (depth.has(n) && depth.get(n) !== -1) return depth.get(n);
    depth.set(n, -1); // cycle guard
    const ds = preds.get(n) || [];
    const d = ds.length === 0 ? 0 : Math.max(...ds.map(depthOf)) + 1;
    depth.set(n, d);
    return d;
  };
  nodes.forEach(depthOf);
  return depth;
}

function nodeSubtitle(spec) {
  const cfg = spec.config || {};
  if (spec.primitive === "route") {
    const bits = [];
    if (cfg.decider) bits.push(`${cfg.decider} decider`);
    if (cfg.pinned) bits.push(`pinned: ${cfg.pinned}`);
    if (spec.budget === 0) bits.push("budget 0");
    return bits.join(" · ") || "route";
  }
  if (spec.primitive === "act" && cfg.task) return `task: "${trunc(cfg.task, 44)}"`;
  if (spec.primitive === "prompt" && cfg.text) return `prompt: "${trunc(cfg.text, 44)}"`;
  if (spec.primitive === "plan") return "active planner slot";
  if (spec.budget != null) return `budget ${spec.budget}`;
  return spec.primitive;
}

function buildScene() {
  const svg = $("#replay-svg");
  svg.innerHTML = "";

  const W = 640, H = 300;
  const depth = layerGraph(RUN.graph);
  const maxD = Math.max(0, ...depth.values());

  // depth is a Map (its entries are NOT object properties — Object.keys()
  // on a Map yields []); enumerate with depth.keys() instead.
  const allNodes = [...depth.keys()];
  const layers = [];
  for (let d = 0; d <= maxD; d += 1) {
    layers.push(allNodes.filter((n) => depth.get(n) === d));
  }

  const CARD_W = 210, CARD_H = 64;
  const pos = new Map();
  const padX = 24;
  const spanX = W - padX * 2;
  layers.forEach((ids, d) => {
    const x = maxD === 0 ? (W - CARD_W) / 2 : padX + (spanX - CARD_W) * (d / maxD);
    const gap = 26;
    const totalH = ids.length * CARD_H + (ids.length - 1) * gap;
    const y0 = Math.max(16, (H - totalH) / 2);
    ids.forEach((id, i) => pos.set(id, { x, y: y0 + i * (CARD_H + gap) }));
  });

  const defs = svgEl("defs");
  const marker = svgEl("marker", {
    id: "rp-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
  });
  marker.appendChild(svgEl("path", { d: "M 0 1 L 9 5 L 0 9 z", fill: "var(--text-faint)" }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  const states = new Map();

  // edges (under nodes)
  for (const edge of RUN.graph.edges || []) {
    const a = pos.get(edge.from), b = pos.get(edge.to);
    if (!a || !b) continue;
    const x1 = a.x + CARD_W, y1 = a.y + CARD_H / 2;
    const x2 = b.x - 10, y2 = b.y + CARD_H / 2;
    const dx = Math.max(36, (x2 - x1) / 2);
    const g = svgEl("g", {
      class: "rp-edge" + (edge.type === "control" ? " control" : ""),
      "data-from": edge.from,
      "data-to": edge.to,
    });
    g.appendChild(svgEl("path", {
      class: "line",
      d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
      "marker-end": "url(#rp-arrow)",
    }));
    svg.appendChild(g);
  }

  // nodes
  for (const [id, spec] of Object.entries(RUN.graph.nodes)) {
    const { x, y } = pos.get(id);
    const g = svgEl("g", { class: "rp-node", "data-node": id });
    g.appendChild(svgEl("rect", { class: "box", x, y, width: CARD_W, height: CARD_H, rx: 10 }));
    const t1 = svgEl("text", { class: "title", x: x + 12, y: y + 23 });
    t1.textContent = id;
    const t2 = svgEl("text", { class: "kind", x: x + 12, y: y + 40 });
    t2.textContent = spec.primitive;
    const t3 = svgEl("text", { class: "sub", x: x + 12, y: y + 55 });
    t3.textContent = trunc(nodeSubtitle(spec), 40);
    g.appendChild(t1);
    g.appendChild(t2);
    g.appendChild(t3);
    svg.appendChild(g);
    states.set(id, { g, badge: null });
  }

  return {
    states,
    edges: [...svg.querySelectorAll(".rp-edge")],
  };
}

/* ── event application (product parity with designer lib/run.ts) ─────── */

function nodeEl(state, on) {
  state.g.classList.remove("running", "ok", "failed", "skipped");
  if (on) state.g.classList.add(on);
}

function setNodeBadge(state, text) {
  if (state.badge) state.badge.remove();
  if (!text) return;
  const rect = state.g.querySelector("rect");
  const t = svgEl("text", {
    class: "rp-status rp-badge",
    x: Number(rect.getAttribute("x")) + 12,
    y: Number(rect.getAttribute("y")) + Number(rect.getAttribute("height")) + 16,
  });
  t.textContent = "⊘ " + trunc(text, 34);
  state.g.appendChild(t);
  state.badge = t;
}

function applyEvent(ev) {
  // result on a hop node: light the chosen edge (informational, matching
  // the designer's treatment of result/control events).
  if (ev.event === "result" && ev.outputs && typeof ev.outputs.choice === "string") {
    const edge = scene.edges.find(
      (e) => e.dataset.from === ev.node && e.dataset.to === ev.outputs.choice
    );
    if (edge) edge.classList.add("lit");
  }
  if (ev.event === "control") {
    scene.edges.forEach((e) => e.classList.add("lit"));
  }

  const state = ev.node ? scene.states.get(ev.node) : undefined;
  if (!state) return;
  const next = STATE_BY_EVENT[ev.event];
  if (next) nodeEl(state, next);
  if (ev.event === "skipped") setNodeBadge(state, ev.detail);
}

/* ── event feed ──────────────────────────────────────────────────────── */

function feedKindClass(ev) {
  if (ev.event === "done") return "kind-ok";
  if (ev.event === "skipped") return "kind-skip";
  if (ev.event === "failure" || ev.event === "aborted") return "kind-err";
  return "";
}

function eventDetailText(ev) {
  if (ev.event === "result" && ev.outputs) {
    const o = ev.outputs;
    const parts = [];
    if (o.choice) parts.push(`branch ${o.choice}`);
    if (o.reason) parts.push(trunc(String(o.reason), 72));
    if (o.result) parts.push(trunc(String(o.result), 48));
    return parts.join(" — ");
  }
  if (ev.detail) return String(ev.detail);
  return "";
}

function appendFeed(ev) {
  const li = document.createElement("li");
  const line = document.createElement("div");
  line.className = "ev-line";
  const kind = document.createElement("span");
  kind.className = "ev-kind " + feedKindClass(ev).trim();
  kind.textContent = ev.event;
  line.appendChild(kind);
  const node = document.createElement("span");
  node.className = "ev-node";
  node.textContent = ev.node || "graph";
  line.appendChild(node);
  li.appendChild(line);
  const text = eventDetailText(ev);
  if (text) {
    const d = document.createElement("div");
    d.className = "ev-detail";
    d.textContent = text;
    d.title = text;
    li.appendChild(d);
  }
  const feed = $("#event-feed");
  feed.appendChild(li);
  feed.scrollTop = feed.scrollHeight;
  return li;
}

/* ── playback ────────────────────────────────────────────────────────── */

function resetScene() {
  if (!scene) return;
  scene.states.forEach((s) => {
    nodeEl(s, null);
    if (s.badge) { s.badge.remove(); s.badge = null; }
  });
  scene.edges.forEach((e) => e.classList.remove("lit"));
  $("#event-feed").innerHTML = "";
  $("#answer-box").hidden = true;
  $("#stage-note").textContent = "standby";
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function play() {
  if (!RUN || playing) return;
  playing = true;
  const btn = $("#replay-btn");
  btn.disabled = true;
  const gen = (playGeneration += 1);

  resetScene();
  const pace = (RUN.pace_ms && RUN.pace_ms.length === RUN.events.length + 1)
    ? RUN.pace_ms
    : [400, ...RUN.events.map(() => 420)];

  for (let i = 0; i < RUN.events.length; i += 1) {
    await sleep(pace[i]);
    if (gen !== playGeneration) return; // a newer playback superseded us
    applyEvent(RUN.events[i]);
    appendFeed(RUN.events[i]);
    noteForEvent(RUN.events[i]);
  }
  if (gen !== playGeneration) return;

  // run-complete row
  const li = document.createElement("li");
  const line = document.createElement("div");
  line.className = "ev-line";
  const kind = document.createElement("span");
  kind.className = "ev-kind kind-ok";
  kind.textContent = "run complete";
  line.appendChild(kind);
  const node = document.createElement("span");
  node.className = "ev-node";
  node.textContent = `status ${RUN.status || "ok"}`;
  line.appendChild(node);
  li.appendChild(line);
  const feed = $("#event-feed");
  feed.appendChild(li);
  feed.scrollTop = feed.scrollHeight;

  if (RUN.answer) {
    $("#answer-text").textContent = RUN.answer;
    $("#answer-box").hidden = false;
  }
  $("#stage-note").textContent =
    `walked ${RUN.events.length} events in ${RUN.real_duration_ms || "<1"} ms real time — ` +
    "paced for viewing (deterministic run, zero LLM calls)";
  btn.disabled = false;
  playing = false;
}

function noteForEvent(ev) {
  const el = $("#stage-note");
  const who = ev.node || "graph";
  if (ev.event === "start") el.textContent = `${who}: entering…`;
  else if (ev.event === "result") {
    const o = ev.outputs || {};
    if (o.choice) el.textContent = `${who}: routed to ${o.choice} — ${o.reason || ""}`;
    else el.textContent = `${who}: produced a result`;
  }
  else if (ev.event === "done") el.textContent = `${who}: complete`;
  else if (ev.event === "skipped") el.textContent = `${who}: skipped (${ev.detail || "not routed"})`;
  else if (ev.event === "failure") el.textContent = `${who}: FAILED — ${ev.detail || ""}`;
  else if (ev.event === "retry") el.textContent = `${who}: retrying…`;
  else if (ev.event === "aborted") el.textContent = `${who}: aborted (${ev.detail || ""})`;
}

/* ── meta line ───────────────────────────────────────────────────────── */

function paintMeta() {
  const m = RUN.meta || {};
  $("#meta-graph").textContent = m.graph_name || m.graph_id || "unknown graph";
  $("#meta-preset").textContent = m.preset || "—";
  $("#meta-events").textContent = String(RUN.events.length);
  const llm = $("#meta-llm");
  const calls = m.llm_calls ?? 0;
  llm.textContent = calls === 0 ? "0 LLM calls" : `${calls} LLM calls`;
  if (calls === 0) llm.style.color = "var(--ok)";
  const endpoint = $("#live-endpoint");
  if (CONFIG && CONFIG.live_api) {
    endpoint.textContent = "live API: " + CONFIG.live_api;
    endpoint.hidden = false;
  }
}

/* ── boot ────────────────────────────────────────────────────────────── */

async function boot() {
  RUN = await loadJson("assets/replay/run.json");
  try {
    CONFIG = await loadJson("config.json");
  } catch (err) {
    CONFIG = null; // optional: baked in by CI when a live backend exists
  }
  paintMeta();
  scene = buildScene();
  resetScene();
  $("#replay-btn").addEventListener("click", () => play());
  window.addEventListener("keydown", (e) => {
    if (e.key === "r" || e.key === "R") { e.preventDefault(); play(); }
  });
  setTimeout(() => play(), 700);
}

const bootNote = $("#stage-note");
boot().catch((err) => {
  console.error(err);
  if (bootNote) bootNote.textContent = `could not load the replay fixture (${err.message})`;
});

# Hot-swappable segregation of the agent loop

Status: implemented (v1)
Date: 2026-09-29

## Problem

The agent loop lived inside `AgentRuntime._execute()`: plan → execute →
evaluate, with the loop's policies (replan trigger, budget/commit behavior)
hardwired into the loop and into `LLMTaskExecutor`. The roles around it
(goal resolver, planner, executor, evaluator, memory) existed as classes with
interfaces, but composition was frozen in `build_agent()` at process start,
and `server.py` built a fresh agent per request — so **no component could be
changed at runtime** without editing code and restarting a process.

## Outcome

Every role — and the loop itself — is now a named **slot** in a
`ComponentRegistry`. Components are registered under aliases, can be swapped
at runtime per slot or as a whole **preset**, and the swap is visible from
the REPL, the HTTP API, and the chat-ui panel. The loop's topology is a
swappable `AgentLoop` component, not hardcoded control flow.

```
                 ┌────────────────────────────────────────────┐
 user input ───▶ │  AgentRuntime (thin shell)                 │
                 │   1. snapshot active components (turn)     │
                 │   2. goal resolution (slot)                │
                 │   3. loop.run(components, state)  ◀────────┼── loop slot:
                 │                                            │     plan_execute / direct
                 └────────────────────────────────────────────┘
      ┌──────────────────┬─────────────┬─────────────┬─────────────┬───────────┐
      ▼                  ▼             ▼             ▼             ▼           ▼
 goal_resolver        planner       executor      evaluator       memory       loop
 (default|passthrough)          …
```

## Design decisions

| Decision | Choice | Why |
|---|---|---|
| Loop as component | `AgentLoop` interface; `PlanExecuteLoop` (classic) + `DirectLoop` (single pass) | "Segregate the loop" means the topology itself is replaceable, not just its parts |
| Swap timing | **Per-turn snapshots**: the turn freezes the active components at start; swaps apply to the next turn | Mid-flight swaps would tear a task budget / conversation out from under a running loop |
| Memory semantics | The memory slot is **per-agent**: each `AgentRuntime`/state gets a fresh store from the active factory | Stateless server requests must never share a conversation; REPL keeps one long-lived store |
| Memory swap effect | Runtime rebinds its store at the next snapshot (generation counter); new store starts fresh, system prompt re-applied | Observable + deterministic contract |
| Component delivery | In-process factories + **JSON manifests / presets** (dotted class paths) | Simple, auditable, no plugin dir to watch (v2 candidate) |
| Construction | Factories take a `ComponentContext` (llm, tools, config, extras) | Components are self-contained; the registry supplies shared dependencies |
| Back-compat | `AgentRuntime(planner=..., task_executor=..., ...)` still works via `StaticRegistry` (immutable; `activate()` raises) | Existing tests/callers unchanged; the old `/api/chat` contract is untouched |

## Slots

| Slot | Contract | Built-in aliases |
|---|---|---|
| `goal_resolver` | `extract(user_input, history) -> Goal` | `default` (LLM GoalExtractor), `passthrough` (no LLM) |
| `planner` | `create_plan(state) -> list[Task]` | `default` (LLMPlanner), `trivial` (single task) |
| `executor` | `execute(task, state) -> answer` | `default` (LLM + tools, budgeted), `echo` (deterministic) |
| `evaluator` | `evaluate(state, task) -> EvaluationResult` | `default` (criteria), `always_complete` |
| `memory` | `ConversationMemory` store (per agent) | `default` (full), `trimmed` (bounded context) |
| `loop` | `run(components, state) -> answer` | `plan_execute`, `direct` |

## Presets

- **default** — classic LLM pipeline (extraction → planning → LLM+tools
  execution → criteria evaluation, plan-execute loop).
- **fast** — no planning round-trip, trimmed context memory.
- **offline** — fully deterministic, zero LLM calls (tests / no-model drill).

## Usage

### REPL (`python main.py`)

```
You> /components
  goal_resolver  active=default          available=default, passthrough
  planner        active=default          available=default, trivial
  …
  presets        default, fast, offline

You> /activate fast                 # whole preset
You> /activate executor echo        # single slot
You> /manifest my_components.json   # register a JSON manifest
```

### HTTP API

```
GET  /api/components                → live slots + presets (described)
POST /api/components/activate       {"preset": "fast"}  → described state
POST /api/components/activate       {"slot": "loop", "alias": "direct"}
```

### chat-ui

"Components" button in the header → live panel: presets as chips, each slot
with its active alias and clickable alternatives.

### Code

```python
from components import ComponentSlot, apply_manifest
from main import get_registry              # process-shared (REPL / HTTP server)

registry = get_registry()
registry.activate(ComponentSlot.EXECUTOR, "echo")   # one slot
registry.activate_preset("fast")                     # whole preset

manifest = {
    "slots": {"executor": {"mine": {"factory": "my_pkg.MyExecutor"}}},
    "presets": {"mine": {"executor": "mine"}},
}
apply_manifest(registry, manifest)      # dict or .json path — register, then activate
registry.activate_preset("mine")
```

## Safety properties (pinned by tests)

- `tests/test_component_hotswap.py` (27 tests) covers:
  - swap changes the **next** turn only; an in-flight turn finishes on its
    snapshot (verified by a component that swaps the loop mid-execution).
  - preset activation is **atomic**: a failing factory leaves the running
    configuration untouched.
  - memory slot: per-agent instances; live rebind on swap; stateless
    `replace()`-after-swap keeps the client conversation in the live store.
  - full API contract + error codes (400/500), manifest validation errors.
- The existing `tests/test_api_contract.py` (9 tests) still passes unchanged:
  `/api/chat` behavior and stateless semantics are preserved.

## Out of scope (v2 candidates)

- Plugin directory: watch a folder for dropped `.py` components and hot-load.
- Loop composition DSL (chains of loops, conditional routing).
- Swap observability (per-activation audit log, UI event feed).
- Per-request component selection in the stateless API (`{message,
  conversation, components: {…}}`), i.e. client-chosen loops without global
  swap.

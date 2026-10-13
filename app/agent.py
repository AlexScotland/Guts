"""The agent runtime: a thin shell around the hot-swappable component slots.

The loop logic no longer lives here — it lives in an ``AgentLoop`` component
activated in the ``loop`` slot (components/loops.py). AgentRuntime's job is
now: own the conversation state, resolve the per-turn component snapshot from
the registry, handle goal resolution, rebind the memory store when the
memory slot is swapped, and delegate the turn to the active loop.

Two entry points (unchanged contract):
  * ``run()``   - one-shot execution of the goal already set on ``state``.
  * ``chat()``  - one conversational turn: resolve a Goal from the user's
                  message, execute it via the active loop, return the answer.

Back-compat: constructing with explicit component instances (planner,
evaluator, task_executor, goal_extractor) still works — they are wrapped in
an immutable StaticRegistry. Pass ``registry=...`` to enable hot-swap.
"""
import logging

from langchain_core.messages import SystemMessage

from components.loops import LoopComponents, PlanExecuteLoop
from components.registry import (
    ComponentNotRegisteredError,
    ComponentRegistry,
    StaticRegistry,
)
from components.slots import ComponentSlot

GOAL_RESOLVER = ComponentSlot.GOAL_RESOLVER
PLANNER = ComponentSlot.PLANNER
EXECUTOR = ComponentSlot.EXECUTOR
EVALUATOR = ComponentSlot.EVALUATOR
MEMORY = ComponentSlot.MEMORY
LOOP = ComponentSlot.LOOP

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        planner=None,
        evaluator=None,
        task_executor=None,
        state=None,
        config=None,
        goal_extractor=None,
        registry=None,
        loop=None,
    ):
        self.state = state
        if registry is not None:
            self.registry = registry
            self.config = config or registry.context.config
        else:
            # Back-compat path: freeze the given instances in a static registry.
            components = {}
            if goal_extractor is not None:
                components[GOAL_RESOLVER] = goal_extractor
            if planner is not None:
                components[PLANNER] = planner
            if task_executor is not None:
                components[EXECUTOR] = task_executor
            if evaluator is not None:
                components[EVALUATOR] = evaluator
            if state is not None and getattr(state, "conversation", None) is not None:
                components[MEMORY] = state.conversation
            components[LOOP] = loop or PlanExecuteLoop()
            self.registry = StaticRegistry(components)
            self.config = config
        self.system_prompt = self.load_agent_instructions(self.config.agent_path)
        if self.state is not None:
            self.state.initialize(system_prompt=self.system_prompt)
        # Generation the runtime was built against (static registries are 0).
        self._memory_generation = self.registry.generation(MEMORY)

    # ── helpers ─────────────────────────────────────────────────

    @staticmethod
    def load_agent_instructions(path):
        with open(path) as f:
            return f.read()

    def _snapshot_components(self) -> LoopComponents:
        """Freeze the active components for this turn (swap-safe semantics).

        A memory-slot activation since the last snapshot rebinds the live
        conversation store before the turn runs (per-agent stores: the new
        store starts fresh, system prompt re-applied).
        """
        registry = self.registry
        if isinstance(registry, ComponentRegistry):
            generation = registry.generation(MEMORY)
            if generation != self._memory_generation:
                self._memory_generation = generation
                new_store = registry.create(MEMORY)
                self.state.set_conversation(new_store)
                if self.system_prompt:
                    new_store.add(SystemMessage(content=self.system_prompt))
                logger.info("Memory slot swapped; state now uses %s", type(new_store).__name__)

        def take(slot, default=None):
            try:
                return registry.active(slot)
            except ComponentNotRegisteredError:
                return default

        return LoopComponents(
            goal_resolver=take(GOAL_RESOLVER),
            planner=take(PLANNER),
            executor=take(EXECUTOR),
            evaluator=take(EVALUATOR),
            loop=take(LOOP, PlanExecuteLoop()),
        )

    # ── entry points ────────────────────────────────────────────

    def run(self):
        """One-shot execution of the goal already set on ``state``."""
        components = self._snapshot_components()
        return components.loop.run(components, self.state)

    def chat(self, user_input, history=None):
        """Handle one conversational turn.

        Extracts a Goal from ``user_input`` (using the full conversation
        history for context), executes it through the active loop, and
        returns the agent's answer.

        ``history`` is optional and only needed in stateless (HTTP) mode: when
        provided, it replaces the conversation before this turn, so the agent
        works from the client-supplied thread instead of server-side memory.
        When omitted (the REPL path), the shared ``ConversationMemory`` is
        used as before and successive turns form a coherent multi-turn dialogue.
        """
        # Snapshot first (this also rebinds the memory store if the memory
        # slot was swapped), and it must happen BEFORE the stateless replace
        # below so a client-supplied conversation lands in the LIVE store.
        # This turn then runs on one consistent component set; a swap made
        # DURING the turn lands on the NEXT turn, never mid-flight.
        components = self._snapshot_components()

        # Stateless mode: the caller supplies the prior conversation.
        if history is not None:
            self.state.conversation.replace(
                [SystemMessage(content=self.system_prompt), *history]
            )

        # Pass the full conversation so far (all prior messages) so the
        # resolved Goal reflects the entire conversation, not just this
        # single message.
        history = self.state.conversation.get()

        if components.goal_resolver is not None:
            self.state.goal = components.goal_resolver.extract(user_input, history)
        else:
            self.state.goal = user_input

        logger.info("Starting agent turn for input: %r", user_input)
        result = components.loop.run(components, self.state)
        logger.info("Agent turn completed. Result: %s", result)
        return result

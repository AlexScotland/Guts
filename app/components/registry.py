"""ComponentRegistry: the hot-swap core.

Registration model
------------------
A slot holds many named implementations (aliases). Activation resolves the
registered entry to a *live instance* ONCE at activation time and caches it;
``active(slot)`` returns the cached instance afterwards. Re-activating the
same alias is idempotent (no rebuild); activating a different alias builds
the new instance and swaps it in for the old one.

Swap semantics (per-turn snapshots)
-----------------------------------
An in-flight turn snapshots the active components at turn start and runs on
that snapshot, so a swap mid-turn takes effect at the NEXT turn. This keeps
a half-finished task budget / conversation from being torn out from under a
running loop. Listeners are notified under the lock after each activation;
the runtime uses one to rebind its live memory store.

Concurrency
-----------
All mutation is under an RLock; activation is atomic per slot, and preset
activation builds every replacement first and only then swaps, so a failing
factory leaves the running configuration untouched.
"""
import inspect
import threading

from .context import ComponentContext
from .slots import ALL_SLOTS, ComponentSlot


class ComponentNotRegisteredError(KeyError):
    """Raised by active() for a slot with no active alias."""

    def __str__(self):
        return f"No active component for slot {str(self.args[0])!s}"


class ComponentAliasError(KeyError):
    """Raised by activate() for an unknown slot alias / preset name."""


class ComponentBuildError(ValueError):
    """Raised when a registered factory cannot be built with the registry's context."""


class _Entry:
    __slots__ = ("value", "version", "description")

    def __init__(self, value, version="1.0.0", description=""):
        self.value = value
        self.version = version
        self.description = description


#: Slots whose instances are created FRESH per consumer (not shared): the
#: memory store is one per AgentRuntime/state, so concurrent server requests
#: never share a conversation even when the slot is shared.
PER_AGENT_SLOTS = frozenset({ComponentSlot.MEMORY})


class ComponentRegistry:
    """Named slots, aliased implementations, activate-at-runtime, presets."""

    def __init__(self, context: ComponentContext | None = None):
        self.context = context or ComponentContext()
        self._lock = threading.RLock()
        self._entries: dict[ComponentSlot, dict[str, _Entry]] = {s: {} for s in ALL_SLOTS}
        self._instances: dict[ComponentSlot, object] = {}
        self._aliases: dict[ComponentSlot, str] = {}
        self._presets: dict[str, dict[ComponentSlot, str]] = {}
        self._listeners: list = []
        self._generations: dict[ComponentSlot, int] = {s: 0 for s in ALL_SLOTS}
        # The `primitives` namespace (graph nodes): a parallel, closed-vocabulary
        # namespace — NOT a 7th slot; the six agent slots stay sacred. One door:
        # registered here, described by describe()/GET /api/components.
        self._primitives: dict[str, _Entry] = {}
        self._primitive_instances: dict[str, object] = {}
        self._graphs_view: object = None

    # ── registration ───────────────────────────────────────────

    def register(
        self,
        slot,
        alias: str,
        component,
        version: str = "1.0.0",
        description: str = "",
    ):
        """Register an implementation (instance, 0-arg or 1-arg-context factory).

        The FIRST alias registered for a slot becomes its active one, so a
        freshly built registry is immediately usable.
        """
        slot = ComponentSlot(slot)
        with self._lock:
            self._entries[slot][alias] = _Entry(component, version, description)
            current = self._aliases.get(slot)
            if current is None:
                self._activate_locked(slot, alias)

    def register_preset(self, name: str, mapping: dict):
        """Register a named set of (slot -> alias) selections."""
        with self._lock:
            self._presets[name] = {ComponentSlot(k): str(v) for k, v in mapping.items()}

    def add_listener(self, callback):
        """callback(slot, instance) — fired under the lock after each activation."""
        with self._lock:
            self._listeners.append(callback)

    # ── activation ─────────────────────────────────────────────

    def activate(self, slot, alias: str):
        """Swap ``alias`` into ``slot``. Idempotent for the current alias."""
        slot = ComponentSlot(slot)
        with self._lock:
            if self._aliases.get(slot) == alias and slot in self._instances:
                return self._instances[slot]
            if alias not in self._entries[slot]:
                known = ", ".join(self._entries[slot]) or "(none)"
                raise ComponentAliasError(
                    f"Unknown alias {alias!r} for slot {slot.value!r}. Available: {known}"
                )
            return self._activate_locked(slot, alias)

    def activate_preset(self, name: str) -> dict:
        """Swap every slot of a preset atomically.

        Every replacement instance is built FIRST; if any factory fails,
        nothing is swapped and the exception propagates.
        """
        with self._lock:
            if name not in self._presets:
                known = ", ".join(self._presets) or "(none)"
                raise ComponentAliasError(f"Unknown preset {name!r}. Available: {known}")
            mapping = self._presets[name]
            instances = {}
            for slot, alias in mapping.items():
                entry = self._entries[slot].get(alias)
                if entry is None:
                    raise ComponentAliasError(
                        f"Preset {name!r} references unknown alias {alias!r} for slot {slot.value!r}"
                    )
                if self._aliases.get(slot) == alias and slot in self._instances:
                    instances[slot] = self._instances[slot]
                else:
                    instances[slot] = self._build(entry, slot, alias)
            changed = {}
            for slot, instance in instances.items():
                self._aliases[slot] = mapping[slot]
                self._instances[slot] = instance
                self._generations[slot] += 1
                changed[slot.value] = mapping[slot]
                self._notify(slot, instance)
            return changed

    def _activate_locked(self, slot: ComponentSlot, alias: str):
        entry = self._entries[slot][alias]
        instance = self._build(entry, slot, alias)
        self._instances[slot] = instance
        self._aliases[slot] = alias
        self._generations[slot] += 1
        self._notify(slot, instance)
        return instance

    def _build(self, entry: _Entry, slot: ComponentSlot, alias: str):
        value = entry.value
        if not callable(value):
            return value
        try:
            sig = inspect.signature(value)
        except (TypeError, ValueError):
            return value()
        params = [
            p for p in sig.parameters.values()
            if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            and p.name not in ("self", "cls")
        ]
        required = [
            p for p in params
            if p.default is inspect.Parameter.empty and p.name not in ("ctx", "context", "_context")
        ]
        if required:
            names = ", ".join(p.name for p in required)
            raise ComponentBuildError(
                f"Factory for {slot.value}.{alias} requires parameter(s) {names!r}, which the "
                f"registry cannot supply. Register a plain instance or a (context) -> component factory."
            )
        if any(p.name in ("ctx", "context", "_context") for p in params):
            return value(self.context)
        return value()

    def _notify(self, slot: ComponentSlot, instance):
        for callback in list(self._listeners):
            callback(slot, instance)

    # ── accessors ──────────────────────────────────────────────

    def create(self, slot) -> object:
        """Build a FRESH instance of the slot's active alias.

        The accessor for per-agent slots (memory): each AgentRuntime/state
        gets its own store, so server requests stay concurrency-safe.
        """
        slot = ComponentSlot(slot)
        with self._lock:
            alias = self._aliases.get(slot)
            if alias is None:
                raise ComponentNotRegisteredError(slot)
            entry = self._entries[slot][alias]
            return self._build(entry, slot, alias)

    def generation(self, slot) -> int:
        """Monotonic counter bumped on every activation of the slot.

        Runtimes record the generation they were built against and compare it
        at turn start to detect a memory-slot swap (and rebind their store)
        without the registry having to call back into them.
        """
        slot = ComponentSlot(slot)
        with self._lock:
            return self._generations.get(slot, 0)

    def active(self, slot) -> object:
        slot = ComponentSlot(slot)
        with self._lock:
            if slot not in self._instances:
                raise ComponentNotRegisteredError(slot)
            return self._instances[slot]

    def active_alias(self, slot) -> str | None:
        slot = ComponentSlot(slot)
        with self._lock:
            return self._aliases.get(slot)

    def aliases(self, slot) -> list[str]:
        slot = ComponentSlot(slot)
        with self._lock:
            return list(self._entries[slot])

    def presets(self) -> dict[str, dict[ComponentSlot, str]]:
        with self._lock:
            return {name: dict(m) for name, m in self._presets.items()}

    def has_slot(self, slot) -> bool:
        with self._lock:
            return ComponentSlot(slot) in self._instances

    # ── primitives namespace (the graph nodes, one door) ─────────

    def register_primitive(self, alias, component, version: str = "1.0.0", description: str = ""):
        """Register a graph-node primitive by name.

        ``component`` is a NodeBase subclass (instantiated on first use) or a
        ready instance. Re-registering a name replaces the primitive, so a
        contributor's node is "a file + a registration" — discovered through
        this one door (describe() / GET /api/components).
        """
        with self._lock:
            self._primitives[alias] = _Entry(component, version, description)
            self._primitive_instances.pop(alias, None)

    def primitive(self, alias) -> object:
        """The live instance behind a primitive name (built once, cached)."""
        with self._lock:
            if alias not in self._primitives:
                known = ", ".join(self._primitives) or "(none)"
                raise ComponentAliasError(f"Unknown primitive {alias!r}. Known: {known}")
            cached = self._primitive_instances.get(alias)
            if cached is not None:
                return cached
            value = self._primitives[alias].value
            instance = value() if callable(value) else value
            self._primitive_instances[alias] = instance
            return instance

    def primitive_aliases(self) -> list[str]:
        with self._lock:
            return list(self._primitives)

    def set_graphs_view(self, view):
        """Bind the ``graphs`` block of describe() to a zero-arg callable.

        Wired in main.py to the process-shared GraphStore — the registry
        stays store-agnostic, and GET /api/components shows both doors
        (primitives + graphs) in one response.
        """
        with self._lock:
            self._graphs_view = view

    @staticmethod
    def _primitive_view(alias: str, entry: _Entry) -> dict:
        """Serializable primitive card (name, version, ports, side effects)."""
        value = entry.value
        view = {"name": alias, "version": entry.version, "description": entry.description}
        for attr in ("requires", "requires_any", "provides", "accepts"):
            ports = getattr(value, attr, None)
            if ports:
                view[attr] = list(ports)
        if getattr(value, "side_effects", False):
            view["side_effects"] = True
        return view

    # ── introspection ──────────────────────────────────────────

    def describe(self) -> dict:
        """Serializable view of the live configuration (API / REPL / tests)."""
        with self._lock:
            slots = {}
            for slot in ALL_SLOTS:
                entries = [
                    {"alias": alias, "version": e.version, "description": e.description}
                    for alias, e in self._entries[slot].items()
                ]
                slots[slot.value] = {
                    "active": self._aliases.get(slot),
                    "available": entries,
                }
            view = {
                "slots": slots,
                "presets": {
                    name: {s.value: alias for s, alias in mapping.items()}
                    for name, mapping in self._presets.items()
                },
            }
            # The graph doors (purely additive blocks — absence = not wired):
            # the closed primitive vocabulary + the saved/active graphs.
            view["primitives"] = [
                self._primitive_view(alias, entry) for alias, entry in self._primitives.items()
            ]
            if self._graphs_view is not None:
                view["graphs"] = self._graphs_view()
            return view


class StaticRegistry:
    """Read-only view over a fixed set of instances.

    Used when an AgentRuntime is built with explicit component instances
    instead of a registry (back-compat path). activate() is a loud error so
    nobody thinks they are hot-swapping a frozen configuration.
    """

    def __init__(self, components: dict, context: ComponentContext | None = None):
        self.context = context or ComponentContext()
        self._components = {ComponentSlot(k): v for k, v in components.items()}

    def active(self, slot) -> object:
        slot = ComponentSlot(slot)
        if slot not in self._components:
            raise ComponentNotRegisteredError(slot)
        return self._components[slot]

    def create(self, slot) -> object:
        return self.active(slot)

    def generation(self, slot) -> int:
        return 0  # a static registry can never be activated

    def active_alias(self, slot) -> str | None:
        return "static" if ComponentSlot(slot) in self._components else None

    def aliases(self, slot) -> list[str]:
        slot = ComponentSlot(slot)
        return ["static"] if slot in self._components else []

    def presets(self) -> dict:
        return {}

    def has_slot(self, slot) -> bool:
        return ComponentSlot(slot) in self._components

    def add_listener(self, callback):
        return None  # nothing can ever activate; ignore

    def register(self, *args, **kwargs):
        raise RuntimeError(
            "StaticRegistry is immutable; use ComponentRegistry for hot-swappable components"
        )

    def register_preset(self, name, mapping):
        raise RuntimeError(
            "StaticRegistry is immutable; use ComponentRegistry for hot-swappable components"
        )

    def activate(self, slot, alias):
        raise RuntimeError(
            "StaticRegistry is immutable; pass a ComponentRegistry to AgentRuntime to enable hot-swap"
        )

    def activate_preset(self, name):
        raise RuntimeError(
            "StaticRegistry is immutable; pass a ComponentRegistry to AgentRuntime to enable hot-swap"
        )

    def describe(self) -> dict:
        slots = {}
        for slot in ALL_SLOTS:
            if slot in self._components:
                slots[slot.value] = {
                    "active": "static",
                    "available": [{"alias": "static", "version": "0", "description": "fixed at construction"}],
                }
        return {"slots": slots, "presets": {}}

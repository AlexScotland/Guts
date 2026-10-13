"""Composition root for the agent harness.

``build_agent()`` builds one AgentRuntime (fresh state + conversation store)
on top of the process-shared ``ComponentRegistry``, which is what makes the
loop hot-swappable: every agent — REPL or server request — reads its
components from the same registry, so activating a new alias or preset
affects the very next turn with no restart.

REPL extras (``/`` commands) drive the same registry:
  /components  - list slots, active alias, available alternatives
  /presets     - list named presets
  /activate <preset>        - swap a whole preset in (e.g. /activate fast)
  /activate <slot> <alias>  - swap one slot (e.g. /activate executor echo)
  /manifest <path.json>     - register + report a JSON manifest
  /help        - this command list
"""
import json
import logging

from agent import AgentRuntime
from components import (
    ComponentContext,
    ComponentRegistry,
    ComponentSlot,
    apply_manifest,
    register_builtin_components,
    register_graph_components,
)
from inference.config import DEFAULT_MODEL
from inference.ollama import OllamaProvider
from tools.search import web_search
from tools.summarize import list_files, read_file
from tools.memory import list_memory, search_memory, read_memory, remember
from tools.pdf import read_pdf_page, get_pdf_info
from tools.python_tools import validate_python, run_python, run_file, edit_file, run_shell
from tools.files import write_file
from tools.utils import get_current_time
from tools.tool_registry import ToolRegistry
from state import AgentState
from agent_config import AgentConfig

ALL_TOOLS = [get_pdf_info, read_pdf_page, list_files, read_file,
             list_memory, search_memory, read_memory, remember,
             validate_python, run_python, run_file, edit_file, write_file,
             run_shell, web_search, get_current_time]


logging.basicConfig(level=logging.INFO)

BANNER = (
    "========================================\n"
    "  Interactive Agent Conversation\n"
    "  Ask questions and get answers from the agent.\n"
    "  Type 'exit' or 'quit' (or Ctrl-D) to end the session.\n"
    f"  Type '/help' for component hot-swap commands.\n"
    "========================================"
)

COMMAND_HELP = (
    "Component hot-swap commands:\n"
    "  /components                 list slots, active alias, available alternatives\n"
    "  /presets                    list named presets\n"
    "  /activate <preset>          activate a preset, e.g. /activate fast\n"
    "  /activate <slot> <alias>    swap one slot, e.g. /activate executor echo\n"
    "  /manifest <path.json>       register a JSON manifest (slots + presets)\n"
    "  /help                       this help text"
)


# ── Shared registry (the hot-swap core for this process) ────────────────────

_shared_registry: ComponentRegistry | None = None


def get_registry() -> ComponentRegistry:
    """The process-shared ComponentRegistry, built once with the default
    pipeline active. Swaps made anywhere (REPL, API, code) are visible to
    every agent built afterwards."""
    global _shared_registry
    if _shared_registry is not None:
        return _shared_registry

    llm = OllamaProvider(model=DEFAULT_MODEL)
    tools_registry = ToolRegistry()
    tools_registry.register_many(ALL_TOOLS)
    context = ComponentContext(
        llm=llm, tools=tools_registry, config=AgentConfig()
    )
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    # The graph loop (loop.graph) + the 8 primitives + the /api/graphs store:
    # purely additive — activation is explicit, default pipeline untouched.
    register_graph_components(registry, context)
    _shared_registry = registry
    return _shared_registry


def build_agent(registry: ComponentRegistry | None = None, agent_id: str = "agent_1"):
    """Build one AgentRuntime on the shared registry.

    Safe to call once per conversation turn (the HTTP server does exactly
    this): each agent gets a FRESH conversation store from the memory slot
    (per-agent semantics), so concurrent requests never share a conversation.
    The shared registry is what carries hot-swap state across turns.
    """
    registry = registry or get_registry()
    conversation = registry.create(ComponentSlot.MEMORY)
    state = AgentState(agent_id=agent_id, goal=None, conversation=conversation)

    return AgentRuntime(
        state=state,
        config=registry.context.config,
        registry=registry,
    )


# ── REPL ────────────────────────────────────────────────────────────────────


def _render_describe(describe: dict) -> str:
    lines = []
    for slot, info in describe["slots"].items():
        available = ", ".join(a["alias"] for a in info["available"]) or "-"
        lines.append(f"  {slot:<14} active={info['active'] or '-':<16} available={available}")
    preset_names = ", ".join(describe["presets"]) or "-"
    lines.append(f"  {'presets':<26} {preset_names}")
    return "\n".join(lines)


def handle_command(line: str, registry: ComponentRegistry) -> str:
    """Execute one REPL '/command'. Returns the text to print.

    Split out so tests can exercise it without a TTY.
    """
    parts = line.strip()[1:].split()
    if not parts:
        return COMMAND_HELP

    cmd = parts[0].lower()

    if cmd == "help":
        return COMMAND_HELP

    if cmd == "components":
        return _render_describe(registry.describe())

    if cmd == "presets":
        presets = registry.presets()
        if not presets:
            return "(no presets registered)"
        rows = [f"  {name:<12} {', '.join(f'{s.value}={a}' for s, a in sorted(m.items(), key=lambda kv: kv[0].value))}"
                for name, m in presets.items()]
        return "\n".join(rows)

    if cmd == "manifest":
        if len(parts) != 2:
            return "usage: /manifest <path.json>"
        try:
            summary = apply_manifest(registry, parts[1])
        except Exception as exc:
            return f"manifest error: {exc}"
        slot_rows = [
            f"{slot}: {', '.join(aliases)}"
            for slot, aliases in summary["slots"].items()
        ]
        lines = ["registered slots: " + (", ".join(slot_rows) or "-")]
        lines.append(f"presets: {', '.join(summary['presets']) or '-'}")
        return "\n".join(lines)

    if cmd == "activate":
        if len(parts) not in (2, 3):
            return "usage: /activate <preset>   or   /activate <slot> <alias>"
        try:
            if len(parts) == 2:
                registry.activate_preset(parts[1])
                return f"activated preset {parts[1]!r}\n" + _render_describe(registry.describe())
            slot_value, alias = parts[1], parts[2]
            try:
                registry.activate(ComponentSlot(slot_value), alias)
            except ValueError:
                return f"unknown slot {slot_value!r} (expected one of: {', '.join(s.value for s in ComponentSlot)})"
            return f"activated slot {slot_value} -> {alias}\n" + _render_describe(registry.describe())
        except KeyError as exc:
            return f"activate failed: {exc}"
        except Exception as exc:
            return f"activate failed: {exc}"

    return f"unknown command {parts[0]!r} — try /help"


def main():
    """Launch an interactive, multi-turn conversation with the agent."""
    registry = get_registry()
    agent = build_agent(registry)
    print(BANNER)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q", "bye"):
            print("Goodbye.")
            break

        if user_input.startswith("/"):
            try:
                print(handle_command(user_input, registry))
            except Exception as exc:
                logging.exception("Command failed")
                print(f"(error) {exc}")
            continue

        try:
            answer = agent.chat(user_input)
        except Exception as exc:  # keep the session alive on a bad turn
            logging.exception("Agent turn failed")
            print(f"Agent> (error) {exc}")
            continue

        print(f"\nAgent> {answer}")


if __name__ == "__main__":
    main()

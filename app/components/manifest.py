"""JSON manifests: declare slot implementations and presets declaratively.

Manifest shape
--------------
{
  "slots": {
    "<slot>": {
      "<alias>": {
        "factory": "module.path.callable_or_class",   # required
        "version": "1.0.0",                              # optional
        "description": "what it does"                    # optional
      }
    }
  },
  "presets": {
    "<preset>": { "<slot>": "<alias>", ... }
  }
}

A factory is a callable ``(context) -> component`` (see ComponentContext) or
a plain class / 0-arg factory; the registry resolves it at activation time,
so manifest files are inert until their components are activated.
"""
import importlib
import json
import logging

from .registry import ComponentRegistry
from .slots import ComponentSlot

logger = logging.getLogger(__name__)


class ManifestError(ValueError):
    """Raised for a malformed manifest or unresolvable factory path."""


def _resolve(dotted: str):
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise ManifestError(f"Factory path {dotted!r} is not a dotted module path")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ManifestError(f"Cannot import module for {dotted!r}: {exc}") from exc
    if not hasattr(module, attr):
        raise ManifestError(f"Module {module_path!r} has no attribute {attr!r}")
    return getattr(module, attr)


def load_manifest(source):
    """Parse a manifest (JSON text, file path, or dict) into (slots, presets).

    Returns:
        slots   - {ComponentSlot: {alias: (factory, version, description)}}
        presets - {name: {ComponentSlot: alias}}
    """
    if isinstance(source, dict):
        data = source
    else:
        try:
            with open(source) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Cannot load manifest {source!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("A manifest must be a JSON object at the top level")

    slots = {}
    for slot_name, aliases in data.get("slots", {}).items():
        try:
            slot = ComponentSlot(slot_name)
        except ValueError:
            raise ManifestError(f"Unknown slot {slot_name!r} (expected one of: {', '.join(s.value for s in ComponentSlot)})")
        entries = {}
        for alias, spec in aliases.items():
            if not isinstance(spec, dict) or "factory" not in spec:
                raise ManifestError(f"Entry {slot_name}.{alias} must be an object with a 'factory'")
            try:
                factory = _resolve(spec["factory"])
            except ManifestError as exc:
                raise ManifestError(f"slot {slot_name}.{alias}: {exc}") from exc
            entries[alias] = (factory, str(spec.get("version", "1.0.0")), spec.get("description", ""))
        slots[slot] = entries

    presets = {}
    for name, mapping in data.get("presets", {}).items():
        slots_map = {}
        for slot_name, alias in mapping.items():
            try:
                slots_map[ComponentSlot(slot_name)] = str(alias)
            except ValueError:
                raise ManifestError(f"Preset {name!r} references unknown slot {slot_name!r}")
        presets[name] = slots_map

    return slots, presets


def apply_manifest(registry: ComponentRegistry, source) -> dict:
    """Load a manifest and register all slots + presets. Returns a summary."""
    slots, presets = load_manifest(source)
    for slot, entries in slots.items():
        for alias, (factory, version, description) in entries.items():
            registry.register(slot, alias, factory, version=version, description=description)
    for name, mapping in presets.items():
        registry.register_preset(name, mapping)
    return {
        "slots": {s.value: list(e.keys()) for s, e in slots.items()},
        "presets": list(presets),
    }

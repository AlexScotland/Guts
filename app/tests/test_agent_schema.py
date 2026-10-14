"""Contract tests for the agent-graph schema (v2, app/docs/agent-graph.md).

Pinned behaviors (the doc's pinned shape, red -> green):
  * exactly one ``loop``, which is exactly one of:
      - ``ref``   — a saved turn-graph id (transitional; resolution-checked
        via the injected known-ids seam);
      - ``alias`` — a built-in loop (closed enum);
      - ``graph`` — an INLINE graph document, validated with the same
        closed-vocabulary ``validate_graph`` contract (the stateless v2
        path);
    ambiguous (two or three set) or empty is rejected;
  * unknown top-level key (closed vocabulary, nothing routes silently);
  * ``mcp`` — alias uniqueness, closed transport enum, per-transport field
    pairing (stdio => command, sse/http => url; the other is a violation);
  * ``skill[]`` — each entry exactly one of ``text`` (inline persona
    fragment) XOR ``path`` (readable text file under the injected skill
    root), optional declared ``tools``;
  * ``settings`` keys are the six slot names;
  * ``model.provider`` is a closed enum (v1: ollama);
  * ``AgentError`` IS a ``ValueError`` — the save route maps it to a 400
    exactly like ``GraphError`` (a ``loop.graph`` violation is re-raised
    as AgentError, path-qualified ``loop.graph: …``).

No app runtime imports, no network, no real LLM: pure dict-in / error-out,
fakes only (the repo's frozen rule).

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/test_agent_schema.py -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from schemas.agent import AgentDoc, AgentError, parse_agent, validate_agent


def _valid(**overrides):
    """The doc's example document, always loop.ref-valid in shape mode."""
    doc = {
        "id": "researcher",
        "name": "Deep Researcher",
        "prompt": "You are a research specialist. Cite sources. Stay on the ask.",
        "model": {"provider": "ollama", "id": "qwen:latest"},
        "mcp": [
            {"alias": "web", "transport": "sse", "url": "http://searxng:8080/mcp"},
        ],
        "skill": [
            {"path": "citation.md"},
            {"path": "pplp.md", "tools": ["read_pdf_page", "get_pdf_info"]},
        ],
        "settings": {"memory": "trimmed"},
        "loop": {"ref": "killer-research"},
    }
    doc.update(overrides)
    return doc


# ── the green path ──────────────────────────────────────────────────────────


def test_valid_document_validates():
    doc = validate_agent(_valid())
    assert isinstance(doc, AgentDoc)
    assert doc.id == "researcher"
    assert doc.model.provider == "ollama"
    assert len(doc.mcp) == 1 and doc.mcp[0].transport == "sse"
    assert doc.mcp[0].url == "http://searxng:8080/mcp"
    assert doc.mcp[0].command is None
    assert [s.path for s in doc.skill] == ["citation.md", "pplp.md"]
    assert doc.skill[1].tools == ("read_pdf_page", "get_pdf_info")
    assert doc.settings == {"memory": "trimmed"}
    assert doc.loop.ref == "killer-research" and doc.loop.alias is None


def test_minimal_document_is_loop_only():
    doc = validate_agent({"loop": {"alias": "plan_execute"}})
    assert doc.id is None and doc.model is None
    assert doc.mcp == () and doc.skill == () and doc.settings == {}
    assert doc.loop.ref is None and doc.loop.alias == "plan_execute"


def test_loop_alias_direct_is_known():
    doc = validate_agent({"loop": {"alias": "direct"}})
    assert doc.loop.alias == "direct"


def test_agent_error_is_value_error():
    assert issubclass(AgentError, ValueError)
    with pytest.raises(ValueError):
        validate_agent({"loop": {}})


def test_parse_agent_from_json_string():
    doc = parse_agent(json.dumps(_valid()))
    assert doc.id == "researcher"


def test_parse_agent_from_file(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(_valid()), encoding="utf-8")
    doc = parse_agent(str(path))
    assert doc.loop.ref == "killer-research"


def test_parse_agent_rejects_bad_json():
    with pytest.raises(AgentError, match="invalid"):
        parse_agent("{not json")


# ── exactly one loop ────────────────────────────────────────────────────────


def test_missing_loop_rejected():
    doc = _valid()
    del doc["loop"]
    with pytest.raises(AgentError, match="no loop"):
        validate_agent(doc)


def test_loop_none_rejected():
    with pytest.raises(AgentError, match="no loop"):
        validate_agent(_valid(loop=None))


def test_loop_both_ref_and_alias_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(loop={"ref": "killer-research", "alias": "direct"}))


def test_loop_unknown_alias_rejected():
    with pytest.raises(AgentError, match="loop.alias"):
        validate_agent(_valid(loop={"alias": "plan-execute"}))  # shape, not an alias


def test_loop_graph_reached_by_ref_not_alias():
    with pytest.raises(AgentError, match="loop.alias"):
        validate_agent(_valid(loop={"alias": "graph"}))


def test_loop_unknown_key_rejected():
    with pytest.raises(AgentError, match="unknown key"):
        validate_agent(_valid(loop={"ref": "killer-research", "id": "x"}))


def test_loop_ref_resolution_injected():
    # Save-time seam: the store passes the known graph ids; unknown = 400.
    with pytest.raises(AgentError, match="not a saved graph"):
        validate_agent(_valid(loop={"ref": "ghost"}), known_graph_ids={"killer-research"})
    ok = validate_agent(
        _valid(loop={"ref": "killer-research"}), known_graph_ids={"killer-research"}
    )
    assert ok.loop.ref == "killer-research"
    # Pure mode (no store seam): shape-only check passes.
    ok = validate_agent(_valid(loop={"ref": "ghost"}))
    assert ok.loop.ref == "ghost"


def test_loop_ref_bad_id_shape_rejected():
    with pytest.raises(AgentError, match="loop.ref"):
        validate_agent(_valid(loop={"ref": "has space"}))


# ── loop.graph (the stateless v2 path) ───────────────────────────────────────


#: A minimal valid inline graph document (one entry, closed vocabulary) —
#: the same shape as the server's DEFAULT_LOOP_SEED.
MIN_GRAPH = {
    "name": "inline loop",
    "nodes": {"seed": {"primitive": "prompt", "config": {"text": "Set the context."}}},
    "edges": [],
}


def test_loop_graph_inline_validates():
    doc = validate_agent(_valid(loop={"graph": MIN_GRAPH}))
    assert doc.loop.ref is None and doc.loop.alias is None
    assert doc.loop.graph == MIN_GRAPH


def test_loop_graph_with_full_agent_document():
    doc = validate_agent(_valid(
        prompt="Answer like a 5th-grade explainer.",
        skill=[{"text": "Prefer small, verifiable steps."}],
        loop={"graph": MIN_GRAPH},
    ))
    assert doc.loop.graph == MIN_GRAPH
    assert doc.skill[0].text == "Prefer small, verifiable steps."


def test_loop_graph_invalid_document_rejected():
    # The same closed-vocabulary contract as a saved graph: a violation is
    # re-raised as AgentError, path-qualified through the loop edge.
    bad = {"nodes": {"x": {"primitive": "vibecoding"}}, "edges": []}
    with pytest.raises(AgentError, match="loop.graph.*unknown primitive"):
        validate_agent(_valid(loop={"graph": bad}))


def test_loop_graph_not_a_dict_rejected():
    with pytest.raises(AgentError, match="loop"):
        validate_agent(_valid(loop={"graph": ["nope"]}))


def test_loop_ref_and_graph_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(loop={"ref": "killer-research", "graph": MIN_GRAPH}))


def test_loop_alias_and_graph_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(loop={"alias": "direct", "graph": MIN_GRAPH}))


def test_loop_all_three_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(loop={"ref": "x", "alias": "direct", "graph": MIN_GRAPH}))


def test_loop_empty_object_rejected():
    with pytest.raises(AgentError, match="no loop"):
        validate_agent(_valid(loop={}))



# ── closed top-level vocabulary ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["agents", "toolset", "persona", "routes", "extra"])
def test_unknown_top_level_key_rejected(key):
    with pytest.raises(AgentError, match="unknown key"):
        validate_agent(_valid(**{key: "x"}))


def test_empty_prompt_rejected():
    with pytest.raises(AgentError, match="prompt"):
        validate_agent(_valid(prompt="   "))


# ── mcp entries ─────────────────────────────────────────────────────────────


def test_mcp_alias_collision_rejected():
    with pytest.raises(AgentError, match="unique"):
        validate_agent(_valid(mcp=[
            {"alias": "web", "transport": "sse", "url": "http://a"},
            {"alias": "web", "transport": "stdio", "command": ["python", "-m", "x"]},
        ]))


def test_mcp_transport_closed_enum():
    with pytest.raises(AgentError, match="transport"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "grpc", "url": "http://a"}]))


def test_mcp_stdio_with_url_rejected():
    with pytest.raises(AgentError, match="not 'url'"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "stdio", "url": "http://a"}]))


def test_mcp_stdio_requires_command():
    with pytest.raises(AgentError, match="command"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "stdio"}]))
    with pytest.raises(AgentError, match="command"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "stdio", "command": [""]}]))


def test_mcp_url_transport_with_command_rejected():
    with pytest.raises(AgentError, match="not 'command'"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "sse", "command": ["x"]}]))


def test_mcp_url_transport_requires_url():
    with pytest.raises(AgentError, match="url"):
        validate_agent(_valid(mcp=[{"alias": "w", "transport": "http"}]))


def test_mcp_headers_must_be_string_map():
    with pytest.raises(AgentError, match="headers"):
        validate_agent(_valid(mcp=[
            {"alias": "w", "transport": "sse", "url": "http://a", "headers": {"k": 1}},
        ]))
    ok = validate_agent(_valid(mcp=[
        {"alias": "w", "transport": "sse", "url": "http://a", "headers": {"Authorization": "Bearer t"}},
    ]))
    assert ok.mcp[0].headers == {"Authorization": "Bearer t"}


def test_mcp_entry_non_object_rejected():
    with pytest.raises(AgentError, match="JSON object"):
        validate_agent(_valid(mcp=["web"]))


# ── skill entries ───────────────────────────────────────────────────────────


def test_skill_path_missing_rejected(tmp_path):
    with pytest.raises(AgentError, match="no such file"):
        validate_agent(_valid(skill=[{"path": "citation.md"}]), skill_root=str(tmp_path))


def test_skill_path_escapes_root_rejected(tmp_path):
    with pytest.raises(AgentError, match="stay under"):
        validate_agent(
            _valid(skill=[{"path": "../outside.md"}]),
            skill_root=str(tmp_path / "skills"),
        )


def test_skill_path_must_be_readable_text(tmp_path):
    blob = tmp_path / "binary.md"
    blob.write_bytes(b"\x00\x01\xff\xfe")
    with pytest.raises(AgentError, match="text file"):
        validate_agent(_valid(skill=[{"path": "binary.md"}]), skill_root=str(tmp_path))
    good = tmp_path / "citation.md"
    good.write_text("# Cite your sources\n", encoding="utf-8")
    ok = validate_agent(_valid(skill=[{"path": "citation.md"}]), skill_root=str(tmp_path))
    assert ok.skill[0].path == "citation.md"


def test_skill_tools_shape():
    with pytest.raises(AgentError, match="tools"):
        validate_agent(_valid(skill=[{"path": "a.md", "tools": [""]}]))
    ok = validate_agent(_valid(skill=[{"path": "a.md", "tools": ["read_pdf_page"]}]))
    assert ok.skill[0].tools == ("read_pdf_page",)


def test_skill_entry_unknown_key_rejected():
    with pytest.raises(AgentError, match="unknown key"):
        validate_agent(_valid(skill=[{"path": "a.md", "run": "always"}]))


# ── skill entries: text (inline) XOR path ───────────────────────────────────


def test_skill_text_inline_fragment_validates():
    ok = validate_agent(_valid(skill=[{"text": "Prefer small, verifiable steps."}]))
    assert ok.skill[0].text == "Prefer small, verifiable steps."
    assert ok.skill[0].path is None and ok.skill[0].tools is None


def test_skill_text_with_declared_tools():
    ok = validate_agent(_valid(skill=[{"text": "Cite sources.", "tools": ["web_search"]}]))
    assert ok.skill[0].text == "Cite sources."
    assert ok.skill[0].tools == ("web_search",)


def test_skill_text_and_path_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(skill=[{"text": "inline", "path": "a.md"}]))


def test_skill_neither_text_nor_path_rejected():
    with pytest.raises(AgentError, match="exactly one"):
        validate_agent(_valid(skill=[{"tools": ["web_search"]}]))


def test_skill_empty_text_rejected():
    with pytest.raises(AgentError, match="skill\\[0\\].text"):
        validate_agent(_valid(skill=[{"text": "   "}]))


def test_mixed_text_and_path_entries_preserve_order():
    ok = validate_agent(_valid(skill=[
        {"text": "First fragment."},
        {"path": "citation.md"},
        {"text": "Second fragment."},
    ]))
    assert [s.text or s.path for s in ok.skill] == [
        "First fragment.", "citation.md", "Second fragment.",
    ]


# ── settings ────────────────────────────────────────────────────────────────


def test_settings_unknown_slot_rejected():
    with pytest.raises(AgentError, match="unknown slot"):
        validate_agent(_valid(settings={"memory": "trimmed", "router": "x"}))


def test_settings_values_must_be_nonempty():
    with pytest.raises(AgentError, match="settings.planner"):
        validate_agent(_valid(settings={"planner": ""}))


def test_settings_all_six_slots_accepted():
    ok = validate_agent(_valid(settings={
        "goal_resolver": "passthrough",
        "planner": "trivial",
        "executor": "echo",
        "evaluator": "always_complete",
        "memory": "trimmed",
        "loop": "direct",
    }))
    assert ok.settings["memory"] == "trimmed"


# ── model ───────────────────────────────────────────────────────────────────


def test_model_provider_closed_enum():
    with pytest.raises(AgentError, match="model.provider"):
        validate_agent(_valid(model={"provider": "openai", "id": "gpt"}))


def test_model_id_nonempty():
    with pytest.raises(AgentError, match="model.id"):
        validate_agent(_valid(model={"provider": "ollama"}))


def test_model_params_optional_object():
    ok = validate_agent(
        _valid(model={"provider": "ollama", "id": "qwen:latest", "params": {"temperature": 0}})
    )
    assert ok.model.params == {"temperature": 0}
    with pytest.raises(AgentError, match="params"):
        validate_agent(_valid(model={"provider": "ollama", "id": "x", "params": [1]}))


def test_model_unknown_key_rejected():
    with pytest.raises(AgentError, match="unknown key"):
        validate_agent(_valid(model={"provider": "ollama", "id": "x", "base_url": "http://a"}))


# ── identity ────────────────────────────────────────────────────────────────


def test_id_bad_shape_rejected():
    with pytest.raises(AgentError, match=r"^\bid:"):
        validate_agent(_valid(id="not an id"))


def test_top_level_non_object_rejected():
    with pytest.raises(AgentError, match="JSON object"):
        validate_agent(["loop"])
    with pytest.raises(AgentError, match="JSON object"):
        validate_agent(None)

"""Few-shot examples for the goal extractor.

Pure data - no logic. Add, edit, or remove examples here to steer extraction
quality (e.g. if a model mangles multiple-choice options, add a sharp example)
without touching the extractor. Each entry is ``{"prompt": str, "goal": dict}``.

Keep the examples small and mutually disjoint - they anchor the shape of a
``Goal``, not its vocabulary. ``output_spec`` is the open tail: show the model
it belongs in the prompt verbatim for question/options/keywords/sections.
"""
from __future__ import annotations

EXAMPLES: list[dict] = [
    # Multiple choice - options captured verbatim in output_spec
    {
        "prompt": (
            "Choose exactly one answer. What is 2+2? "
            "Options: - 3\n- 4\n- 5"
        ),
        "goal": {
            "intent": "Answer a multiple-choice arithmetic question.",
            "requirements": ["Pick exactly one option"],
            "constraints": ["Choose strictly from the provided options"],
            "suggested_sources": None,
            "output_spec": {"answer_options": ["3", "4", "5"]},
        },
    },
    # Generation - structured tail holds sections + keywords verbatim
    {
        "prompt": (
            "Write a Markdown product announcement for 'ApexCloud' targeting "
            "enterprise CTOs. Include an 'Architecture Overview' section and a "
            "'Pricing Tiers' section, and explicitly use the terms "
            "'SOC2 Compliant' and 'Zero-Trust Infrastructure'."
        ),
        "goal": {
            "intent": "Draft a professional ApexCloud product announcement.",
            "requirements": [
                "Produce a Markdown announcement for ApexCloud",
                "Include an Architecture Overview section",
                "Include a Pricing Tiers section",
            ],
            "constraints": ["Tone: professional, enterprise-focused"],
            "suggested_sources": None,
            "output_spec": {
                "target_format": "Markdown",
                "required_keywords": ["SOC2 Compliant", "Zero-Trust Infrastructure"],
            },
        },
    },
    # Summarization with sources - no structured tail needed
    {
        "prompt": (
            "Based on the files inside memory/pdfs/cmlto/, explain the circle "
            "of care. Keep it under 3 paragraphs, use bullets, professional tone."
        ),
        "goal": {
            "intent": "Summarize the circle-of-care standard from the provided PDFs.",
            "requirements": [
                "Explain what the circle of care is",
                "Ground the explanation in the provided documentation",
            ],
            "constraints": [
                "Under 3 paragraphs",
                "Use a bulleted format",
                "Maintain a professional tone",
            ],
            "suggested_sources": ["memory/pdfs/cmlto/"],
            "output_spec": None,
        },
    },
    # Plain open question - requirements only, no spec, no sources
    {
        "prompt": "What are the trade-offs between a monolith and microservices?",
        "goal": {
            "intent": "Explain the trade-offs between a monolith and microservices.",
            "requirements": [
                "State the main advantages of each",
                "State the main disadvantages of each",
            ],
            "constraints": [],
            "suggested_sources": None,
            "output_spec": None,
        },
    },
]
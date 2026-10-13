"""The single goal object for the whole harness.

One flexible schema replaces the seven typed goal schemas
(multiple-choice, open-question, summarization, generation, code-analysis,
general). The closed/core fields are the reliable ones; the open tail
(``requirements`` list + ``output_spec`` dict) is where anything novel lives,
so a new kind of goal is a better example in ``examples.py`` — NOT a new
schema + extractor + factory registration.
"""
from typing import Any, List, Optional

from pydantic import BaseModel, Field, FieldValidationInfo, field_validator

# non-optional list fields: must resolve to a list, never None
_REQUIRED_LISTS = ("requirements", "constraints")


class Goal(BaseModel):
    intent: str = Field(
        description="The high-level action/goal of the prompt (one sentence)."
    )
    requirements: List[str] = Field(
        default_factory=list,
        description=(
            "EVERY concrete requirement / deliverable the prompt asks for, "
            "one item each, independently verifiable. This is the main field "
            "and the open dimension - capture anything with no dedicated field here."
        ),
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="Rules, length limits, tone, style, format, or structural criteria given in the prompt."
    )
    suggested_sources: Optional[List[str]] = Field(
        default=None,
        description="Explicit file paths or documents the prompt names that the agent should look at. None if absent."
    )
    output_spec: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional structured tail for prompt-specific structure that belongs "
            "verbatim: e.g. answer_options, required_keywords, mandatory_sections, "
            "target_format. Omit when the prompt has no such structure."
        ),
    )

    # ------------------------------------------------------------------ #
    # Robustness: models sometimes emit a string where a list is asked. #
    # Coerce at the model boundary so the loop can always consume it.    #
    # ------------------------------------------------------------------ #
    @field_validator("requirements", "constraints", "suggested_sources", mode="before")
    @classmethod
    def _coerce_to_list(cls, value, info: FieldValidationInfo):
        # Non-optional list fields must resolve to a list, never None.
        fallback = [] if info.field_name in _REQUIRED_LISTS else None

        if value is None:
            return fallback
        if isinstance(value, str):
            if not value.strip() or value.strip() == "None":
                return fallback
            parts = [
                p.strip()
                for p in value.replace(";", "\n").split("\n")
                if p.strip()
            ]
            return parts
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        return value

    @field_validator("output_spec", mode="before")
    @classmethod
    def _empty_spec_to_none(cls, value):
        if value is None:
            return None
        if isinstance(value, dict) and not value:
            return None
        return value

    @classmethod
    def from_raw(cls, data: Any) -> "Goal":
        """Coerce whatever the model emitted into a valid Goal.

        `with_structured_output(Goal)` already returns a Goal instance, so the
        common path is a no-op. The other two ways a malformed result shows up:
        a bare string (model dumped prose) or a plain dict. Anything else raises.
        """
        if isinstance(data, cls):
            return data
        if isinstance(data, dict):
            return cls(**data)
        if isinstance(data, str):
            return cls(intent=data.strip())
        raise TypeError(f"Cannot build Goal from {type(data)!r}")

    # ------------------------------------------------------------------ #
    # Banner (the string the loop is actually steered by)               #
    # ------------------------------------------------------------------ #
    @property
    def text(self) -> str:
        requirements_block = (
            "\n".join(f"  * {r}" for r in self.requirements)
            if self.requirements
            else "  * (none specified)"
        )
        constraints_block = (
            "\n".join(f"  * {c}" for c in self.constraints)
            if self.constraints
            else "  * None specified"
        )
        sources_block = (
            "\n".join(f"  * {s}" for s in self.suggested_sources)
            if self.suggested_sources
            else "  * None specified"
        )
        spec_block = self._render_spec().strip("\n")

        sections = "\n".join([
            f"        - INTENT: {self.intent}",
            "",
            "        MANDATORY REQUIREMENTS (each must be addressed):",
            requirements_block,
            "",
            "        CRITICAL CONSTRAINTS:",
            constraints_block,
            "",
            "        TARGET REFERENCE SOURCES:",
            sources_block,
        ])
        if spec_block:
            sections += "\n\n" + spec_block

        return f"""
        =========================================
        {sections}
        EXECUTION FRAMEWORK DIRECTIONS:
        Use your tool inventory to gather any context you need.
        Your final response MUST explicitly satisfy every MANDATORY REQUIREMENT
        and every CRITICAL CONSTRAINT listed above.
        =========================================
        """

    def _render_spec(self) -> str:
        """Render the open ``output_spec`` tail generically.

        Each key becomes a sub-section: list values become bullets, plain
        values inline. Empty/None renders nothing (no dead section).
        """
        if not self.output_spec:
            return ""
        lines = ["        STRUCTURED FIELDS (from the prompt):"]
        for key, value in self.output_spec.items():
            title = str(key).replace("_", " ").strip()
            title = title.title() if title else str(key)
            if isinstance(value, (list, tuple)):
                lines.append(f"          {title}:")
                lines.extend(f"            - {item}" for item in value)
            elif isinstance(value, dict):
                lines.append(f"          {title}:")
                for inner_key, inner_val in value.items():
                    lines.append(f"            {inner_key}: {inner_val}")
            else:
                lines.append(f"          {title}: {value}")
        return "\n".join(lines)
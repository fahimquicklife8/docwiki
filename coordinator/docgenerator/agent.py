"""Documentation sub-agent — doc_generator.

Receives an active application slug from session state and generates
structured Markdown documentation from graph + source evidence.

This is a stub scaffold. Full implementation in Phase 5.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent

from coordinator import config
from coordinator.tools.documentation_tools import (
    finalize_documentation,
    list_documentation_modules,
    load_documentation_context,
    load_overview_context,
    save_module_document,
    save_overview_document, #saves final document
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "documentation_system.md"
_INSTRUCTION = (
    _PROMPT_PATH.read_text(encoding="utf-8")
    if _PROMPT_PATH.exists()
    else "You are DocWiki's documentation generator. Use supplied tools only."
)

doc_generator = LlmAgent(
    name="doc_generator",
    model=config.DOCUMENTATION_MODEL,
    instruction=_INSTRUCTION,
    description=(
        "Generates structured Markdown documentation for an onboarded repository "
        "using graph analysis results and exact source evidence."
    ),
    tools=[
        list_documentation_modules,
        load_documentation_context,
        save_module_document,
        load_overview_context,
        save_overview_document,
        finalize_documentation,
    ],
)
